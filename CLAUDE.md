# Café Inventory System

Barcode-based inventory management for a café and ice cream shop. Staff scan a code at a station by the storage door and stock is deducted automatically. The owner sees stock, expiring batches and reorder suggestions on a web dashboard, and can ask an AI agent questions.

This is a student portfolio project built part-time (10–15 hrs/week). A real shop will pilot the MVP. Prefer simple, readable code over clever code, and explain non-obvious AWS decisions in comments.

## Current status

- Phase 1 in progress. No Raspberry Pi yet: use `station/scanner_sim.py` on the laptop (not in this repo yet).
- `station/scanner_station.py` is the Raspberry Pi version (evdev + gpiozero), not yet tested on hardware (not in this repo yet).
- `/backend/core` done: code types, units, undo by code (today, this station), never-negative clamp, duplicate keys, shop settings (optional staff badges). Tested with pytest.
- Phase 1 keeps it simple: each item has one `stock` number. Batches, expiry and FIFO come in Phase 2.
- Deployed to `us-east-2`: `CDKToolkit` (bootstrap), `CafeInventory-Budget` ($10/month alerts) and `CafeInventory-dev`: 6 DynamoDB tables (key builders in `backend/store/keys.py`) plus the station API, `POST /scan` and `GET /station/config` (handlers in `backend/handlers`, DynamoDB access in `backend/store/dynamo.py`). The API URL and the door-1 API key id are stack outputs; get the key value with `aws apigateway get-api-key --api-key <id> --include-value`.
- Smoke test OK: an unregistered station gets 403 from our Lambda.
- Seed script `scripts/seed.py` written and tested with moto, **not yet run against dev** (the tables are still empty).
- **Next:** `aws login`, run the seed, then test end to end with curl (`GET /station/config`, a scan, an undo) and check stock in the Items table. This also shows whether Lambda needs the `tzdata` package. Then the iPhone Shortcut test.

Update this section as phases are completed.

## Architecture

All serverless on AWS, defined with AWS CDK (Python). No EC2, no RDS, nothing always-on.

```
Scanner station (Pi or laptop sim) --API key--> API Gateway --> Lambda --> DynamoDB
Owner dashboard (React) --Cognito JWT-------->  API Gateway --> Lambda --> DynamoDB
ScanLog --DynamoDB Streams--> alerts Lambda --> SNS email (below reorder point)
EventBridge (daily) --> Lambda --> SNS email (expiring batches)
Dashboard AI chat --> Lambda --> Bedrock (tool calling) --> Lambda tools --> DynamoDB
```

| Layer | Choice |
| --- | --- |
| Scanner station | Python 3, USB 2D barcode scanner acting as keyboard, SQLite offline queue |
| Dashboard | React + Vite + Recharts, hosted on S3 + CloudFront |
| Auth | Cognito user pool (owner/manager); scan-only API key for stations |
| Backend | API Gateway (REST) + Lambda (Python 3.12) |
| Database | DynamoDB (on-demand) |
| Alerts | DynamoDB Streams → alerts Lambda (reorder points); EventBridge Scheduler daily job (expiry); SNS email (SMS later) |
| AI agent | Amazon Bedrock with tool calling |
| Infra | AWS CDK (Python), CloudWatch logs |

## Key design decisions

