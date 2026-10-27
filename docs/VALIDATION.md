# Local validation and outstanding work

## Current accepted CORS decision

Wildcard CORS is accepted by the owner for this internal deployment. Production
uses same-origin /api/v1 through nginx. CORS_ORIGINS is unused by the application
CORS middleware. app/main.py retains allow_origins=["*"] without an added exception
handler. The current tests/test_cors.py is preserved unchanged in the backend
baseline; its original cannot be recovered. These tests do not match the wildcard
setup and need the developer's decision. CI excludes them from blocking tests and
runs them separately as a non-blocking known issue. No application fix branch is
planned. Earlier validation results below are historical, not a new passing run.

Application code retains the original wildcard CORS setup.

- Python development, CI and deployment target 3.10.
- At initial setup the backend/.venv interpreter was missing. It is now available as Python 3.10.14 with pytest 8.3.4; CI now targets Python 3.10. Ruff, Black, Bandit, audit and migration checks have not been rerun.
- requirements-dev.txt declares the approved tools; they are installed by CI.
- No production database was opened or modified.
- Ruff/Black findings must be reviewed before any application source edits.

## CORS test collection repair

- Original failure: tests/test_cors.py imported nonexistent cors_error_headers from
  app.core.middleware. The test predates CI/CD work; no equivalent helper exists.
  The approved app.main origin-list change did not cause this missing import.
- Approved option B implemented: remove the stale helper and synthetic app/error
  handler. Test the actual application's CORS stack on /health, invalid login (422),
  an unconfigured origin, and an injected unexpected endpoint exception (500).
- Older tests install a partial security module stub during collection. The CORS
  fixture imports the actual application lazily and temporarily loads real security
  when needed, restoring the prior sys.modules entry through monkeypatch.
- All 42 tests now collect successfully (39 existing tests plus 3 CORS tests).
- Runtime attempt stalled in TestClient/AnyIO and was stopped after a 20-second
  timeout. Runtime CORS behavior is therefore NOT verified in this environment.
- The 500-header assertion remains active; no skip or xfail was added. It may expose
  a production error-response CORS gap. Confirm by running the focused tests in a
  working test environment before proposing an application middleware change.
- Tests avoid application lifespan, override the DB dependency, and were invoked
  with an in-memory DATABASE_URL, demo data disabled and scheduler disabled.
  No server or production database was accessed. No application files changed.

Recheck from backend/ in a working environment:

```bash
DATABASE_URL=sqlite:// BOOTSTRAP_DEMO_DATA=false ENABLE_INTERNAL_SCHEDULER=false CORS_ORIGINS=http://localhost:5173 .venv/bin/python -m pytest tests/test_cors.py -q
```

## User-machine runtime result

- User reports 41 passed and 1 failed: the HTTP 500 CORS-header test.
- The missing-import collection problem is resolved.
- Local sandbox execution timed out; the user-machine run did not hang.
- A minimal global exception handler is proposed to return generic JSON 500
  responses with CORS headers only for explicitly allowed origins.
- Post-fix verification is pending; 42 passing tests are not yet confirmed.

## Current CI/CD scope

No separate application changes or CORS fix branch are planned. See FOR_DEVELOPER.md for temporary CI exceptions. Earlier
entries above record prior attempts, not application changes in the CI/CD PR.

## Deployment setup update

Coverage provider @vitest/coverage-v8@2.1.9 is now installed and committed separately.
The owner reports 7 npm audit vulnerabilities, including 3 critical; frontend audit
is a non-blocking known issue. No automatic audit fixes were run. Both repositories
have separate release workflows and runners; backend must release before frontend.
Deployment files are prepared locally, not installed or runtime-validated on server.
Scheduler settings and first-admin creation require developer confirmation.

Release update: direct server connectivity works; proxy settings are optional.
Only manual dispatch and develop/main pushes can trigger the guarded release jobs.
Activation failures attempt automatic previous-release rollback; SQLite is not
automatically restored/downgraded. Runtime installation remains pending.

## Repository owner update

The owner is credentekdev-art; repository name remains hrms-backend.
The local origin already points to https://github.com/credentekdev-art/hrms-backend.git.
The owner reports baseline main/develop branches pushed to this repository.
Both release-job repository guards now use credentekdev-art/hrms-backend.
CODEOWNERS reviewer confirmation is required separately. Feature changes have not
been pushed by this assistant; no server commands were run for this update.
