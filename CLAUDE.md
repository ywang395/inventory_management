# Café Inventory System

Barcode-based inventory management for a café and ice cream shop. Staff scan a code at a station by the storage door and stock is deducted automatically. The owner gets low-stock emails, sees stock and reorder suggestions on a web dashboard, and can ask an AI agent questions.

This is a student portfolio project built part-time (10–15 hrs/week). A real shop will pilot the MVP. Prefer simple, readable code over clever code, and explain non-obvious AWS decisions in comments.

## Current status

- **Phase 1 done (2026-10-23).** Core API, scanner station, seed, door sheet and stock board all work against dev.
- Scanner station: `station/scanner_sim.py` (Python, laptop; a USB scanner types like a keyboard). Live-tested on dev: scan, undo, and two scans with Wi-Fi off that waited in the SQLite queue and were sent when the connection came back. A Raspberry Pi is optional later: the same program runs there with a terminal (a headless version would read the scanner with evdev).
- `/backend/core` done: code types, units, undo by code (today, this station), never-negative clamp, duplicate keys, shop settings (optional staff badges), `recalculate_stock` (stock = sum of the log's `change`). Tested with pytest.
- Each item has one `stock` number. Batches, expiry dates, FIFO deduction and the daily expiry job are **skipped** (decided 2026-10-23: the project won't go to real production).
- Deployed to `us-east-2`: `CDKToolkit` (bootstrap), `CafeInventory-Budget` ($10/month alerts) and `CafeInventory-dev`: 6 DynamoDB tables (key builders in `backend/store/keys.py`) plus the station API, `POST /scan` and `GET /station/config` (handlers in `backend/handlers`, DynamoDB access in `backend/store/dynamo.py`). The API URL and the door-1 API key id are stack outputs; get the key value with `aws apigateway get-api-key --api-key <id> --include-value`.
- Seed script `scripts/seed.py` has been run against dev: test shop `cafe-01` with 7 items (incl. a test candle with a real barcode), 16 codes, 2 staff, and station door-1 linked to the API key. `GET /station/config` returns the shop settings (HTTP 200), which also confirmed Lambda's Python 3.12 runtime has the time zone database (no `tzdata` package needed).
- Station API tested end to end on dev with curl: scan, resend of the same scan (`duplicate`, not saved), a second scan, two undos (each reverses the latest not-yet-undone scan) and a third undo (`nothing_to_undo`). Lids stock went 30 → 18 → 6 → 18 → 30 and ScanLog holds the matching `take` / `undo` entries.
- Also tested from an iPhone Shortcut acting as station door-1 (Scan QR or Barcode → Format Date `yyyy-MM-dd'T'HH:mm:ss.SSSZZZZZ` → POST /scan with the `x-api-key` header and a JSON body → show `message`). Scanning the test candle's real barcode took 1 candle (10 → 9). It showed that iPhones read UPC-A barcodes as 13 digits with a leading `0`; codes are now normalized so both forms match (deployed).
- Known gap: the seed sets opening stock directly, without log entries, so on dev the log doesn't add up to `stock` yet (e.g. lids: log sums to 0, stock is 30).
- Owner demo (2026-10-23): the iPhone Shortcut scanned the real Producers whole milk jug barcode (`078255001097`) and the printed door sheet (`scripts/door_sheet.py`); `scripts/stock_board.py` showed stock live (status from `stock_status()` in core). Demo amounts (lids 12, milk 8: "low" after two scans) come from `seed.py --reset-stock`. The demo work is merged into `main`.
- **Owner feedback:** the idea is good, but the main risk is that staff won't scan consistently. Design answer: a fixed hands-free station, scan per box/case taken (not per unit), track only the 10–15 key items, and a weekly count that corrects stock and measures how many scans were missed.
- **Phase 2 (alerts) done (2026-10-23):** low-stock emails, Items stream → alerts Lambda → SNS. Live-tested on dev: a restock (lids 10 → 22) sent nothing; taking a box (22 → 10) emailed "[Test Cafe] Low stock: 12 oz cup lids" within seconds.
- **Phase 3 in progress.** Step 1 done: owner login (Cognito user pool `cafe-inventory-dev-owners`, invite-only, immutable `custom:tenant_id`, managed login page, public dashboard client with code + PKCE, callback `http://localhost:5173/`). Live-tested: invited the owner with `scripts/create_owner.py`, signed in with the temporary password, set a new password, redirected back with a code; the user is CONFIRMED with tenant `cafe-01`.
- **Next:** Phase 3 step 2, the owner API (separate REST API, Cognito authorizer, CORS): `GET /items`, `GET /log`.
- **Later (proposed):** weekly counts and corrections, with a "scan accuracy" number per week. It is the safety net for missed scans and also fixes the opening-stock gap above.

Update this section as phases are completed.

## Architecture

All serverless on AWS, defined with AWS CDK (Python). No EC2, no RDS, nothing always-on.

```
Scanner station (Pi or laptop sim) --API key--> API Gateway --> Lambda --> DynamoDB
Owner dashboard (React) --Cognito JWT-------->  API Gateway --> Lambda --> DynamoDB
Items table --DynamoDB Streams--> alerts Lambda --> SNS --> email (low / out / count)
Dashboard AI chat --> Lambda --> Bedrock (tool calling) --> Lambda tools --> DynamoDB
```

| Layer | Choice |
| --- | --- |
| Scanner station | Python 3, USB 2D barcode scanner acting as keyboard, SQLite offline queue |
| Dashboard | React + Vite + Recharts, hosted on S3 + CloudFront |
| Auth | Cognito user pool (owner/manager); scan-only API key for stations |
| Backend | API Gateway (REST) + Lambda (Python 3.12) |
| Database | DynamoDB (on-demand) |
| Alerts | DynamoDB Streams on Items → alerts Lambda → SNS topic → email (SMS later) |
| AI agent | Amazon Bedrock with tool calling |
| Infra | AWS CDK (Python), CloudWatch logs |

## Key design decisions

- **Multi-tenancy:** one shared system for all shops, data kept apart by key. Every DynamoDB key starts with `TENANT#<tenant_id>#` (e.g. `TENANT#cafe-01#ITEM#lids_12oz`). The API works out the tenant from the station's API key or the owner's Cognito login and **never trusts a `tenant_id` sent in the request body**. Big customers could later get their own separate deployment.
- **Idempotent scans:** scan id = station + `scanned_at` (`scan_id()` in `/backend/core`), which is also the ScanLog key. `POST /scan` saves with a DynamoDB conditional write, so a scan resent by the offline queue is ignored. The station must set `scanned_at` once (microseconds, saved in its queue) and reuse it on resend.
- **Event-driven alerts:** the scan service only saves the scan. The Items table's stream passes every change, with the item before and after, to an alerts Lambda, so scans stay fast and keep working if alerts fail. Listening to Items (not ScanLog) gives the exact before/after stock and catches every kind of change (scans, undos, later corrections). Alerts go to one SNS topic; each message carries `tenant_id`, so each shop owner can later subscribe with a filter policy for their own shop.
- **Offline-first stations:** the station queues scans in SQLite when the internet drops and resends later; idempotency makes resending safe.
- **Append-only log is the source of truth:** an item's `stock` is a running total that can always be rebuilt from ScanLog (sum of `change`). This makes undo, audits and bug fixes easier and feeds reorder predictions.
- **Scaling:** Lambda and DynamoDB on-demand scale automatically, from one shop to hundreds. Watch per-tenant rate limits (an API Gateway usage plan per API key) and Bedrock costs.

## Repo layout (target)

```
/infra          CDK app and stacks
/backend        Lambda handlers + shared logic
  /handlers     one file per endpoint
  /core         inventory logic (scans, undo, stock status, alerts, reorder math) — pure functions, no AWS calls
  /store        DynamoDB data layer (key formats in keys.py; adds the tenant prefix)
  /tests        pytest
/station        the scanner station: logic.py (undo/badge rules), outbox.py (SQLite queue),
                api.py (HTTP), scanner_sim.py (run this), tests/
/scripts        local tools run with your `aws login`: seed.py (test shop), stock_board.py,
                door_sheet.py, station_env.py (writes station/.env)
/dashboard      React app
/docs           diagrams, notes
CLAUDE.md
```

## Domain rules (important)

- **Smallest unit:** stock is always stored in the item's smallest unit (e.g. bags of lids). A code can represent several units (1 box = 12 bags).
- **Codes:** our own codes start with `INV-` (e.g. `INV-LID12-1BAG`, `INV-LID12-1BOX`, `INV-LID12-RESTOCK-BOX`). Staff badges start with `STAFF-`. Supplier UPC barcodes are also allowed and map to an item. Ignore any other scanned code. Codes are normalized (trimmed, upper case) before lookup and when saved; a 13-digit barcode starting with `0` is the 12-digit UPC-A it contains (iPhones read UPC-A that way, USB scanners usually don't), so both match.
- **Undo:** scanning `INV-UNDO` puts the station in undo mode (about 10 s, shown on screen; cancelled by another `INV-UNDO` or a badge). The next code scanned reverses the most recent not-yet-undone scan of exactly that code, made today (shop's local time) at this station. Repeat to undo more. Nothing matching → "Nothing to undo". Older mistakes are fixed with a manager correction. The station sends one request: `{action: "undo", code: <code to undo>}`.
- **Staff badges are optional per shop** (owner's setting on the Shops record):
  - `require_staff_badge` (default off): when on, every scan and undo needs a valid, active `STAFF-` badge. The API enforces this, not the station. When off, scans need no badge (staff is recorded only if sent).
  - `undo_own_scans_only` (default off, only allowed when badges are on): staff can only undo their own scans.
  - At the station, a scanned badge stays active until another badge is scanned or 2 minutes pass without a scan. When badges are required, the station asks for one before sending (the API still enforces it).
- **Stock (Phase 1):** each item has a single `stock` number. A scan adds or deducts the code's quantity.
- **Alerts:** an email only when an item gets worse, so one drop means one email: **low** (stock drops to or below the reorder point), **out** (stock reaches 0), **count** (a scan took more than was in stock, `needs_count` turns on). Restocks and undos that go up send nothing. Rule in `core/alerts.py`.
- **Batches / expiry / FIFO: skipped** (not going to production). A first FIFO version with tests is in the first commit, "Add backend core inventory logic with pytest tests".
- **Scan log is append-only:** every scan, restock, undo and manual correction is a log entry. Undo creates a reversing entry, never deletes.
- **Idempotency:** each scan carries `station` + `scanned_at`; the API must ignore duplicates (offline queue may resend).
- **Never go negative:** if a deduction exceeds stock, record it, clamp stock at 0 and flag the item for a count (`needs_count`).
- **Reorder point** = avg daily usage × supplier lead time + safety stock. Math lives in `/backend/core`, never in the AI agent.

## Data model (DynamoDB, single table or a few tables — keep it simple)

Separate tables (one per entity), all on-demand. Attribute names are `pk` / `sk`; key formats live in `backend/store/keys.py` (the infra tests check the stack matches). Shop ids look like `cafe-01`.

| Table | `pk` | `sk` | Other fields |
| --- | --- | --- | --- |
| Shops | `TENANT#cafe-01` | — | name, timezone (e.g. `America/Los_Angeles`), require_staff_badge, undo_own_scans_only |
| Items | `TENANT#cafe-01` | `ITEM#lids_12oz` | name, category, smallest_unit, stock, needs_count, reorder_point, lead_time_days |
| Codes | `TENANT#cafe-01` | `CODE#INV-LID12-1BOX` | item_id, quantity (signed, smallest units: negative for `take`, positive for `restock`), action (`take` / `restock`) |
| Staff | `TENANT#cafe-01` | `STAFF#STAFF-001` | name, active |
| Stations | `APIKEY#<api key id>` | — | tenant_id, station_id, name |
| ScanLog | `TENANT#cafe-01#STATION#door-1` | `scanned_at` (UTC, microseconds) | item_id, item_pk, code, change, shortfall, staff (optional), action (`take` / `restock` / `undo` / `correction`), undoes |

- **Items** has a stream (`NEW_AND_OLD_IMAGES`): every change, before and after, goes to the alerts Lambda.
- **Stations** is the only table without a tenant prefix: looking up the API key is how the API finds the tenant.
- **ScanLog** key = station + `scanned_at` = the duplicate check (conditional write). Index `by_item` (`item_pk` = `TENANT#cafe-01#ITEM#lids_12oz`, `sk`) gives an item's history across stations. Its stream (`NEW_IMAGE`) isn't used yet (free, kept for later, e.g. usage statistics). `change` is what was actually applied to stock; `shortfall` = units asked for but not in stock (sets the item's `needs_count`); `undoes` = id (`station#scanned_at`) of the entry an undo reverses.
- **dev** tables are deleted by `cdk destroy`; **prod** tables are retained, deletion-protected and have point-in-time backups.
- `/backend/core` stays tenant-agnostic; `/backend/store` adds the prefix.

## API (first version)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/scan` | API key | Body: `{code, action: "scan" or "undo", staff (optional), scanned_at}`. For undo, `code` is the code to undo. The station and shop come from the API key; any `station` / `tenant_id` in the body is ignored. Replies 200 `{status: applied / duplicate / nothing_to_undo / rejected / ignored, message, item?, change?}`, 400 bad request, 403 unregistered key, 503 busy (resend later). |
| GET | `/station/config` | API key | Shop settings the station needs (shop and station name, time zone, badge settings); the station caches them for offline use |
| GET | `/items` | Cognito | Items with current stock and status |
| POST | `/items` | Cognito | Create item |
| POST | `/codes` | Cognito | Create code for an item |
| GET | `/log` | Cognito | Recent activity, filterable by item |
| POST | `/corrections` | Cognito | Manual count adjustment |

## Phases and tasks

### Phase 1 — Core API + scanner (target end of Nov 2026)
- [x] CDK app skeleton with dev stage, budget alarm, tags
- [x] DynamoDB tables (Shops, Items, Codes, Staff, Stations, ScanLog) with `TENANT#` keys, streams on ScanLog and Items (no Batch table: batches are skipped)
- [x] Undo by code (today, this station) and owner settings for staff badges, in core
- [x] `/backend/core` inventory logic with pytest tests (units, undo, never negative, duplicates)
- [x] `recalculate_stock(entries)` in core (`core/stock.py`): rebuild an item's stock from its ScanLog (for corrections and audits)
- [x] `POST /scan` + `GET /station/config` Lambdas + API Gateway with API key and usage plan (deployed to dev; scan, duplicate and undo tested end to end with curl)
- [x] Seed script with sample items and codes (`scripts/seed.py`; run against dev)
- [x] Point `scanner_sim.py` at the real API (`API_URL`, `API_KEY`): offline queue, undo mode, badges, cached settings; live-tested on dev
- [x] Script to print the door scan sheet (QR codes for each `INV-` code): `scripts/door_sheet.py`, from the live Codes and Items tables. `INV-UNDO` gets added once the station has undo mode.

### Phase 2 — Alerts (target Dec 20)
- [x] Alerts Lambda on the Items stream: item gets low / runs out / needs a count → SNS email (deployed, live-tested)
- Skipped (not going to production): `POST /batches` with expiry, FIFO deduction across batches, EventBridge daily expiry job

### Phase 3 — Owner dashboard (target Jan 24, 2027)
- [x] Cognito user pool and login (invite-only, `custom:tenant_id`, managed login; `scripts/create_owner.py`)
- [ ] Owner API: separate REST API with a Cognito authorizer (ID token), CORS; `GET /items`, `GET /log`; shop from the token's `custom:tenant_id`
- [ ] Dashboard (React + Vite + TypeScript, `react-oidc-context`): sign in, stock list with green/yellow/red status (low and out first), an item's recent activity
- [ ] Host on S3 + CloudFront via CDK (own stack; if the project blocks CloudFront, choose another way)
- [ ] Optional: counts and corrections from the dashboard (`POST /corrections`)

### Phase 4 — Pilot (Jan 25 – Feb 21)
- [ ] Deploy station in the shop, weekly spot counts, fix issues

### Phase 5 — Reorder predictions (Feb 22 – Mar 14)
- [ ] Usage stats per item, weekday patterns, reorder suggestions, waste report

### Phase 6 — AI agent (Mar 15 – Apr 11)
- [ ] Bedrock tool calling with read tools: `get_stock`, `get_usage`, `get_expiring`, `get_reorder_suggestions`
- [ ] Write actions (e.g. `draft_order`) require owner confirmation in the UI
- [ ] Log every agent tool call

## Conventions

- Python: type hints, small functions, `ruff` for lint/format, `pytest` for tests.
- DynamoDB code is tested against `moto` (a fake DynamoDB in memory, `backend/tests/conftest.py`); tests never touch the real account.
- Python env is managed with `uv` (Python 3.12, same as Lambda): `uv sync`, `uv run pytest`, `uv run ruff check . && uv run ruff format .`
- Seed the dev tables with the test shop `cafe-01` (America/Los_Angeles, badges off, 7 sample items, 16 codes incl. three supplier UPCs (real barcodes on a milk jug and a test candle, for phone tests), 2 staff): `uv run python scripts/seed.py` (keeps current stock; `--reset-stock` puts sample amounts back). Safe to re-run.
- Live stock board (read-only, for demos): `uv run python scripts/stock_board.py` (Ctrl+C to stop; `--once` prints once).
- Scanner station: `uv run python scripts/station_env.py` once (writes `station/.env` with the API URL and key, never printed), then `uv run --env-file station/.env python station/scanner_sim.py`. Its queue is `station/station.db` (git-ignored). No extra packages: `sqlite3` and `urllib` from Python itself.
- Door scan sheet: `uv run python scripts/door_sheet.py` writes `out/door-sheet.html` (git-ignored) and opens it; print from the browser. QR codes come from `segno` (dev dependency) and must be regular QR, not Micro QR (phones can't read those; a test checks this).
- CDK: run from `/infra` with the pinned CLI: `npx cdk synth`, `npx cdk deploy <stack>`, `-c stage=dev|prod` (default `dev`). Needs `.env` (copy `.env.example`).
- Owner logins: `uv run --env-file .env python scripts/create_owner.py` invites `BUDGET_ALERT_EMAIL` (or `--email`) to shop `cafe-01`; Cognito emails a temporary password (`--resend` if it expired after 7 days). Login page: `LoginDomainUrl` output. Dev pool has MFA off (dev-only exception); prod requires an authenticator app.
- Stock alerts and budget alerts go to `BUDGET_ALERT_EMAIL`. After the first deploy of a stack (or a new address), AWS emails "Subscription Confirmation": click the link, or no alerts arrive. Check with `aws sns list-subscriptions-by-topic` (`PendingConfirmation` = not clicked yet).
- Business logic in `/backend/core` as pure functions; handlers only parse input, call core, write to DynamoDB.
- Config via environment variables; never hard-code ARNs, keys or table names.
- Never commit secrets. `.env` is git-ignored.
- Least-privilege IAM: each Lambda gets only the tables and actions it needs.
- Every new endpoint gets a test and a line in the API table above.

## AWS account

- Uses the **new AWS experience**: sign-in with an AWS Builder ID; resources live in the project "Touch Grass Later" (an AWS account managed by AWS). There is no root user or IAM user to manage.
- **Region is fixed to `us-east-2` (Ohio).** AWS blocks other regions for this project (except global services). Deploy everything there.
- CLI/CDK auth: `aws login` (official AWS CLI installer, not Homebrew), then pick the project in the browser. Credentials last 12 hours; run `aws login` again after that.
- Local Python scripts (boto3) use the same `aws login` session through the dev-only `botocore[crt]` package; without it boto3 fails with "Missing Dependency".
- Free plan services cover everything planned (DynamoDB, Lambda, API Gateway, Cognito, S3, SNS, EventBridge, CloudFormation, IAM roles, Budgets). Organizations / IAM Identity Center are not available.
- **Never "Activate advanced features"** in AWS Settings: it needs a paid upgrade and can't be undone.

## Cost guardrails

- AWS Free Plan: credits, account closes after 6 months unless upgraded. Upgrade to Paid plan before April 2027.
- Budget: `CafeInventory-Budget` stack, $10/month, emails at $5 spent, $10 spent and $10 forecast. Credits are excluded so it tracks real usage. Serverless only. Delete unused stacks with `cdk destroy`.
- Bedrock and SMS cost money per use; use SNS email during development.

## How to work with me (Claude)

- Work on one task from the current phase at a time; propose a short plan before large changes.
- Write or update tests with each change and run them.
- When a task is done, tick it in this file and update "Current status".
- Ask before adding new AWS services or dependencies.
