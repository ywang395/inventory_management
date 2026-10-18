# Café Inventory System

Barcode-based inventory management for a café and ice cream shop. Staff scan a code at a station by the storage door and stock is deducted automatically. The owner sees stock, expiring batches and reorder suggestions on a web dashboard, and can ask an AI agent questions.

This is a student portfolio project built part-time (10–15 hrs/week). A real shop will pilot the MVP. Prefer simple, readable code over clever code, and explain non-obvious AWS decisions in comments.

## Current status

- Phase 1 in progress. No Raspberry Pi yet: use `station/scanner_sim.py` on the laptop (not in this repo yet).
- `station/scanner_station.py` is the Raspberry Pi version (evdev + gpiozero), not yet tested on hardware (not in this repo yet).
- `/backend/core` done: code types, units, undo, never-negative clamp, duplicate keys. Tested with pytest.
- Phase 1 keeps it simple: each item has one `stock` number. Batches, expiry and FIFO come in Phase 2.
- AWS backend not built yet (no CDK app, tables or handlers).

Update this section as phases are completed.

## Architecture

All serverless on AWS, defined with AWS CDK (Python). No EC2, no RDS, nothing always-on.

```
Scanner station (Pi or laptop sim) --API key--> API Gateway --> Lambda --> DynamoDB
Owner dashboard (React) --Cognito JWT-------->  API Gateway --> Lambda --> DynamoDB
EventBridge (daily) --> Lambda --> SNS email (low stock, expiring batches)
Dashboard AI chat --> Lambda --> Bedrock (tool calling) --> Lambda tools --> DynamoDB
```

| Layer | Choice |
| --- | --- |
| Scanner station | Python 3, USB 2D barcode scanner acting as keyboard, SQLite offline queue |
| Dashboard | React + Vite + Recharts, hosted on S3 + CloudFront |
| Auth | Cognito user pool (owner/manager); scan-only API key for stations |
| Backend | API Gateway (REST) + Lambda (Python 3.12) |
| Database | DynamoDB (on-demand) |
| Alerts | EventBridge Scheduler + SNS email (SMS later) |
| AI agent | Amazon Bedrock with tool calling |
| Infra | AWS CDK (Python), CloudWatch logs |

## Repo layout (target)

```
/infra          CDK app and stacks
/backend        Lambda handlers + shared logic
  /handlers     one file per endpoint
  /core         inventory logic (FIFO, units, reorder math) — pure functions, no AWS calls
  /tests        pytest
/station        scanner_sim.py, scanner_station.py
/dashboard      React app
/docs           diagrams, notes
CLAUDE.md
```

## Domain rules (important)

- **Smallest unit:** stock is always stored in the item's smallest unit (e.g. bags of lids). A code can represent several units (1 box = 12 bags).
- **Codes:** our own codes start with `INV-` (e.g. `INV-LID12-1BAG`, `INV-LID12-1BOX`, `INV-LID12-RESTOCK-BOX`). Staff badges start with `STAFF-`. `INV-UNDO` reverses that station's last scan; a second `INV-UNDO` right after does nothing. Supplier UPC barcodes are also allowed and map to an item. Ignore any other scanned code.
- **Stock (Phase 1):** each item has a single `stock` number. A scan adds or deducts the code's quantity.
- **Batches (Phase 2, not built yet):** items with expiry (dairy, ice cream) are tracked per batch with `expires_on`. Deductions are FIFO: oldest expiry first. Items without expiry use a single batch.
- **Scan log is append-only:** every scan, restock, undo and manual correction is a log entry. Undo creates a reversing entry, never deletes.
- **Idempotency:** each scan carries `station` + `scanned_at`; the API must ignore duplicates (offline queue may resend).
- **Never go negative:** if a deduction exceeds stock, record it, clamp stock at 0 and flag the item for a count (`needs_count`).
- **Reorder point** = avg daily usage × supplier lead time + safety stock. Math lives in `/backend/core`, never in the AI agent.

## Data model (DynamoDB, single table or a few tables — keep it simple)

| Entity | Key fields | Other fields |
| --- | --- | --- |
| Item | `item_id` | name, category, smallest_unit, stock, needs_count, reorder_point, lead_time_days (has_expiry: Phase 2) |
| Code | `code` | item_id, quantity (signed, smallest units: negative for `take`, positive for `restock`), action (`take` / `restock`) |
| Batch (Phase 2) | `item_id`, `batch_id` | quantity, received_on, expires_on |
| ScanLog | `item_id`, `timestamp#id` | code, change, shortfall, staff, station, action (`take` / `restock` / `undo` / `correction`), undoes |
| Staff | `staff_code` | name, active |

Start with separate tables if single-table design slows progress.

ScanLog notes: `id` is `station#scanned_at` (UTC), which is also the duplicate check key. `change` is what was actually applied to stock. `shortfall` = units asked for but not in stock (sets the item's `needs_count`). `undoes` = id of the entry an undo reverses.

## API (first version)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/scan` | API key | Body: `{code, action: "scan" or "undo", staff, station, scanned_at}` |
| GET | `/items` | Cognito | Items with current stock and status |
| POST | `/items` | Cognito | Create item |
| POST | `/codes` | Cognito | Create code for an item |
| POST | `/batches` | Cognito | Restock with quantity and expiry |
| GET | `/log` | Cognito | Recent activity, filterable by item |
| POST | `/corrections` | Cognito | Manual count adjustment |

## Phases and tasks

### Phase 1 — Core API + scanner (target end of Nov 2026)
- [ ] CDK app skeleton with dev stage, budget alarm, tags
- [ ] DynamoDB tables for Item, Code, ScanLog, Staff (Batch table comes in Phase 2)
- [x] `/backend/core` inventory logic with pytest tests (units, undo, never negative, duplicates)
- [ ] `POST /scan` Lambda + API Gateway with API key and usage plan
- [ ] Seed script with sample items and codes
- [ ] Point `scanner_sim.py` at the real API (`API_URL`, `API_KEY`)
- [ ] Script to print the door scan sheet (QR codes for each `INV-` code)

### Phase 2 — Batches and expiry (target Dec 20)
- [ ] `POST /batches` restock with expiry
- [ ] FIFO deduction across batches (a first version with tests is in git commit `a4f068a`)
- [ ] EventBridge daily job: expiring in 3 days + below reorder point → SNS email

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
- Python env is managed with `uv` (Python 3.12, same as Lambda): `uv sync`, `uv run pytest`, `uv run ruff check . && uv run ruff format .`
- Business logic in `/backend/core` as pure functions; handlers only parse input, call core, write to DynamoDB.
- Config via environment variables; never hard-code ARNs, keys or table names.
- Never commit secrets. `.env` is git-ignored.
- Least-privilege IAM: each Lambda gets only the tables and actions it needs.
- Every new endpoint gets a test and a line in the API table above.

## Cost guardrails

- AWS Free Plan: credits, account closes after 6 months unless upgraded. Upgrade to Paid plan before April 2027.
- Budget alert at $5–10. Serverless only. Delete unused stacks with `cdk destroy`.
- Bedrock and SMS cost money per use; use SNS email during development.

## How to work with me (Claude)

- Work on one task from the current phase at a time; propose a short plan before large changes.
- Write or update tests with each change and run them.
- When a task is done, tick it in this file and update "Current status".
- Ask before adding new AWS services or dependencies.