- **Multi-tenancy:** one shared system for all shops, data kept apart by key. Every DynamoDB key starts with `TENANT#<tenant_id>#` (e.g. `TENANT#cafe-01#ITEM#lids_12oz`). The API works out the tenant from the station's API key or the owner's Cognito login and **never trusts a `tenant_id` sent in the request body**. Big customers could later get their own separate deployment.
- **Idempotent scans:** scan id = station + `scanned_at` (`scan_id()` in `/backend/core`), which is also the ScanLog key. `POST /scan` saves with a DynamoDB conditional write, so a scan resent by the offline queue is ignored. The station must set `scanned_at` once (microseconds, saved in its queue) and reuse it on resend.
- **Event-driven alerts:** the scan service only writes the log entry. DynamoDB Streams passes it to an alerts Lambda that checks reorder points, so scans stay fast and keep working if alerts fail. A daily EventBridge job catches expiring batches.
- **Offline-first stations:** the station queues scans in SQLite when the internet drops and resends later; idempotency makes resending safe.
- **Append-only log is the source of truth:** an item's `stock` is a running total that can always be rebuilt from ScanLog (sum of `change`). This makes undo, audits and bug fixes easier and feeds reorder predictions.
- **Scaling:** Lambda and DynamoDB on-demand scale automatically, from one shop to hundreds. Watch per-tenant rate limits (an API Gateway usage plan per API key) and Bedrock costs.

## Repo layout (target)

```
/infra          CDK app and stacks
/backend        Lambda handlers + shared logic
  /handlers     one file per endpoint
  /core         inventory logic (FIFO, units, reorder math) — pure functions, no AWS calls
  /store        DynamoDB data layer (key formats in keys.py; adds the tenant prefix)
  /tests        pytest
/station        scanner_sim.py, scanner_station.py
/scripts        local tools run with your `aws login`: seed.py (test shop), later the door sheet
/dashboard      React app
/docs           diagrams, notes
CLAUDE.md
```

## Domain rules (important)

