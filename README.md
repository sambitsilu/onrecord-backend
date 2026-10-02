# Onrecord backend

The server for Onrecord: a record of what each client agreed to, built from call
transcripts, reviewed by people, and backed by evidence that can't be quietly changed.

Python (FastAPI) + PostgreSQL. Designed for Google Cloud Run + Cloud SQL, but runs
anywhere Docker runs. **Nothing in this folder deploys anything by itself.**

## What it does

- **Teams.** Agencies are organizations with owners and members; invitations by email.
  Sign-in with Google Identity Platform (Firebase). Every query is scoped to the
  organization; tests prove one agency can't reach another's data.
- **Reading calls.** Paste or upload a transcript (Meet, Zoom, Teams, VTT, SRT, plain
  notes). The AI finds approvals, decisions, action items, change requests and open
  questions. Long calls are read in parts. Runs in the background; failures are recorded.
- **Checks done in code, not by the AI.** Each quote must be found in the transcript,
  numbers must have been said, approvals must come from someone on the client's side.
  Failing records are flagged and can't be bulk-accepted.
- **Review.** Accept, edit (the AI's original wording is kept), discard, mark replaced,
  restore, delete, or add something the AI missed. Re-reading a call never touches
  reviewed records.
- **Brief.** The current value of every agreed topic, with its history of changes.
- **Ask, recap emails, quality stats, export (dispute pack).**
- **Evidence protection in the database itself.** Transcripts can't be edited; a call's
  fingerprint, date and consent can't be changed; the activity log is append-only *and*
  hash-chained, so tampering is detectable even by someone with database-owner rights.
  Deleting a client erases its content while the log stays verifiable.
- **Cost control.** A monthly AI token budget per organization (failed attempts count).
- **AI test suite** (`eval/`): 8 made-up calls with known right answers.

## Run it on your computer (no cloud needed)

You need Docker Desktop.

```bash
cp .env.example .env          # optionally add ANTHROPIC_API_KEY
docker compose up --build
```

Open http://localhost:8000/docs for interactive API docs. In local mode, authenticate by
clicking **Authorize** and entering `dev:you@yourdomain.com` (any email).

Without an API key everything works except reading calls, Ask and recaps, which return
a clear configuration error.

## Run the tests

Needs a PostgreSQL you can reach (the compose `db` service works).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
createdb onrecord_test   # or: docker compose exec db createdb -U onrecord onrecord_test
DATABASE_URL=postgresql+psycopg://onrecord:onrecord@localhost:5432/onrecord_test pytest -q
```

28 tests: transcript parsing, evidence checks, the full API flow, isolation between
agencies, roles, audit-log tamper detection, erasure, budgets, failure handling, the real
Anthropic client (network mocked), and the eval grader itself.

## Measure AI accuracy (do this before trusting it, and before changing model or prompt)

```bash
ANTHROPIC_API_KEY=... python -m eval.run_eval --repeat 3
```

Prints a score and writes a report to `eval/reports/`. Use `--min-score 0.9` in CI to
block changes that make extraction worse. Add your own anonymized real calls to
`eval/cases.json` over time; they are the most valuable tests you'll have.

## Layout

| Path | What's there |
|---|---|
| `app/transcripts.py` | Parsing transcripts, splitting long calls |
| `app/evidence.py` | Quote, number and approval checks |
| `app/extraction.py` | The AI pipeline (no database access, so the eval runs the same code) |
| `app/jobs.py` | Background runs that save extraction results |
| `app/services.py` | Review, brief, ask, recap, quality, export, erasure |
| `app/audit.py` | Append-only hash-chained activity log |
| `app/llm.py` | Anthropic client: pinned model, structured output, retries, budget |
| `app/auth.py` | Sign-in and organization access |
| `migrations/` | Database schema, including the protection triggers |
| `sql/app_role.sql` | Limited database user for the app |
| `eval/` | AI test calls, grader and runner |
| `docs/` | Security notes, API overview, GCP guide for later |

## Not done yet

- The Onrecord web screens still use the prototype's storage; they need connecting to this API.
- Audio upload and transcription (phone calls, voice notes, Hinglish).
- Email delivery of invitations (today an invite is accepted by signing in with that email).
- Privacy policy and terms (need a lawyer's review).
- Real accuracy numbers: run the eval with your API key.
