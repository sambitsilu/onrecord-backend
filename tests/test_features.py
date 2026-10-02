"""Client confirmation, change orders and audio transcription."""
import os
import re

import pytest
from sqlalchemy import text

from app.change_orders import money
from app.config import get_settings
from app.transcription import TranscriptionError
from app.transcripts import Line

from .conftest import auth
from .test_api import CALL1, ITEMS1, add_call, by_topic, records, setup_org

A = auth("owner@vynedigital.com")
B = auth("teammate@vynedigital.com")


def accepted_call(client, llm):
    org, cid = setup_org(client, llm)
    llm.on("Homepage review", ITEMS1)
    out = add_call(client, org, cid, "Homepage review", "2026-09-12", CALL1)
    for r in records(client, org, cid):
        client.post(f"/v1/orgs/{org}/records/{r['id']}/accept", json={"confirm_flagged": True}, headers=A)
    return org, cid, out["call"]["id"]


def token_of(link):
    return link.rsplit("/", 1)[1]


# --- client confirmation ---------------------------------------------------------------

def test_confirmation_flow(client, llm):
    org, cid, call_id = accepted_call(client, llm)
    hero = by_topic(records(client, org, cid), "Homepage hero")
    client.patch(f"/v1/orgs/{org}/records/{hero['id']}", json={"text": "Sage green hero <script>alert(1)</script>"}, headers=A)
    r = client.post(f"/v1/orgs/{org}/calls/{call_id}/confirmation", headers=A).json()
    assert r["items"] == 5  # 1 approval, 3 decisions, 1 action item
    tok = token_of(r["link"])
    page = client.get(f"/confirm/{tok}")
    assert page.status_code == 200 and "Please confirm what we agreed" in page.text
    assert "<script>alert(1)</script>" not in page.text and "&lt;script&gt;" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"] and "noindex" in page.headers["x-robots-tag"]
    ids = re.findall(r'name="r_([0-9a-f-]{36})" value="confirmed"', page.text)
    assert len(ids) == 5

    # Missing answers and a dispute without a reason are sent back with a message, nothing saved.
    bad = client.post(f"/confirm/{tok}", data={"name": "Meera", f"r_{ids[0]}": "confirmed"})
    assert "Please confirm or flag every item" in bad.text
    form = {"name": "Meera Kapoor", **{f"r_{i}": "confirmed" for i in ids}}
    form[f"r_{ids[1]}"] = "disputed"
    assert "say what" in client.post(f"/confirm/{tok}", data=form).text
    form[f"c_{ids[1]}"] = "We said 5 pages, not 7"
    done = client.post(f"/confirm/{tok}", data=form)
    assert "flagged 1" in done.text

    recs = {r["id"]: r for r in records(client, org, cid)}
    assert recs[ids[1]]["client_confirmation"] == "disputed" and recs[ids[1]]["client_comment"] == "We said 5 pages, not 7"
    assert recs[ids[0]]["client_confirmation"] == "confirmed"
    brief = client.get(f"/v1/orgs/{org}/clients/{cid}/brief", headers=A).json()
    assert brief["disputed_by_client"] == 1
    assert "already responded" in client.get(f"/confirm/{tok}").text
    assert client.post(f"/v1/public/confirmations/{tok}", json={"name": "x", "answers": [
        {"record_id": ids[0], "response": "confirmed"}]}).status_code == 409
    md = client.get(f"/v1/orgs/{org}/clients/{cid}/export", headers=A).text
    assert "DISPUTED by the client" in md and "We said 5 pages, not 7" in md
    assert client.get(f"/v1/orgs/{org}/audit/verify", headers=A).json()["ok"]


def test_confirmation_links_replace_and_expire(client, llm, engine):
    org, cid, call_id = accepted_call(client, llm)
    first = token_of(client.post(f"/v1/orgs/{org}/calls/{call_id}/confirmation", headers=A).json()["link"])
    second = token_of(client.post(f"/v1/orgs/{org}/calls/{call_id}/confirmation", headers=A).json()["link"])
    assert client.get(f"/confirm/{first}").status_code == 404
    assert client.get(f"/confirm/does-not-exist").status_code == 404
    with engine.begin() as c:
        c.execute(text("UPDATE confirmation_requests SET expires_at = now() - interval '1 day'"))
    assert client.get(f"/confirm/{second}").status_code == 410


def test_recap_includes_confirmation_link(client, llm):
    org, cid, call_id = accepted_call(client, llm)
    llm.on("Sender name", "Subject: Recap\n\nHi Meera,\nThanks.")
    r = client.post(f"/v1/orgs/{org}/calls/{call_id}/recap", json={}, headers=A).json()
    assert r["confirm_link"] and r["confirm_link"] in r["body"]
    assert client.get(f"/confirm/{token_of(r['confirm_link'])}").status_code == 200


# --- change orders ---------------------------------------------------------------------------

