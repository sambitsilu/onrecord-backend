# Onrecord

[![tests](https://github.com/sambitsilu/onrecord-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/sambitsilu/onrecord-backend/actions/workflows/ci.yml)

**A reviewed, evidence-backed record of what every client agreed to.**

> "We never agreed to that."

Every agency has heard it. Decisions get made on calls, the client changes their mind two
weeks later, someone remembers it differently, and the extra work quietly gets done for free.
Meeting note-takers summarize each call, but none of them answer the questions that actually
cost agencies money: *What is agreed right now? What changed, and when? Was that request in scope?*

Onrecord turns call transcripts into a **living project brief** that answers those questions,
with every line traceable to the exact moment it was said.

**Live demo:** [try the prototype with a sample client](https://claude.ai/artifact/TNfd5YC9yAnMBdyC8CbGxH)
(click "Try it with a sample client", no sign-up)

---

## How it works

```mermaid
flowchart LR
    A[Call transcript<br/>Meet · Zoom · Teams · notes] --> B[AI reads the call]
    B --> C{Checks done in code}
    C -->|quote found, numbers said,<br/>client approved it| D[Review queue]
    C -->|a check failed| E[Flagged:<br/>must be checked one by one]
    E --> D
    D -->|a person accepts| F[Living brief<br/>current value + history]
    F --> G[Recap email<br/>for the client to confirm]
    F --> H[Scope-creep flags]
    F --> I[Dispute pack export]
```

1. **Add a call.** Paste or upload the transcript. Recording consent is confirmed and stored.
2. **The AI finds what matters:** approvals, decisions, action items, change requests and open questions,
   each with the speaker, an exact quote and a timestamp. Relative dates ("by Wednesday") become real dates.
3. **Software checks the AI's work.** The quote must actually be in the transcript, numbers must have been
   said, and an "approval" must come from someone on the client's side. Anything that fails is flagged.
4. **A person reviews.** Nothing reaches the record without being accepted. Flagged findings can't be bulk-accepted.
5. **The brief updates itself.** "Launch date: **27 October** (changed from 20 October on 26 Sep)."
6. **Use the record:** draft a recap email the client confirms in writing, catch requests outside the
   signed scope before doing free work, ask questions across months of calls, or export a dispute pack.

## Product decisions worth noting

**The transcript is the evidence; the AI is only an index to it.** AI makes mistakes, and in a product
used to settle disagreements, one wrong record destroys trust. So every AI claim is checked against the
transcript in code, every record links to the exact line it came from, and a person approves everything.

**Built for "who agreed to what," not "what was discussed."** The core object is the *agreement*, with
a history of changes, rather than per-meeting notes. That's the gap note-takers leave open.

**History that can't be quietly rewritten.** Transcripts can't be edited; the activity log is append-only,
enforced by the database, and hash-chained, so even someone with database-owner rights can't alter it
without detection. Deleting a client erases its content while the log stays verifiable.

**Accuracy is measured, not assumed.** An evaluation suite of test calls with known right answers covers
hedging ("maybe, not yet" is not an approval), clients changing their minds mid-call, changes to earlier
agreements, scope creep, Hinglish, hidden instructions inside a transcript, and who actually agreed.
The model is pinned and only changed after re-running the suite.

**Made for Indian agencies too.** Hinglish calls are part of the test suite, transcripts in Indian
scripts are parsed correctly, and the deployment plan keeps client data in India (Mumbai region).

## What's in this repository

The production backend: Python (FastAPI) + PostgreSQL, designed for Google Cloud Run and Cloud SQL.

| | |
|---|---|
| Teams and access | Agencies with owners and members; Google sign-in; tested isolation between agencies |
| AI pipeline | Claude via the Anthropic API, structured output, long calls read in parts, failures recorded |
| Review and brief | Accept, edit (AI's original kept), discard, add what the AI missed, merge topics |
| Evidence protection | Database triggers, hash-chained audit log, limited database role, erasure that keeps the log valid |
| Quality and cost | Discard/edit/miss rates per client; monthly AI budget per agency |
| Tests | 28 automated tests against a real PostgreSQL, run on every push (badge above) |

## Status

A working prototype (the live demo) and a tested backend. Not yet deployed or used with real clients.
Next: run the accuracy suite with real model calls, connect the web app to this backend, add audio
upload with transcription for phone calls, and pilot it on my own agency's client calls.

## How it was built

I run a small web agency, and this started from a problem I see in client work. I defined the product,
made the product and architecture decisions, set the quality bar, and directed and tested the build.
Much of the code was written with Claude (Anthropic) as an AI pair-programmer. The idea was inspired by
*Vox*, a meeting-memory project from a lablab.ai hackathon; Onrecord is an independent implementation
focused on client agreements.

---

# Technical guide

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
