"""Claude calls. Everything that needs the model goes through json_call(), which returns schema-valid JSON."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

import anthropic

from .config import MODEL

log = logging.getLogger("autoapply.llm")
_client: anthropic.Anthropic | None = None


class LLMError(RuntimeError):
    pass


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=3)
    return _client


def available() -> bool:
    """True when credentials are configured. (The SDK also reads `ant auth login` profiles.)"""
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return True
    return (Path.home() / ".config" / "anthropic").exists()


# $ per million tokens: input, output, cache read, cache write (5-minute cache = 1.25x input).
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
# Models that accept the server-side refusal fallback (Haiku 5.5 has none and would reject the parameter).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-2", "claude-fable-5-1"}
CHEAP_MODEL = "claude-haiku-5-5"
MID_MODEL = "claude-sonnet-5-5"


def model_for(role: str) -> str:
    """Which model a job uses. role: scoring | sorting (cheap work) or answers | writing | resume (quality work).
    An explicit AUTOAPPLY_MODEL_SCORING / _ANSWERS in .env always wins; otherwise Settings > Cost decides."""
    env = {"scoring": "AUTOAPPLY_MODEL_SCORING", "sorting": "AUTOAPPLY_MODEL_SCORING",
           "answers": "AUTOAPPLY_MODEL_ANSWERS", "writing": "AUTOAPPLY_MODEL_ANSWERS"}.get(role)
    if env and os.getenv(env):
        return os.getenv(env)
    from . import settings as cfg
    mode = cfg.get().get("cost_mode", "balanced")
    cheap_work = role in ("scoring", "sorting")
    if mode == "quality":
        return MODEL
    if mode == "saver":
        return CHEAP_MODEL if cheap_work else MID_MODEL
    return CHEAP_MODEL if cheap_work else MODEL      # balanced


def scoring_effort(model: str) -> str:
    # Measured: Haiku at "low" disagreed with Opus on 6/10 apply/skip calls; at "medium" it matched Opus 9/10
    # (as often as Opus matches itself) for ~1/40th of the cost. Bigger models are fine at "low".
    return "medium" if "haiku" in model else "low"


def _record_usage(purpose: str, model: str, usage) -> None:
    from . import db
    pin, pout, pread, pwrite = PRICES.get(model, PRICES["claude-opus-5-5"])
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cost = (inp * pin + out * pout + cr * pread + cw * pwrite) / 1_000_000
    try:
        db.x("INSERT INTO llm_usage(t, purpose, model, input, output, cache_read, cache_write, cost) VALUES(?,?,?,?,?,?,?,?)",
             (db.now(), purpose, model, inp, out, cr, cw, cost))
    except Exception:  # noqa: BLE001 — bookkeeping must never break a request
        log.exception("couldn't record usage")


def json_call(system: str, content: str | list, schema: dict, *, effort: str = "medium",
              model: str | None = None, max_tokens: int = 16000, context: str = "", purpose: str = "other") -> dict:
    """One request, structured JSON out. Raises LLMError on refusal/truncation/API failure.
    `context` is stable text (e.g. your profile) sent as a cached system block: repeat requests pay ~1/20th for it."""
    if not available():
        raise LLMError("No Claude credentials. Set ANTHROPIC_API_KEY in .env.")
    model = model or MODEL
    sys_blocks = [{"type": "text", "text": system}]
    if context:
        sys_blocks.append({"type": "text", "text": context})
    sys_blocks[-1]["cache_control"] = {"type": "ephemeral"}
    kwargs = dict(model=model, max_tokens=max_tokens, system=sys_blocks,
                  messages=[{"role": "user", "content": content}],
                  output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})
    if model in FALLBACK_MODELS:
        # Server-side refusal fallback: if the model declines, the API retries on a fallback model.
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    try:
        resp = client().beta.messages.create(**kwargs)
        _record_usage(purpose, getattr(resp, "model", model) or model, resp.usage)
    except anthropic.AuthenticationError as e:
        raise LLMError("Claude rejected the API key.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Claude rate limit hit; will retry later.") from e
    except anthropic.BadRequestError as e:
        raise LLMError(f"Claude bad request: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API error {e.status_code}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Claude API.") from e
    except anthropic.AuthenticationError as e:
        raise LLMError("Claude rejected the API key.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Claude rate limit hit; will retry later.") from e
    except anthropic.BadRequestError as e:
        raise LLMError(f"Claude bad request: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API error {e.status_code}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Claude API.") from e

    if resp.stop_reason == "refusal":
        raise LLMError("Claude declined this request.")
    if resp.stop_reason == "max_tokens":
        raise LLMError("Claude's answer was cut off (max_tokens).")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError as e:
        raise LLMError("Claude returned invalid JSON.") from e


async def ajson_call(*args: Any, **kwargs: Any) -> dict:
    return await asyncio.to_thread(json_call, *args, **kwargs)


def pdf_block(path: Path) -> dict:
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}


# ---- tiny schema helpers (structured outputs need additionalProperties:false and explicit required) ----

def obj(props: dict[str, dict]) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(items: dict) -> dict:
    return {"type": "array", "items": items}


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