def test_money_formatting():
    assert money(1415999, "INR") == "₹14,159.99"
    assert money(12345678900, "INR") == "₹12,34,56,789.00"
    assert money(99, "INR") == "₹0.99"
    assert money(123456, "USD") == "USD 1,234.56"


def test_change_order_flow(client, llm, engine):
    org, cid, call_id = accepted_call(client, llm)
    assert client.post(f"/v1/orgs/{org}/members", json={"email": "teammate@vynedigital.com"}, headers=A).status_code == 201
    assert client.post(f"/v1/orgs/{org}/rate-items", json={"name": "x", "unit_price": 1}, headers=B).status_code == 403
    ri = client.post(f"/v1/orgs/{org}/rate-items", json={"name": "Blog page design", "unit": "page", "unit_price": 450000}, headers=A).json()
    rec = by_topic(records(client, org, cid), "Photos")
    co = client.post(f"/v1/orgs/{org}/clients/{cid}/change-orders", headers=A, json={
        "title": "Blog section", "notes": "Requested on our 26 Sep call.",
        "lines": [{"rate_item_id": ri["id"], "quantity": "2", "record_id": rec["id"]},
                  {"description": "Blog setup and categories", "quantity": "1.5", "unit": "hour", "unit_price": 199999}]}).json()
    assert co["status"] == "draft" and co["number"] == 1 and co["lines"][0]["description"] == "Blog page design"
    assert (co["subtotal"], co["tax"], co["total"]) == (1199999, 216000, 1415999) and co["total_text"] == "₹14,159.99"

    co = client.patch(f"/v1/orgs/{org}/change-orders/{co['id']}", json={"tax_percent": "0"}, headers=A).json()
    assert co["total"] == 1199999
    co = client.patch(f"/v1/orgs/{org}/change-orders/{co['id']}", json={"tax_percent": "18"}, headers=A).json()
    sent = client.post(f"/v1/orgs/{org}/change-orders/{co['id']}/send", headers=A).json()
    assert sent["status"] == "sent" and sent["link"]
    assert client.patch(f"/v1/orgs/{org}/change-orders/{co['id']}", json={"title": "x"}, headers=A).status_code == 409
    with pytest.raises(Exception, match="cannot be edited"):
        with engine.begin() as c:
            c.execute(text("UPDATE change_orders SET total = 1"))
    with pytest.raises(Exception, match="cannot be changed"):
        with engine.begin() as c:
            c.execute(text("UPDATE change_order_lines SET unit_price = 1"))

    tok = token_of(sent["link"])
    page = client.get(f"/change-order/{tok}")
    assert "₹14,159.99" in page.text and "Blog setup and categories" in page.text
    assert "Please choose" in client.post(f"/change-order/{tok}", data={"name": "Meera"}).text
    done = client.post(f"/change-order/{tok}", data={"name": "Meera Kapoor", "decision": "approve", "comment": "Go ahead"})
    assert "approved" in done.text
    assert "approved this change order" in client.get(f"/change-order/{tok}").text
    assert client.post(f"/v1/public/change-orders/{tok}", json={"name": "x", "decision": "decline"}).status_code == 409
    v = client.get(f"/v1/orgs/{org}/clients/{cid}/scope-value", headers=A).json()
    assert v["approved_total"] == 1415999 and v["approved_text"] == "₹14,159.99"
    md = client.get(f"/v1/orgs/{org}/clients/{cid}/export", headers=A).text
    assert "#1 Blog section: approved by Meera Kapoor" in md
    assert client.get(f"/v1/orgs/{org}/audit/verify", headers=A).json()["ok"]

    # A withdrawn change order's link stops working; erasing the client still works.
    co2 = client.post(f"/v1/orgs/{org}/clients/{cid}/change-orders", headers=A, json={
        "title": "Extra", "lines": [{"description": "Thing", "unit_price": 100}]}).json()
    link2 = client.post(f"/v1/orgs/{org}/change-orders/{co2['id']}/send", headers=A).json()["link"]
    client.post(f"/v1/orgs/{org}/change-orders/{co2['id']}/withdraw", headers=A)
    assert client.get(f"/change-order/{token_of(link2)}").status_code == 404
    assert client.delete(f"/v1/orgs/{org}/clients/{cid}", headers=A).status_code == 204


def test_change_order_validation(client, llm):
    org, cid = setup_org(client, llm)
    r = client.post(f"/v1/orgs/{org}/clients/{cid}/change-orders", headers=A, json={"title": "x", "lines": [{"description": "No price"}]})
    assert r.status_code == 422
    other = client.post("/v1/orgs", json={"name": "Other"}, headers=auth("x@otheragency.com")).json()["id"]
    ri = client.post(f"/v1/orgs/{other}/rate-items", json={"name": "Theirs", "unit_price": 5}, headers=auth("x@otheragency.com")).json()
    r = client.post(f"/v1/orgs/{org}/clients/{cid}/change-orders", headers=A, json={"title": "x", "lines": [{"rate_item_id": ri["id"]}]})
    assert r.status_code == 422  # can't use another agency's rate card


