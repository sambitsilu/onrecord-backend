# API overview

Base path `/v1`. Send `Authorization: Bearer <ID token>` (or `dev:<email>` in local mode).
Errors look like `{"error": {"code": "...", "message": "..."}}`. Full interactive docs at `/docs` (not in production).

## Organizations
| Method | Path | |
|---|---|---|
| GET | `/orgs` | Your organizations and role |
| POST | `/orgs` | Create one (you become owner) |
| GET / POST | `/orgs/{org}/members` | List / invite by email (owner) |
| DELETE | `/orgs/{org}/members/{id}` | Remove (owner; can't remove the last owner) |
| GET | `/orgs/{org}/audit/verify` | Check the activity log hasn't been tampered with |
| DELETE | `/orgs/{org}` | Delete the organization and all data (owner) |

## Clients and calls
| Method | Path | |
|---|---|---|
| GET / POST | `/orgs/{org}/clients` | List / add (name, people on client's side, scope of work) |
| GET / PATCH / DELETE | `/orgs/{org}/clients/{id}` | Read / update / erase (owner) |
| GET / POST | `/orgs/{org}/clients/{id}/calls` | List / add a transcript (`consent` must be true; `extract` starts reading) |
| GET / DELETE | `/orgs/{org}/calls/{id}` | Read / delete (records it replaced become current again) |
| GET | `/orgs/{org}/calls/{id}/lines` | Transcript lines |
| POST | `/orgs/{org}/calls/{id}/extract` | Read or re-read (reviewed records kept) → run |
| GET | `/orgs/{org}/runs/{id}` | Run status: queued, running, succeeded, failed |
| POST | `/orgs/{org}/calls/{id}/recap` | Draft a recap email from accepted records |

## Records and review
| Method | Path | |
|---|---|---|
| GET | `/orgs/{org}/clients/{id}/records?state=pending|accepted|all` | Records with `needs_check` and reasons |
| POST | `/orgs/{org}/clients/{id}/records` | Add a record the AI missed |
| POST | `/orgs/{org}/clients/{id}/records/accept` | Accept many; flagged ones are skipped |
| POST | `/orgs/{org}/records/{id}/accept` | Accept one (`confirm_flagged` needed if flagged) |
| PATCH | `/orgs/{org}/records/{id}` | Edit topic, value, text, due date, done |
| POST | `/orgs/{org}/records/{id}/discard` | Discard an unreviewed finding |
| POST | `/orgs/{org}/records/{id}/replaced` / `restore` | Mark replaced / current |
| DELETE | `/orgs/{org}/records/{id}` | Delete an accepted record |
| POST | `/orgs/{org}/clients/{id}/topics/merge` | Merge two topic names |

## Views
| Method | Path | |
|---|---|---|
| GET | `/orgs/{org}/clients/{id}/brief` | Current agreements by topic, with history |
| POST | `/orgs/{org}/clients/{id}/ask` | Question → answer with sources |
| GET | `/orgs/{org}/clients/{id}/quality` | Discard rate, edit rate, misses, flags |
| GET | `/orgs/{org}/clients/{id}/audit` | Activity log for the client |
| GET | `/orgs/{org}/clients/{id}/export` | Full record as Markdown (dispute pack) |
