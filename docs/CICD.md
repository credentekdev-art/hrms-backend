# HRMS CI/CD and server deployment

## Approved topology

Server: 192.168.0.188, Ubuntu 20.04, nginx 1.18. System Python remains 3.8.
Python 3.10.14: /home/credentek/.pyenv/versions/3.10.14/bin/python.
The administrator must confirm hrmsdeploy can traverse this path and execute it,
including its shared libraries, before enabling deployment. Do not change system Python.

| Environment | nginx | Backend | Service |
|---|---|---|---|
| staging | HTTP :8080 | 127.0.0.1:8001 | hrms-backend-staging.service |
| production | HTTP :80 | 127.0.0.1:8002 | hrms-backend-production.service |

nginx serves /srv/hrms/<environment>/frontend/current with SPA fallback and
preserves /api/v1 when proxying. The administrator installs the frontend repository's
nginx configs and replaces the default port-80 page after validating nginx config.
Ports must be checked before installation. nginx configuration and service units
are root-owned; deployment jobs cannot edit them.

hrmsdeploy owns /srv/hrms and runs both repository runners and both backend services.
It has no general sudo. The backend deploy/sudoers/hrmsdeploy file permits only
restarting the two exact backend services and reloading nginx. Status is permitted only with --no-pager for those two services; nginx -t is also
permitted. No wildcard service arguments, shells, daemon-reload or file installation
are permitted. An administrator
installs units/configuration/sudoers, validates with visudo and nginx -t, and manages
runner service installation. nginx needs traversal permission on static directory
ancestors; shared directories remain private. Give hrmsdeploy no membership granting
general sudo. Both runners share one identity and therefore trust boundary.

## Separate repository releases

Each repository has its own Release workflow (manual dispatch or push to develop/main only) and downloads only its own
successful push CI artifact with its repository-scoped GITHUB_TOKEN. No cross-repo
token is needed. CI stays on GitHub-hosted runners. No PR jobs run on deployment runners.

1. Release backend first. Push-triggered releases wait for the exact push SHA's successful CI on a hosted runner. Select staging/develop or production/main and its CI run ID.
2. Confirm the backend release succeeds and record the backend source SHA.
3. Release frontend in the matching environment with its own CI run ID and that backend SHA.
   For frontend push releases, set the environment variable BACKEND_RELEASE_SHA to
   the approved, successfully deployed backend SHA before pushing. A missing or
   mismatched value fails deployment. No cross-repository automatic trigger exists.

The workflows validate repository, event, workflow path, successful conclusion,
branch, SHA, artifact name and expiry. Never rebuild frontend during deployment.
The frontend workflow checks the local backend success record and health under the
same server-side environment lock used by backend deployment. The operator owns
version compatibility and must not start another backend release between selecting
versions and completing frontend release.

In BOTH repos: Settings > Environments > create staging and production. Restrict
staging to develop and production to main. Require production reviewers and prevent
self-review where supported. Leave environment variable DEPLOYMENT_READY unset until
server setup, backups, startup/migration behavior, scheduler and admin creation are
confirmed. Then set it to true per environment. If required production approval is
unavailable, leave production disabled. Protect main/develop with PR review and CI.
Release workflows must be on the default branch before workflow_dispatch is available.
Local changes have not been pushed; nothing here is installed or enabled on the server.

## Runner registration: repeat for each repository

Repository > Settings > Actions > Runners > New self-hosted runner > Linux > verified
architecture. Use GitHub's current download and checksum instructions. Register as
hrmsdeploy, never root. Use separate directories under /home/hrmsdeploy:

| Repository | Directory | Runner name |
|---|---|---|
| hrms-backend | runners/backend | hrms-backend-188 |
| hrms-frontend | runners/frontend | hrms-frontend-188 |

Keep default self-hosted, linux and architecture labels; add hrms-deploy. Keep
registration tokens private. The administrator installs each runner service to run
as hrmsdeploy. Confirm both runners show Idle in their own repository. The runner
requires git, gh, tar, Python 3.10 and outbound HTTPS through the proxy. Install
these prerequisites before enabling releases. Restrict runner use to reviewed release
workflows; protect workflow and deploy-script changes through CODEOWNERS/review.

## Proxy

