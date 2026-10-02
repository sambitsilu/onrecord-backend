# Security and data protection notes

## Who can see what
- Every API route is under `/v1/orgs/{org_id}/…` and checks the caller is a member of that
  organization. Records, calls and clients are always looked up together with the org id,
  so a guessed id from another agency returns 404. `tests/test_api.py::test_isolation_between_orgs`
  covers this.
- Roles: **owner** (manage members, erase a client, delete the organization) and **member**
  (everything else).
- Production sign-in verifies Google Identity Platform ID tokens (issuer and audience checked).
  `AUTH_MODE=dev` exists only for local testing; the app refuses to start with it when `ENV=production`.

## Evidence integrity
- `transcript_lines` cannot be updated (database trigger). Lines are deleted only with their call.
- A call's fingerprint (SHA-256 of its parsed transcript), date, consent and owner cannot be changed (trigger).
- `audit_events` cannot be updated or deleted (trigger), except inside an erasure transaction,
  which may only blank the content fields.
- Each audit event is hash-chained to the previous one. `GET /v1/orgs/{org}/audit/verify` walks the
  chain. A test disables the trigger as the database owner, edits history, and the check catches it.
- The app should connect as the limited role in `sql/app_role.sql`: it cannot alter tables, disable
  triggers or truncate. Only migrations run as the owner.
- For stronger guarantees later: periodically copy the latest chain hash somewhere outside your
  control (e.g. emailed to the agency, or a write-once storage bucket with retention lock).

## AI safety
- Transcripts, scope documents and records are passed to the model inside tags, with an instruction
  to treat them as data. The eval includes a hidden-instruction case.
- Nothing the AI produces reaches the record without a person accepting it; nothing is ever sent to a client.
- Every finding is checked in code (`app/evidence.py`). Flagged findings need individual confirmation.
- Model is pinned (`LLM_MODEL`). Change it only after running `python -m eval.run_eval`.

## Personal data (India DPDP Act and similar laws)
- Recording consent must be confirmed when a call is added; it is stored with the call and can't be changed.
- Erasure: `DELETE /v1/orgs/{org}/clients/{id}` (owner) removes the client, calls, transcripts and
  records, and redacts its log entries. `DELETE /v1/orgs/{org}` removes everything for an agency.
- Export: `GET …/clients/{id}/export` gives a complete copy of a client's record.
- Transcripts are sent to Anthropic for processing. Check the current terms on data retention and
  training for your account type, and say so in your privacy policy. Get legal review before selling.
- Backups (Cloud SQL automated backups) keep deleted data until they expire; state that retention period.

## Operational
- Secrets (API key, DB password) belong in a secret manager, never in the image or the repo.
- CORS is closed unless `ALLOWED_ORIGINS` is set; required in production.
- API docs (`/docs`) are disabled in production.
- Security headers are set on every response; HSTS in production.
- Transcripts are capped at 1 MB; AI spend is capped per organization per month.

## Known gaps
- No rate limiting per user yet (put Cloud Armor or an API gateway in front, or add middleware).
- Background runs use in-process tasks. If an instance is shut down mid-run, the run is marked failed
  on next start and can be rerun. For heavy use, move to Cloud Tasks.
- No email sending for invitations yet.
