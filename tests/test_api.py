import pytest
from sqlalchemy import text

from app.config import get_settings
from app.llm import LLMError

from .conftest import auth

A = auth("owner@vynedigital.com")
B = auth("teammate@vynedigital.com")
X = auth("outsider@otheragency.com")

CALL1 = """[00:01:40] Meera: Let's go with the sage green background. That's approved.
[00:02:31] Meera: Yes, five product pages.
[00:04:02] Meera: Razorpay is fine.
[00:05:33] Meera: I'll send the photos by Wednesday.
[00:07:52] Arjun: Yes, 20 October works for launch."""

CALL2 = """[00:01:55] Meera: Instead of the green background, I want a short video loop.
[00:04:10] Meera: Also, can you add a blog section?
[00:06:12] Arjun: Yes, let's move the launch to 27 October."""

ITEMS1 = {"summary": "Hero and launch agreed.", "items": [
    {"type": "approval", "topic": "Homepage hero", "value": "Sage green background", "text": "Sage green hero approved",
     "who": "Meera", "line": 0, "quote": "Let's go with the sage green background. That's approved."},
    {"type": "decision", "topic": "Product pages", "value": "7 pages", "text": "Seven product pages", "who": "Meera",
     "line": 1, "quote": "Yes, five product pages."},
    {"type": "decision", "topic": "Payment gateway", "value": "Razorpay", "text": "Use Razorpay", "who": "Meera",
     "line": 2, "quote": "Razorpay is the best, we signed yesterday"},
    {"type": "action", "topic": "Photos", "value": "by Wednesday", "text": "Meera sends photos", "owner": "Meera",
     "due": "by Wednesday", "dueDate": "2026-09-16", "line": 3, "quote": "I'll send the photos by Wednesday."},
    {"type": "decision", "topic": "Launch date", "value": "20 October", "text": "Launch on 20 October", "who": "Arjun",
     "line": 4, "quote": "Yes, 20 October works for launch."},
]}


def setup_org(client, llm):
    org = client.post("/v1/orgs", json={"name": "Vyne Digital"}, headers=A).json()
    c = client.post(f"/v1/orgs/{org['id']}/clients", json={"name": "Lotus Ayurveda", "people": "Meera",
                    "sow": "Included: homepage, 5 product pages, Razorpay.\nNot included: blog."}, headers=A).json()
    return org["id"], c["id"]


