"""Run only via the approved Release workflow as hrmsdeploy, on the server."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def check_health(port):
    # Explicitly bypass proxies; no_proxy CIDR is not portable across clients.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(30):
        try:
            with opener.open(f"http://localhost:{port}/health", timeout=3) as response:
                if response.status == 200 and json.load(response).get("status") == "ok":
                    return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("Backend health check failed; frontend deployment must stop")


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value) + "\n")
    temporary.replace(path)


def previous_release(current):
    if not current.is_symlink():
        if current.exists():
            raise RuntimeError("current must be a release symlink")
        return None
    previous = current.resolve(strict=True)
    assert previous.parent == (current.parent / "releases").resolve()
    return previous


def activate(release, current):
    temporary = current.with_name("current.next")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError("Unresolved current.next; inspect previous deployment")
    temporary.symlink_to(release)
    temporary.replace(current)


target = os.environ["TARGET"]
sha = os.environ["SOURCE_SHA"]
run_id = os.environ["CI_RUN_ID"]
assert target in {"staging", "production"}
assert re.fullmatch(r"[a-f0-9]{40}", sha)
assert run_id.isdigit()
assert __import__("pwd").getpwuid(os.getuid()).pw_name == "hrmsdeploy"
root = Path("/srv/hrms") / target
shared = root / "shared"
# Both repository runners take the same lock for this environment.
lock = (shared / "deployment.lock").open("a")
fcntl.flock(lock, fcntl.LOCK_EX)
port = 8001 if target == "staging" else 8002
artifact = Path(os.environ["ARTIFACT_DIR"])
release_id = f"{sha}-{run_id}-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
record = {"sha": sha, "ci_run_id": run_id, "release_id": release_id}

import tarfile

release = root / "backend" / "releases" / release_id
release.mkdir()  # Never overwrite an existing release.
with tarfile.open(artifact / "backend-source.tar.gz", "r:gz") as archive:
    for member in archive.getmembers():
        destination = (release / member.name).resolve()
        assert release.resolve() in destination.parents or destination == release.resolve()
        assert member.isfile() or member.isdir(), "Links/devices are not deployable"
    archive.extractall(release)
python = "/home/credentek/.pyenv/versions/3.10.14/bin/python"
run(python, "-m", "venv", str(release / ".venv"))
venv_python = str(release / ".venv/bin/python")
run(venv_python, "-m", "pip", "install", "-r", str(release / "requirements.txt"))
# Use the application's dotenv parser; never source configuration as shell code.
# Subprocess output contains no secret values.
configuration = shared / "backend.env"
assert configuration.is_file()
assert configuration.stat().st_mode & 0o077 == 0, "backend.env must be mode 0600"
prepare = r'''
import os, sqlite3, subprocess, sys
from pathlib import Path
from dotenv import dotenv_values
config = dotenv_values(sys.argv[1])
for key in ("SECRET_KEY", "DATABASE_URL", "BOOTSTRAP_DEMO_DATA", "ENABLE_INTERNAL_SCHEDULER", "AUTO_DEALLOCATE_HOUR"):
    assert config.get(key), "Missing required server configuration: " + key
assert len(config["SECRET_KEY"]) >= 32
assert config["BOOTSTRAP_DEMO_DATA"].lower() == "false"
assert config["ENABLE_INTERNAL_SCHEDULER"].lower() in {"true", "false"}
assert 0 <= int(config["AUTO_DEALLOCATE_HOUR"]) <= 23
expected = "sqlite:////srv/hrms/" + sys.argv[2] + "/shared/data/hrms.sqlite3"
assert config["DATABASE_URL"] == expected, "Confirm SQLite path before deploying"
source = Path("/srv/hrms") / sys.argv[2] / "shared/data/hrms.sqlite3"
# Do not create or partially migrate a fresh server DB without developer approval.
if not source.is_file():
    raise SystemExit("Fresh database requires approved initialisation; see docs/FOR_DEVELOPER.md")
with sqlite3.connect("file:" + str(source) + "?mode=ro", uri=True) as database:
    tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
required_tables = {"allocations", "users", "employees", "projects", "resource_requests", "feedback_tasks"}
if not required_tables.issubset(tables):
    raise SystemExit("Incomplete base schema: developer-approved initialisation is required before deployment")
if source.exists():
    destination = source.parent.parent / "backups" / (sys.argv[3] + ".sqlite3")
    assert not destination.exists()
    with sqlite3.connect("file:" + str(source) + "?mode=ro", uri=True) as db:
        with sqlite3.connect(destination) as backup:
            db.backup(backup)
environment = dict(os.environ)
environment.update({k: v for k, v in config.items() if v is not None})
subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=environment, check=True)
'''
current = root / "backend/current"
previous = previous_release(current)
record_path = shared / "backend-release.json"
old_record = json.loads(record_path.read_text()) if record_path.exists() else None
# Invalidate success before migrations too: partial migrations must block frontend.
atomic_json(record_path, {"status": "deploying", **record})
log_path = shared / (release_id + ".migration.log")
# Migration output may contain application configuration. Keep it server-local.
log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(log_fd, "w") as log:
        run(venv_python, "-c", prepare, str(configuration), target, release_id,
            cwd=release, stdout=log, stderr=subprocess.STDOUT)
except Exception:
    atomic_json(record_path, {"status": "migration_failed", **record})
    raise RuntimeError("Migration failed; current release unchanged. Inspect private server log and database state.") from None
try:
    activate(release, current)
    run("sudo", "-n", "/usr/bin/systemctl", "restart", f"hrms-backend-{target}.service")
    check_health(port)
except Exception:
    atomic_json(record_path, {"status": "activation_failed", **record})
    if previous is None:
        raise RuntimeError("First deployment failed; no previous release exists for rollback") from None
    try:
        activate(previous, current)
        run("sudo", "-n", "/usr/bin/systemctl", "restart", f"hrms-backend-{target}.service")
        check_health(port)
        restored = old_record or {"release_id": previous.name}
        atomic_json(record_path, {**restored, "status": "healthy", "rolled_back_from": release_id})
    except Exception:
        atomic_json(record_path, {"status": "rollback_failed", **record})
        raise RuntimeError("Automatic code rollback failed; operator intervention required") from None
    raise RuntimeError("Deployment failed; previous backend release restored and health checked. Database was not downgraded.") from None
atomic_json(record_path, {"status": "healthy", **record})
print("Backend release healthy:", release_id)
