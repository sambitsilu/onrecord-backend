"""AI access, behind a small interface so tests can use a fake.

Production uses the Anthropic API with a pinned model, temperature 0,
SDK-level retries for rate limits and server errors, one repair attempt when
the reply isn't valid JSON, and a per-organization monthly token budget.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import LLMUsage, Organization

log = logging.getLogger("onrecord.llm")


class LLMError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.usage: Usage | None = None  # tokens already spent when the error happened  # invalid_json | refused | unavailable | rate_limited | budget_exceeded | too_large


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __iadd__(self, other: "Usage") -> "Usage":
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        return self


class LLM(Protocol):
    model: str

    def complete(self, system: str, user: str, max_tokens: int, schema: dict | None = None) -> tuple[str, Usage]: ...


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json(text: str) -> dict:
    s = _FENCE.sub("", (text or "").strip()).strip()
    try:
        v = json.loads(s)
    except json.JSONDecodeError:
        a, b = s.find("{"), s.rfind("}")
        if a < 0 or b <= a:
            raise LLMError("invalid_json", "The AI reply was not JSON.")
        try:
            v = json.loads(s[a:b + 1])
        except json.JSONDecodeError as e:
            raise LLMError("invalid_json", f"The AI reply was not valid JSON: {e}") from e
    if not isinstance(v, dict):
        raise LLMError("invalid_json", "The AI reply was not a JSON object.")
    return v


def complete_json(llm: LLM, system: str, user: str, max_tokens: int = 8000, schema: dict | None = None) -> tuple[dict, Usage]:
    """Ask for JSON. With a schema, the API constrains the reply to it (structured outputs);
    a repair attempt still covers models or providers without that feature."""
    text, usage = llm.complete(system, user, max_tokens, schema)
    try:
        return parse_json(text), usage
    except LLMError:
        pass
    repair = user + "\n\nYour previous reply was not valid JSON. Reply again with ONLY the JSON object, nothing else."
    try:
        text2, usage2 = llm.complete(system, repair, max_tokens, schema)
    except LLMError as e:
        e.usage = usage
        raise
    usage += usage2
    try:
        return parse_json(text2), usage
    except LLMError as e:
        e.usage = usage  # tokens were spent even though the reply was unusable
        raise


class AnthropicLLM:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        import anthropic

        s = get_settings()
        self.model = model or s.llm_model
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key or s.anthropic_api_key,
                                           max_retries=s.llm_max_retries, timeout=s.llm_timeout_seconds)
        self._structured = True

    def complete(self, system: str, user: str, max_tokens: int, schema: dict | None = None) -> tuple[str, Usage]:
        a = self._anthropic
        kwargs = {"model": self.model, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": user}]}
        if schema and self._structured:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        try:
            try:
                msg = self._client.messages.create(**kwargs)
            except a.BadRequestError:
                if "output_config" not in kwargs:
                    raise
                # This model/provider doesn't accept the schema: fall back to prompt-only JSON from now on.
                log.warning("structured outputs rejected for model %s; falling back to prompt-only JSON", self.model)
                self._structured = False
                kwargs.pop("output_config")
                msg = self._client.messages.create(**kwargs)
        except (a.AuthenticationError, a.PermissionDeniedError) as e:
            log.error("Anthropic rejected the API key (check ANTHROPIC_API_KEY): %s", e)
            raise LLMError("misconfigured", "The AI service rejected the API key. An administrator needs to check the configuration.") from e
        except a.NotFoundError as e:
            log.error("Anthropic model not found (check LLM_MODEL=%s): %s", self.model, e)
            raise LLMError("misconfigured", "The configured AI model isn't available. An administrator needs to check the configuration.") from e
        except a.RateLimitError as e:
            raise LLMError("rate_limited", "The AI service is busy. Try again in a minute.") from e
        except a.BadRequestError as e:
            msg_txt = str(e).lower()
            code = "too_large" if ("too long" in msg_txt or "too large" in msg_txt or "context" in msg_txt) else "unavailable"
            raise LLMError(code, "The AI service rejected the request.") from e
        except a.APIError as e:
            raise LLMError("unavailable", "The AI service is unavailable right now.") from e
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
        if getattr(msg, "stop_reason", "") == "refusal":
            raise LLMError("refused", "The AI declined this request.")
        u = getattr(msg, "usage", None)
        return text, Usage(getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0)


# --- budget ------------------------------------------------------------------

def month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def tokens_used_this_month(db: Session, org_id: uuid.UUID) -> int:
    q = select(func.coalesce(func.sum(LLMUsage.input_tokens + LLMUsage.output_tokens), 0)).where(
        LLMUsage.org_id == org_id, LLMUsage.at >= month_start())
    return int(db.execute(q).scalar_one())


def check_budget(db: Session, org_id: uuid.UUID) -> None:
    budget = db.execute(select(Organization.monthly_token_budget).where(Organization.id == org_id)).scalar_one()
    if tokens_used_this_month(db, org_id) >= budget:
        raise LLMError("budget_exceeded", "This organization has used its AI budget for the month.")


def record_usage(db: Session, org_id: uuid.UUID, kind: str, model: str, usage: Usage) -> None:
    db.add(LLMUsage(org_id=org_id, kind=kind, model=model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens))