Direct GitHub connectivity has been confirmed; no proxy is configured in the
server environment, apt or /etc/environment. Do not require proxy setup.
deploy/runner-proxy.env.example is optional documentation only. Use it only if
runner connectivity fails and testing confirms the proxy is needed. The workflows
do not set proxy variables. Preserve direct connectivity by default.

If a proxy becomes necessary, merge the documented values into each runner's local
.env without overwriting runner configuration, then have an administrator restart
the runner services. GitHub's runner does not support IP/CIDR no_proxy entries;
local health checks explicitly bypass proxies. Confirm shell settings with printenv
http_proxy https_proxy no_proxy and test actual runner connectivity separately.
Proxy reference: https://docs.github.com/en/actions/how-tos/manage-runners/use-proxy-servers

## Server-only environment and SQLite

Each environment uses /srv/hrms/<environment>/shared/backend.env, owned by hrmsdeploy,
mode 0600. Generate a separate random SECRET_KEY on the server only; never put it
in Git, workflow inputs, logs or artifacts. The repository contains no backend.env
or secret-value template. Use DATABASE_URL=sqlite:////srv/hrms/<environment>/shared/data/hrms.sqlite3.
BOOTSTRAP_DEMO_DATA=false is the approved deployment setting. The developer must
confirm ENABLE_INTERNAL_SCHEDULER, AUTO_DEALLOCATE_HOUR and initial admin creation
before deployment. No deployment default forces the scheduler off. CI independently
turns it off for isolated tests. The service loads backend.env; do not echo it.

Each backend release gets its own .venv at its final release path, built with the
explicit Python 3.10 interpreter. Release scripts require explicit scheduler values,
back up existing SQLite through the SQLite backup API, run Alembic, switch current,
restart systemd and check health. A fresh database has no backup until it exists.
Startup still performs create_all/local schema upgrades: developer confirmation and
a rehearsal on a database copy are required before DEPLOYMENT_READY is enabled.
The service uses one worker. Determine server timezone and scheduled-job behavior
with the developer. Do not assume migrations or schema changes are reversible.

## Failure and rollback

Scripts retain existing files, releases and backups; they never delete old releases
or overwrite an existing release directory. Retention cleanup is a separate future
action. After switching releases, restart/reload or health-check failures automatically
restore the previous release symlink, restart/reload, and check it again. The workflow
still fails so the operator sees the failed release. On first deployment there is
no previous release; failures require intervention. Migration failures leave current
unchanged and invalidate the success record, because partial schema changes may
need intervention. No database downgrade/restore is automatic. Code rollback assumes
backward-compatible migrations; confirm this before enabling deployment.
Migration output is captured in mode-0600 server-local logs, not Actions logs.
Scripts never print SECRET_KEY or backend.env. A failed backend deployment prevents
frontend deployment via the local success record unless a previous healthy release
is restored and its SHA matches the selected backend version. Inspect logs and database state before retrying. No automatic database downgrade
or restore is performed. Retained SQLite backups need a tested restore procedure.

For a compatible rollback, the operator selects a retained release, switches current
atomically and restarts the exact service (or reloads nginx for frontend), then checks
health. Backend code rollback requires compatibility with the migrated database.
Database restoration requires a deliberate decision because newer writes may be lost.

## Known issues

See FOR_DEVELOPER.md and VALIDATION.md. CI has explicit temporary continue-on-error
steps; a successful run does not mean those issues are resolved. Frontend audit is
non-blocking at the owner's request; do not run npm audit fix. Other security checks,
builds, migrations and non-excepted tests remain blocking. No application changes are
part of these deployment files. Wildcard CORS is accepted for this internal deployment;
production uses same-origin /api/v1 and CORS_ORIGINS is unused by application middleware.

## Trigger and repository restrictions

Both Release workflows accept only workflow_dispatch and push on develop/main.
Hosted validation and self-hosted deployment each check the exact original repository,
reject repository forks, and require refs/heads/develop or refs/heads/main. No
pull_request, pull_request_target or workflow_run release trigger exists. Checkout
uses the trusted release-workflow ref, not a PR ref or unvalidated artifact SHA.
Environment branch restrictions and reviewed workflow changes remain required.
Protect all workflows: an independently added workflow could otherwise target the
same runner labels. A successful CI run includes the documented known-issue exceptions.
