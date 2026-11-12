# Watchtower

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

**English** · [Русский](README.ru.md)

Self-hosted uptime monitoring for websites and APIs. It checks endpoints on a schedule, tracks
incidents, computes uptime and latency, watches TLS certificate expiry, and sends alerts to Telegram
or a webhook.

**Live demo:** [public status page](https://watchtower-yd4d.onrender.com/status) (free tier, the
first load can take up to a minute).

**The problem it solves.** A freelancer or a small business needs to learn that a site is down
before a customer does. Paid services are overkill for that, and a cron script spams, loses
notifications, and keeps no history. Watchtower starts with one command, needs no external
dependencies by default (SQLite), and stays quiet unless something actually changed.

![Dashboard](docs/dashboard.png)

Public status page for customers (`/status`), with no URLs or error details exposed:

![Status page](docs/status.png)

## Quick start

```bash
docker compose up -d
# dashboard:    http://localhost:8000
# status page:  http://localhost:8000/status
# API docs:     http://localhost:8000/docs
```

Configuration lives in `.env` (see [.env.example](.env.example)): Telegram, webhook, API key,
Postgres.

Free cloud deployment (Render + Supabase) is described in [docs/DEPLOY.md](docs/DEPLOY.md)
(in Russian).

To see the UI with 30 days of ready-made history, without Docker:

```bash
pip install -e ".[dev]"
export WT_DATABASE_URL=sqlite+aiosqlite:///./demo.db
python scripts/seed_demo.py
WT_SCHEDULER_ENABLED=false uvicorn app.main:app
```

## Features

- **Checks:** response status, a total deadline, and a keyword in the body (a 200 with a
  "maintenance" page is still an outage).
- **Failure threshold:** a monitor goes down only after N failures in a row, so a single network
  blip does not wake anyone up.
- **Alerts on state changes only:** one message when a site goes down and one when it recovers,
  with the downtime.
- **TLS:** the certificate expiry is read from the same connection; one warning per certificate,
  14 days ahead.
- **Reliable delivery:** alerts are queued in the database and retried with backoff until the
  channel answers. Stale alerts are dropped: "site is down" never arrives after it is back up.
- **Statistics:** uptime for 24 h / 7 d / 30 d, average and p95 latency, an incident log and an
  alert delivery log.
- **Public status page**, a dashboard with no build step and no dependencies (add, edit, pause),
  REST API with OpenAPI.
- **`/metrics`** in Prometheus format.
- SQLite or Postgres; Alembic migrations run on startup.

## How it works

```
           ┌─────────────── one process ───────────────┐
 browser ─▶│ FastAPI: REST API, dashboard, /status      │
           │                                            │
           │ Scheduler (asyncio)                        │      ┌──────────────┐
           │  1. claim: UPDATE … RETURNING ─────────────┼─────▶│ SQLite /     │
           │  2. probe: httpx to the validated IP       │      │ Postgres     │
           │  3. result + incident + alert in outbox,   │─────▶│              │
           │     one transaction                        │      └──────────────┘
           │  4. outbox delivery with retries ──────────┼─────▶ Telegram / webhook
           └────────────────────────────────────────────┘
```

| Module | Responsibility |
|---|---|
| `incidents.py` | Pure decision functions: when a monitor is down, when to warn about TLS, which alert is stale |
| `checker.py` | One HTTP request → a result. Never raises |
| `security.py` | SSRF protection |
| `service.py` | Claiming work, recording results, the outbox, the scheduler |
| `stats.py`, `metrics.py` | Aggregates and the Prometheus export |
| `api.py`, `schemas.py` | HTTP layer and validation |
| `ratelimit.py` | Throttling of API key guessing |

## Engineering decisions

**SSRF protection.** The service fetches URLs supplied by users, which is the classic way to reach
an internal network or cloud metadata (`169.254.169.254`).
- A host is rejected if any of its addresses is not public (including `::ffff:127.0.0.1` and the
  decimal form of an IP).
- The connection goes **to the very IP that passed validation**, with the original `Host` and SNI.
  Otherwise DNS could answer differently between the check and the request (DNS rebinding). The
  certificate is still verified against the original hostname, which is covered by a test against
  a real TLS server.