# --- audio -------------------------------------------------------------------------------------

class FakeTranscriber:
    name = "fake"

    def __init__(self):
        self.lines = [Line("00:00:05", "Speaker A", "Let's go with the sage green background. That's approved."),
                      Line("00:00:20", "Speaker B", "Great, I'll share mockups by Friday.")]
        self.error = None
        self.paths = []

    def transcribe(self, path, language=""):
        self.paths.append(path)
        if self.error:
            raise self.error
        return self.lines, 95


@pytest.fixture
def audio_client(engine, llm, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    s = get_settings()
    old = s.audio_tmp_dir
    s.audio_tmp_dir = str(tmp_path)
    fake = FakeTranscriber()
    app = create_app(llm_factory=lambda: llm, transcriber_factory=lambda: fake)
    with TestClient(app) as tc:
        tc.fake = fake
        yield tc
    s.audio_tmp_dir = old


def upload(c, org, cid, data=b"ID3fake-mp3-bytes", ctype="audio/mpeg", extract="false"):
    return c.post(f"/v1/orgs/{org}/clients/{cid}/audio", headers=A, files={"file": ("call.mp3", data, ctype)},
                  data={"title": "Phone call with Meera", "call_date": "2026-09-30", "consent": "true", "extract": extract})


def test_audio_upload_transcribe_and_name_speakers(audio_client, llm):
    c = audio_client
    org, cid = setup_org(c, llm)
    job = upload(c, org, cid)
    assert job.status_code == 202, job.text
    job = c.get(f"/v1/orgs/{org}/transcriptions/{job.json()['id']}", headers=A).json()
    assert job["status"] == "succeeded" and job["duration_sec"] == 95 and len(job["audio_sha256"]) == 64
    assert not os.path.exists(c.fake.paths[0])  # audio deleted after transcription
    call = c.get(f"/v1/orgs/{org}/calls/{job['call_id']}", headers=A).json()
    assert call["source"] == "audio" and call["line_count"] == 2
    assert c.put(f"/v1/orgs/{org}/calls/{call['id']}/speakers", json={"speakers": {"Speaker Z": "x"}}, headers=A).status_code == 422
    r = c.put(f"/v1/orgs/{org}/calls/{call['id']}/speakers", json={"speakers": {"Speaker A": "Meera", "Speaker B": "Arjun"}}, headers=A)
    assert r.json()["speaker_map"] == {"Speaker A": "Meera", "Speaker B": "Arjun"}
    lines = c.get(f"/v1/orgs/{org}/calls/{call['id']}/lines", headers=A).json()
    assert [ln["speaker"] for ln in lines] == ["Meera", "Arjun"]
    # The AI sees real names, so the "approval came from the client" check works.
    llm.on("Phone call with Meera", {"summary": "s", "items": [{"type": "approval", "topic": "Homepage hero", "value": "Sage green",
           "text": "Sage green approved", "who": "Meera", "line": 0, "quote": "That's approved."}]})
    c.post(f"/v1/orgs/{org}/calls/{call['id']}/extract", headers=A)
    assert "Meera: Let's go with the sage green" in llm.prompts[-1]
    rec = records(c, org, cid)[0]
    assert rec["role_ok"] is True and rec["verified"] is True
    md = c.get(f"/v1/orgs/{org}/clients/{cid}/export", headers=A).text
    assert "transcribed from an audio recording" in md and "Speaker A = Meera" in md


def test_audio_extract_immediately_and_errors(audio_client, llm):
    c = audio_client
    org, cid = setup_org(c, llm)
    llm.on("Phone call with Meera", {"summary": "s", "items": [{"type": "action", "topic": "Mockups", "value": "by Friday",
           "text": "Arjun shares mockups", "owner": "Speaker B", "line": 1, "quote": "I'll share mockups by Friday."}]})
    job = upload(c, org, cid, extract="true").json()
    job = c.get(f"/v1/orgs/{org}/transcriptions/{job['id']}", headers=A).json()
    assert job["status"] == "succeeded" and len(records(c, org, cid)) == 1

    assert upload(c, org, cid, ctype="application/pdf").status_code == 415
    s = get_settings()
    s.max_audio_bytes, old = 5, s.max_audio_bytes
    try:
        assert upload(c, org, cid).status_code == 413
    finally:
        s.max_audio_bytes = old
    c.fake.error = TranscriptionError("No speech was found in the recording.")
    job = upload(c, org, cid).json()
    job = c.get(f"/v1/orgs/{org}/transcriptions/{job['id']}", headers=A).json()
    assert job["status"] == "failed" and "No speech" in job["error"]
    assert not any(os.path.exists(p) for p in c.fake.paths)
    assert os.listdir(get_settings().audio_tmp_dir) == []


def test_audio_disabled_without_provider(client, llm):
    org, cid = setup_org(client, llm)
    r = upload(client, org, cid)
    assert r.status_code == 503 and r.json()["error"]["code"] == "misconfigured"