- **Smallest unit:** stock is always stored in the item's smallest unit (e.g. bags of lids). A code can represent several units (1 box = 12 bags).
- **Codes:** our own codes start with `INV-` (e.g. `INV-LID12-1BAG`, `INV-LID12-1BOX`, `INV-LID12-RESTOCK-BOX`). Staff badges start with `STAFF-`. Supplier UPC barcodes are also allowed and map to an item. Ignore any other scanned code.
- **Undo:** scanning `INV-UNDO` puts the station in undo mode (about 10 s, shown on screen; cancelled by another `INV-UNDO` or a badge). The next code scanned reverses the most recent not-yet-undone scan of exactly that code, made today (shop's local time) at this station. Repeat to undo more. Nothing matching → "Nothing to undo". Older mistakes are fixed with a manager correction. The station sends one request: `{action: "undo", code: <code to undo>}`.
- **Staff badges are optional per shop** (owner's setting on the Shops record):
  - `require_staff_badge` (default off): when on, every scan and undo needs a valid, active `STAFF-` badge. The API enforces this, not the station. When off, scans need no badge (staff is recorded only if sent).
  - `undo_own_scans_only` (default off, only allowed when badges are on): staff can only undo their own scans.
- **Stock (Phase 1):** each item has a single `stock` number. A scan adds or deducts the code's quantity.
- **Batches (Phase 2, not built yet):** items with expiry (dairy, ice cream) are tracked per batch with `expires_on`. Deductions are FIFO: oldest expiry first. Items without expiry use a single batch.
- **Scan log is append-only:** every scan, restock, undo and manual correction is a log entry. Undo creates a reversing entry, never deletes.
- **Idempotency:** each scan carries `station` + `scanned_at`; the API must ignore duplicates (offline queue may resend).
- **Never go negative:** if a deduction exceeds stock, record it, clamp stock at 0 and flag the item for a count (`needs_count`).
- **Reorder point** = avg daily usage × supplier lead time + safety stock. Math lives in `/backend/core`, never in the AI agent.

## Data model (DynamoDB, single table or a few tables — keep it simple)

Separate tables (one per entity), all on-demand. Attribute names are `pk` / `sk`; key formats live in `backend/store/keys.py` (the infra tests check the stack matches). Shop ids look like `cafe-01`.

| Table | `pk` | `sk` | Other fields |
| --- | --- | --- | --- |
| Shops | `TENANT#cafe-01` | — | name, timezone (e.g. `America/Los_Angeles`), require_staff_badge, undo_own_scans_only |
| Items | `TENANT#cafe-01` | `ITEM#lids_12oz` | name, category, smallest_unit, stock, needs_count, reorder_point, lead_time_days (has_expiry: Phase 2) |
| Codes | `TENANT#cafe-01` | `CODE#INV-LID12-1BOX` | item_id, quantity (signed, smallest units: negative for `take`, positive for `restock`), action (`take` / `restock`) |
| Staff | `TENANT#cafe-01` | `STAFF#STAFF-001` | name, active |
| Stations | `APIKEY#<api key id>` | — | tenant_id, station_id, name |
| ScanLog | `TENANT#cafe-01#STATION#door-1` | `scanned_at` (UTC, microseconds) | item_id, item_pk, code, change, shortfall, staff (optional), action (`take` / `restock` / `undo` / `correction`), undoes |
| Batch (Phase 2) | | | quantity, received_on, expires_on |

- **Stations** is the only table without a tenant prefix: looking up the API key is how the API finds the tenant.
- **ScanLog** key = station + `scanned_at` = the duplicate check (conditional write). Index `by_item` (`item_pk` = `TENANT#cafe-01#ITEM#lids_12oz`, `sk`) gives an item's history across stations. A stream (`NEW_IMAGE`) will feed the alerts Lambda. `change` is what was actually applied to stock; `shortfall` = units asked for but not in stock (sets the item's `needs_count`); `undoes` = id (`station#scanned_at`) of the entry an undo reverses.
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
| POST | `/batches` | Cognito | Restock with quantity and expiry |
| GET | `/log` | Cognito | Recent activity, filterable by item |
| POST | `/corrections` | Cognito | Manual count adjustment |

## Phases and tasks

### Phase 1 — Core API + scanner (target end of Nov 2026)
- [x] CDK app skeleton with dev stage, budget alarm, tags
- [x] DynamoDB tables (Shops, Items, Codes, Staff, Stations, ScanLog) with `TENANT#` keys and a stream on ScanLog (Batch table comes in Phase 2)
- [x] Undo by code (today, this station) and owner settings for staff badges, in core
- [x] `/backend/core` inventory logic with pytest tests (units, undo, never negative, duplicates)
- [ ] `recalculate_stock(entries)` in core: rebuild an item's stock from its ScanLog (for corrections and audits)
- [ ] `POST /scan` + `GET /station/config` Lambdas + API Gateway with API key and usage plan. Built, tested and deployed; still to do: the end-to-end check with seed data (a scan changes stock, and the time zone database works on Lambda; add the `tzdata` package if not)
- [x] Seed script with sample items and codes (`scripts/seed.py`; not yet run against dev)
- [ ] Point `scanner_sim.py` at the real API (`API_URL`, `API_KEY`)
- [ ] Script to print the door scan sheet (QR codes for each `INV-` code)

### Phase 2 — Batches and expiry (target Dec 20)
- [ ] `POST /batches` restock with expiry
- [ ] FIFO deduction across batches (a first version with tests is in git commit `a4f068a`)
- [ ] Alerts Lambda on the ScanLog stream: item drops below reorder point → SNS email
- [ ] EventBridge daily job: batches expiring in 3 days → SNS email

### Phase 3 — Owner dashboard (target Jan 24, 2027)
- [ ] Cognito user pool and login
- [ ] Stock list with green/yellow/red status
- [ ] Expiring soon, low stock, activity log with corrections
- [ ] Host on S3 + CloudFront via CDK

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
- Seed the dev tables with the test shop `cafe-01` (America/Los_Angeles, badges off, 6 sample items, 14 codes incl. one supplier UPC, 2 staff): `uv run python scripts/seed.py` (keeps current stock; `--reset-stock` puts sample amounts back). Safe to re-run.
- CDK: run from `/infra` with the pinned CLI: `npx cdk synth`, `npx cdk deploy <stack>`, `-c stage=dev|prod` (default `dev`). Needs `.env` (copy `.env.example`).
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