- Redirects are not followed, and validation runs on every check, not only when a monitor is created.

**Alerts are not lost (transactional outbox).** The status change, the incident and the alert
record are committed in one transaction; delivery happens separately with exponential backoff.
There is one record per channel, so a failing webhook does not cause duplicates in Telegram. The
guarantee is at-least-once.

**Several instances without double checks.** A monitor is claimed with a single
`UPDATE … RETURNING` that pushes its next check time forward by a lease. No Redis or broker is
needed, and it works on both SQLite and Postgres. If a worker dies mid-check, the lease expires and
another one picks the monitor up.

**A slow site does not hold up the rest.** Every check is its own task with a total deadline: a
server that drips one byte per second cannot hold it longer than the timeout. At most 1 MB of the
body is read.

**Graceful shutdown.** The scheduler stops on a flag, lets in-flight checks record their results,
and cancels only those that do not finish within 8 seconds. This fixed a real bug: cancelling tasks
in the middle of a database call left connections open (there is a regression test).

**Front-end hardening.** A strict Content-Security-Policy: scripts run only from the same origin,
inline code is forbidden, and the page cannot be framed. A test makes sure no inline script sneaks
back into the markup, where the policy would silently block it for users.

**API key brute-force protection.** After 10 wrong keys from one address the client is locked out
for 5 minutes, and the lockout applies before the key is checked. Behind a reverse proxy the client
address is taken from the last `X-Forwarded-For` entry, because the earlier ones can be forged by
the client.

**Other details.** Time is stored in UTC and returned with `Z`. The API key is compared with
`secrets.compare_digest`. The Telegram token never reaches logs or the database. The public status
page exposes no URLs or error messages. `/health` also checks the database.

## Tests and quality

```bash
pytest --cov      # ~97% coverage, CI gate at 90%
ruff check . && ruff format --check . && mypy    # mypy in strict mode
WT_TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/db pytest   # same suite on Postgres
pip install -e ".[dev,e2e]" && playwright install chromium && pytest tests/e2e   # browser tests
```

Beyond ordinary unit tests, the suite checks:

- IP pinning and certificate verification against a local TLS server, not mocks;
- the total deadline against a server that drips the body byte by byte;
- two "instances" claiming 20 monitors at once: the sets do not overlap;
- 40 checks finishing and writing to the database simultaneously, with no record lost;
- migrations match the models (a forgotten migration fails the test);
- an end-to-end run: startup → migrations → check → webhook alert → incident in the API;
- the UI in a real browser (Playwright): add, edit, pause, delete, API key sign-in, no CSP
  violations, and HTML in a monitor name is shown as text rather than executed.

CI: lint and types, tests on Python 3.11–3.13, a separate run on Postgres, browser tests, and a
Docker image build with a smoke test.

## API

| Method | Path | Description |
|---|---|---|
| `POST` `GET` | `/api/monitors` | Create, list |
| `GET` `PATCH` `DELETE` | `/api/monitors/{id}` | Get, update (including pause), delete with history |
| `GET` | `/api/monitors/{id}/checks`, `/stats` | Check history, uptime and latency |
| `GET` | `/api/summary` | All monitors with statistics in one request |
| `GET` | `/api/incidents`, `/api/alerts` | Incidents, alert delivery log |
| `GET` | `/api/public/status` | Data for the public page (no key required) |
| `GET` | `/metrics`, `/health` | Prometheus, liveness |

```bash
curl -X POST localhost:8000/api/monitors -H 'content-type: application/json' \
  -d '{"name":"My site","url":"https://example.com","interval_seconds":60,"keyword":"Example"}'
```

## Limitations

- Alert delivery is at-least-once: a crash after sending but before the database write can
  produce a duplicate.
- Checks run from a single location: a network failure at the service itself looks like every
  site is down.
- Statistics are computed from raw checks; thousands of monitors would need hourly rollups.
- There is no user management, only one shared API key.
- The failed sign-in counter lives in process memory: with several instances the limit applies
  to each one separately.