def add_call(client, org, cid, title, date, transcript, headers=A, extract=True):
    r = client.post(f"/v1/orgs/{org}/clients/{cid}/calls", json={"title": title, "call_date": date, "transcript": transcript,
                    "consent": True, "extract": extract}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def records(client, org, cid, state="all"):
    return client.get(f"/v1/orgs/{org}/clients/{cid}/records?state={state}", headers=A).json()


def by_topic(recs, topic):
    return next(r for r in recs if r["topic"] == topic)


def test_consent_required(client, llm):
    org, cid = setup_org(client, llm)
    r = client.post(f"/v1/orgs/{org}/clients/{cid}/calls", json={"title": "x", "call_date": "2026-09-12",
                    "transcript": "A: hi", "consent": False}, headers=A)
    assert r.status_code == 422


def test_extraction_flags_and_review(client, llm):
    org, cid = setup_org(client, llm)
    llm.on("Homepage review", ITEMS1)
    out = add_call(client, org, cid, "Homepage review", "2026-09-12", CALL1)
    run = client.get(f"/v1/orgs/{org}/runs/{out['run']['id']}", headers=A).json()
    assert run["status"] == "succeeded" and run["created_count"] == 5 and run["flagged_count"] == 2
    recs = records(client, org, cid, "pending")
    assert {r["topic"]: r["needs_check"] for r in recs} == {
        "Homepage hero": False, "Product pages": True, "Payment gateway": True, "Photos": False, "Launch date": False}
    assert by_topic(recs, "Photos")["due_date"] == "2026-09-16"
    assert by_topic(recs, "Homepage hero")["t"] == "00:01:40"

    # Batch accept skips flagged records.
    res = client.post(f"/v1/orgs/{org}/clients/{cid}/records/accept", json={"ids": [r["id"] for r in recs]}, headers=A).json()
    assert len(res["accepted"]) == 3 and len(res["skipped_need_check"]) == 2

    flagged = by_topic(recs, "Payment gateway")
    r = client.post(f"/v1/orgs/{org}/records/{flagged['id']}/accept", json={}, headers=A)
    assert r.status_code == 409 and r.json()["error"]["code"] == "needs_confirmation"
    assert client.post(f"/v1/orgs/{org}/records/{flagged['id']}/accept", json={"confirm_flagged": True}, headers=A).status_code == 200
    # Discard the invented "7 pages"
    seven = by_topic(recs, "Product pages")
    assert client.post(f"/v1/orgs/{org}/records/{seven['id']}/discard", headers=A).status_code == 204

    q = client.get(f"/v1/orgs/{org}/clients/{cid}/quality", headers=A).json()
    assert q["discarded"] == 1 and q["flagged_then_discarded"] == 1 and q["accepted_unchanged"] == 4 and q["pending"] == 0


def test_changes_link_and_brief(client, llm):
    org, cid = setup_org(client, llm)
    llm.on("Homepage review", ITEMS1)
    add_call(client, org, cid, "Homepage review", "2026-09-12", CALL1)
    recs = records(client, org, cid)
    for r in recs:
        client.post(f"/v1/orgs/{org}/records/{r['id']}/accept", json={"confirm_flagged": True}, headers=A)
    hero, launch = by_topic(recs, "Homepage hero"), by_topic(recs, "Launch date")
    gateway = by_topic(recs, "Payment gateway")
    llm.on("Mockup feedback", {"summary": "Changes.", "items": [
        {"type": "decision", "topic": "Homepage hero", "value": "Video loop", "text": "Hero becomes a video loop", "who": "Meera",
         "line": 0, "quote": "I want a short video loop.", "replaces": [hero["id"]]},
        # The AI wrongly tries to replace the payment gateway from a launch-date change: topic guard drops it.
        {"type": "decision", "topic": "Launch date", "value": "27 October", "text": "Launch moved to 27 October", "who": "Arjun",
         "line": 2, "quote": "let's move the launch to 27 October.", "replaces": [launch["id"], gateway["id"]]},
        {"type": "scope_change", "topic": "Blog", "value": "Blog section", "text": "Client asks for a blog", "who": "Meera",
         "line": 1, "quote": "can you add a blog section?", "outsideScope": True, "scopeNote": "Blog not included"},
    ]})
    add_call(client, org, cid, "Mockup feedback", "2026-09-26", CALL2)
    prompt = llm.prompts[-1]
    assert hero["id"] in prompt and "<transcript>" in prompt and "DATA" not in prompt.split("<transcript>")[1]
    new = records(client, org, cid, "pending")
    lnch = by_topic(new, "Launch date")
    assert lnch["replaces"] == [launch["id"]]  # gateway link dropped by the topic guard
    res = client.post(f"/v1/orgs/{org}/clients/{cid}/records/accept", json={"ids": [r["id"] for r in new]}, headers=A).json()
    assert len(res["accepted"]) == 3
    b = client.get(f"/v1/orgs/{org}/clients/{cid}/brief", headers=A).json()
    terms = {t["topic"]: t for t in b["terms"]}
    assert [e["value"] for e in terms["Launch date"]["current"]] == ["27 October"]
    assert [e["value"] for e in terms["Launch date"]["history"]] == ["20 October"]
    assert [e["value"] for e in terms["Homepage hero"]["history"]] == ["Sage green background"]
    assert terms["Payment gateway"]["history"] == []
    assert b["outside_scope"] == 1 and b["pending_review"] == 0

    # Deleting the second call brings the old agreements back.
    second = [c for c in client.get(f"/v1/orgs/{org}/clients/{cid}/calls", headers=A).json() if c["title"] == "Mockup feedback"][0]
    assert client.delete(f"/v1/orgs/{org}/calls/{second['id']}", headers=A).status_code == 204
    b = client.get(f"/v1/orgs/{org}/clients/{cid}/brief", headers=A).json()
    terms = {t["topic"]: t for t in b["terms"]}
    assert [e["value"] for e in terms["Launch date"]["current"]] == ["20 October"]


def test_reextract_keeps_reviewed_and_edits_keep_original(client, llm):
    org, cid = setup_org(client, llm)
    llm.on("Homepage review", ITEMS1)
    out = add_call(client, org, cid, "Homepage review", "2026-09-12", CALL1)
    recs = records(client, org, cid)
    hero = by_topic(recs, "Homepage hero")
    client.post(f"/v1/orgs/{org}/records/{hero['id']}/accept", json={}, headers=A)
    r = client.patch(f"/v1/orgs/{org}/records/{hero['id']}", json={"value": "Sage green"}, headers=A).json()
    assert r["value"] == "Sage green" and r["original"]["value"] == "Sage green background"
    r = client.post(f"/v1/orgs/{org}/calls/{out['call']['id']}/extract", headers=A)
    assert r.status_code == 202
    recs2 = records(client, org, cid)
    assert len(recs2) == 5  # reviewed hero kept, 4 pending redone; duplicate hero (same type+line) skipped
    assert by_topic(recs2, "Homepage hero")["id"] == hero["id"]
    q = client.get(f"/v1/orgs/{org}/clients/{cid}/quality", headers=A).json()
    assert q["accepted_after_edit"] == 1 and q["edit_rate"] == 1.0


def test_isolation_between_orgs(client, llm):
    org, cid = setup_org(client, llm)
    llm.on("Homepage review", ITEMS1)
    add_call(client, org, cid, "Homepage review", "2026-09-12", CALL1)
    rec = records(client, org, cid)[0]
    assert client.get(f"/v1/orgs/{org}/clients", headers=X).status_code == 404
    other = client.post("/v1/orgs", json={"name": "Other"}, headers=X).json()["id"]
    # Using their own org id with our record id must not work either.
    assert client.post(f"/v1/orgs/{other}/records/{rec['id']}/accept", json={}, headers=X).status_code == 404
    assert client.get(f"/v1/orgs/{other}/clients/{cid}/brief", headers=X).status_code == 404
    assert client.get(f"/v1/orgs/{other}/clients/{cid}/export", headers=X).status_code == 404


def test_invites_and_roles(client, llm):
    org, cid = setup_org(client, llm)
    assert client.post(f"/v1/orgs/{org}/members", json={"email": "teammate@vynedigital.com"}, headers=A).status_code == 201
    orgs = client.get("/v1/orgs", headers=B).json()
    assert orgs[0]["id"] == org and orgs[0]["role"] == "member"
    assert client.get(f"/v1/orgs/{org}/clients/{cid}", headers=B).status_code == 200
    assert client.delete(f"/v1/orgs/{org}/clients/{cid}", headers=B).status_code == 403
    assert client.post(f"/v1/orgs/{org}/members", json={"email": "x@vynedigital.com"}, headers=B).status_code == 403


def test_audit_chain_and_guards(client, llm, engine):
    org, cid = setup_org(client, llm)
    llm.on("Homepage review", ITEMS1)
    add_call(client, org, cid, "Homepage review", "2026-09-12", CALL1)
    chain = client.get(f"/v1/orgs/{org}/audit/verify", headers=A).json()
    assert chain["ok"] and chain["events"] >= 4
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as c:
            c.execute(text("UPDATE audit_events SET note = 'tampered'"))
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM audit_events"))
    with pytest.raises(Exception, match="cannot be edited"):
        with engine.begin() as c:
            c.execute(text("UPDATE transcript_lines SET text = 'changed'"))
    with pytest.raises(Exception, match="fingerprint"):
        with engine.begin() as c:
            c.execute(text("UPDATE calls SET sha256 = 'x'"))
    # Someone with database owner rights disables the guard and edits history: the chain catches it.
    with engine.begin() as c:
        c.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_guard"))
        c.execute(text("UPDATE audit_events SET note = 'tampered' WHERE id = 2"))
        c.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_guard"))
    chain = client.get(f"/v1/orgs/{org}/audit/verify", headers=A).json()
    assert not chain["ok"] and "event 2" in chain["problem"]


def test_erasure_redacts_and_chain_survives(client, llm):
    org, cid = setup_org(client, llm)
    llm.on("Homepage review", ITEMS1)
    add_call(client, org, cid, "Homepage review", "2026-09-12", CALL1)
    assert client.delete(f"/v1/orgs/{org}/clients/{cid}", headers=A).status_code == 204
    assert client.get(f"/v1/orgs/{org}/clients/{cid}", headers=A).status_code == 404
    chain = client.get(f"/v1/orgs/{org}/audit/verify", headers=A).json()
    assert chain["ok"], chain


def test_ask_full_and_retrieval(client, llm):
    org, cid = setup_org(client, llm)
    add_call(client, org, cid, "Homepage review", "2026-09-12", CALL1, extract=False)
    call_id = client.get(f"/v1/orgs/{org}/clients/{cid}/calls", headers=A).json()[0]["id"]
    llm.on("QUESTION:", {"answer": "Launch is 20 October [1].", "sources": [{"n": 1, "call": call_id, "line": 4}, {"n": 2, "call": "bogus", "line": 1}]})
    r = client.post(f"/v1/orgs/{org}/clients/{cid}/ask", json={"question": "When is the launch?"}, headers=A).json()
    assert r["searched_excerpts_only"] is False and len(r["sources"]) == 1 and r["sources"][0]["t"] == "00:07:52"
    s = get_settings()
    old = s.ask_full_corpus_chars
    s.ask_full_corpus_chars = 10
    try:
        r = client.post(f"/v1/orgs/{org}/clients/{cid}/ask", json={"question": "When is the launch date?"}, headers=A).json()
        assert r["searched_excerpts_only"] is True
        assert "20 October works for launch" in llm.prompts[-1] and "sage green" not in llm.prompts[-1]
    finally:
        s.ask_full_corpus_chars = old


def test_recap_needs_accepted_records(client, llm):
    org, cid = setup_org(client, llm)
    llm.on("Homepage review", ITEMS1)
    out = add_call(client, org, cid, "Homepage review", "2026-09-12", CALL1)
    call_id = out["call"]["id"]
    assert client.post(f"/v1/orgs/{org}/calls/{call_id}/recap", json={}, headers=A).status_code == 409
    rec = by_topic(records(client, org, cid), "Homepage hero")
    client.post(f"/v1/orgs/{org}/records/{rec['id']}/accept", json={}, headers=A)
    llm.on("Sender name", "Subject: Recap of our homepage call\n\nHi Meera,\nThanks for your time.")
    r = client.post(f"/v1/orgs/{org}/calls/{call_id}/recap", json={"signer": "Arjun"}, headers=A).json()
    assert r["subject"] == "Recap of our homepage call" and r["body"].startswith("Hi Meera") and r["left_out_pending"] == 4


def test_budget_and_failures(client, llm, engine):
    org, cid = setup_org(client, llm)
    llm.on("Homepage review", "this is not json")
    out = add_call(client, org, cid, "Homepage review", "2026-09-12", CALL1)
    run = client.get(f"/v1/orgs/{org}/runs/{out['run']['id']}", headers=A).json()
    assert run["status"] == "failed" and run["error"].startswith("invalid_json")
    assert client.get(f"/v1/orgs/{org}/calls/{out['call']['id']}", headers=A).json()["status"] == "failed"
    llm.fail_with = LLMError("rate_limited", "busy")
    r = client.post(f"/v1/orgs/{org}/clients/{cid}/ask", json={"question": "launch?"}, headers=A)
    assert r.status_code == 429
    llm.fail_with = None
    # Tokens spent on the failed (invalid JSON) extraction still count toward the budget.
    with engine.begin() as c:
        spent = c.execute(text("SELECT sum(input_tokens + output_tokens) FROM llm_usage")).scalar_one()
        assert spent == 300  # first try + repair try
        c.execute(text("UPDATE organizations SET monthly_token_budget = 300"))
    r = client.post(f"/v1/orgs/{org}/clients/{cid}/ask", json={"question": "launch?"}, headers=A)
    assert r.status_code == 402 and r.json()["error"]["code"] == "budget_exceeded"


def test_manual_record_and_export(client, llm):
    org, cid = setup_org(client, llm)
    llm.on("Homepage review", ITEMS1)
    out = add_call(client, org, cid, "Homepage review", "2026-09-12", CALL1)
    for r in records(client, org, cid):
        client.post(f"/v1/orgs/{org}/records/{r['id']}/accept", json={"confirm_flagged": True}, headers=A)
    m = client.post(f"/v1/orgs/{org}/clients/{cid}/records", json={"call_id": out["call"]["id"], "type": "decision",
                    "topic": "Product pages", "value": "5 pages", "text": "Five product pages", "line_idx": 1,
                    "quote": "five product pages"}, headers=A).json()
    assert m["source"] == "manual" and m["verified"] is True and m["reviewed"] is True
    assert client.get(f"/v1/orgs/{org}/clients/{cid}/quality", headers=A).json()["manual_additions"] == 1
    md = client.get(f"/v1/orgs/{org}/clients/{cid}/export", headers=A).text
    assert out["call"]["sha256"] in md and "quote NOT verified" in md and "Sage green background" in md
    assert "Added a record the AI missed" in md
