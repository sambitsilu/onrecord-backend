"""The real Anthropic wrapper, with the network call replaced."""
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.llm import AnthropicLLM, LLMError, complete_json, parse_json


def _msg(text, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def _bad_request():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError("bad", response=httpx.Response(400, request=req), body=None)


def make(monkeypatch, behaviour):
    llm = AnthropicLLM(api_key="test-key", model="claude-sonnet-5-5")
    calls = []

    def create(**kw):
        calls.append(kw)
        return behaviour(kw, len(calls))

    monkeypatch.setattr(llm._client.messages, "create", create)
    return llm, calls


def test_sends_schema_and_no_unsupported_params(monkeypatch):
    llm, calls = make(monkeypatch, lambda kw, n: _msg('{"a": 1}'))
    data, usage = complete_json(llm, "sys", "user", schema={"type": "object"})
    assert data == {"a": 1} and usage.input_tokens == 10
    assert calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in calls[0] and calls[0]["model"] == "claude-sonnet-5-5"


def test_falls_back_when_schema_rejected(monkeypatch):
    def behaviour(kw, n):
        if "output_config" in kw:
            raise _bad_request()
        return _msg('```json\n{"ok": true}\n```')
    llm, calls = make(monkeypatch, behaviour)
    assert complete_json(llm, "s", "u", schema={"type": "object"})[0] == {"ok": True}
    complete_json(llm, "s", "u", schema={"type": "object"})
    assert [("output_config" in c) for c in calls] == [True, False, False]  # remembers the fallback


def test_refusal_and_rate_limit(monkeypatch):
    llm, _ = make(monkeypatch, lambda kw, n: _msg("", stop="refusal"))
    with pytest.raises(LLMError) as e:
        llm.complete("s", "u", 10)
    assert e.value.code == "refused"

    def limited(kw, n):
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.RateLimitError("slow down", response=httpx.Response(429, request=req), body=None)
    llm, _ = make(monkeypatch, limited)
    with pytest.raises(LLMError) as e:
        llm.complete("s", "u", 10)
    assert e.value.code == "rate_limited"


def test_parse_json_variants():
    assert parse_json('Sure! {"x": 1} done') == {"x": 1}
    with pytest.raises(LLMError):
        parse_json("[1, 2]")


def test_bad_key_is_reported_as_configuration_problem(monkeypatch):
    def denied(kw, n):
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.AuthenticationError("invalid x-api-key", response=httpx.Response(401, request=req), body=None)
    llm, _ = make(monkeypatch, denied)
    with pytest.raises(LLMError) as e:
        llm.complete("s", "u", 10)
    assert e.value.code == "misconfigured"


def test_assemblyai_transcriber_maps_utterances_and_deletes(tmp_path):
    """The real AssemblyAI client, with HTTP replaced: upload, request, poll, map speakers, delete."""
    from app.transcription import AssemblyAITranscriber

    calls = []

    class Resp:
        def __init__(self, data, code=200):
            self._d, self.status_code = data, code

        def json(self):
            return self._d

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

    polls = iter([{"status": "processing"}, {"status": "completed", "audio_duration": 61.4, "utterances": [
        {"speaker": "A", "start": 1500, "text": "Green wala design final karo."},
        {"speaker": "B", "start": 65000, "text": "Theek hai."}]}])

    class FakeRequests:
        def post(self, url, **kw):
            calls.append(("POST", url, kw.get("json")))
            return Resp({"upload_url": "https://cdn/x"} if url.endswith("/upload") else {"id": "t1"})

        def get(self, url, **kw):
            calls.append(("GET", url, None))
            return Resp(next(polls))

        def delete(self, url, **kw):
            calls.append(("DELETE", url, None))
            return Resp({})

    t = AssemblyAITranscriber(api_key="k", poll_seconds=0)
    t._rq = FakeRequests()
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    lines, dur = t.transcribe(str(audio))
    assert [(ln.t, ln.speaker, ln.text) for ln in lines] == [
        ("00:00:01", "Speaker A", "Green wala design final karo."), ("00:01:05", "Speaker B", "Theek hai.")]
    assert dur == 61
    req = [c for c in calls if c[1].endswith("/transcript")][0][2]
    assert req["speaker_labels"] is True and req["language_detection"] is True
    assert calls[-1][0] == "DELETE"  # transcript removed from the provider
