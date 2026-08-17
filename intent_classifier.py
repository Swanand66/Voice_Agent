"""Post-call intent classifier.

Called fire-and-forget from bot.py's on_client_disconnected. Sends a
PII-redacted transcript to Groq, parses {intent, confidence, reason},
writes all fields plus classifier version + model to sessions. Never
raises — a failed classification must not break a call.

Semantics:
  - `other`   = model confidently says none of the categories fit
  - `unknown` = classifier failed, parsed a bad label, or confidence
                was below UNKNOWN_CONFIDENCE_THRESHOLD
  - NULL in DB = classification never ran (retry candidate)
"""
from __future__ import annotations

import asyncio
import json
import os
import re

from loguru import logger

from db import get_pool


INTENT_LABELS = {
    "booking",
    "inquiry",
    "callback",
    "complaint",
    "chat",
    "refused",
    "other",
    "unknown",
}

UNKNOWN_CONFIDENCE_THRESHOLD = 0.5

# Bump when the prompt, label set, or coercion rules change so old rows
# stay comparable in analytics.
INTENT_VERSION = "v2"
_MODEL = "openai/gpt-oss-120b"

_GROQ_TIMEOUT_SECS = 15

_SYSTEM_PROMPT = (
    "You classify voice-agent phone calls into exactly one high-level intent. "
    "Read the transcript and pick the single best label from this set:\n"
    "  - booking: caller wants to schedule, book, or commit to something\n"
    "  - inquiry: caller asked for information\n"
    "  - callback: caller wants to be called back later\n"
    "  - complaint: caller expressed a problem or dissatisfaction\n"
    "  - chat: casual conversation, no specific goal\n"
    "  - refused: caller said no or ended the call quickly\n"
    "  - other: none of the above fit, but the call had a clear purpose\n\n"
    "Respond with JSON only, no prose:\n"
    '  {"intent": "<label>", "confidence": <0.0-1.0>, "reason": "<max 15 words>"}\n\n'
    "confidence should reflect how sure you are — use <0.5 when the "
    "transcript is ambiguous, too short, or fits multiple labels."
)

# PII patterns applied to the transcript before it is sent to Groq.
# Best-effort — regex catches digit-form phone numbers and email addresses;
# spelled-out digits ('five five five...') slip through.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def _redact_pii(text: str) -> str:
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    return text


def _transcript_to_text(messages: list[dict]) -> str:
    """Flatten Pipecat context messages into a readable transcript."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role in {"user", "assistant"} and content:
            speaker = "Caller" if role == "user" else "Agent"
            lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


async def _call_groq_once(client, transcript: str):
    return await asyncio.wait_for(
        client.chat.completions.create(
            model=_MODEL,
            temperature=0,
            max_tokens=150,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Transcript:\n{transcript}"},
            ],
        ),
        timeout=_GROQ_TIMEOUT_SECS,
    )


async def _call_groq_with_retry(client, transcript: str):
    """One retry on any failure (timeout, 5xx, 429, parse error upstream)."""
    try:
        return await _call_groq_once(client, transcript)
    except Exception as e:
        logger.warning(f"[intent] Groq call failed, retrying once: {e}")
        return await _call_groq_once(client, transcript)


def _parse_response(raw: str) -> tuple[str, float, str]:
    """Return (intent, confidence, reason). Coerces bad output to unknown."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return "unknown", 0.0, "invalid JSON from classifier"

    label = str(parsed.get("intent", "")).strip().lower()
    reason = str(parsed.get("reason", ""))[:200]
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    if label not in INTENT_LABELS or label == "unknown":
        return "unknown", confidence, reason or "label not in allowed set"
    if confidence < UNKNOWN_CONFIDENCE_THRESHOLD:
        return "unknown", confidence, reason or "confidence below threshold"
    return label, confidence, reason


async def classify_and_store(session_id: str, messages: list[dict]) -> None:
    try:
        from groq import AsyncGroq
    except ImportError:
        logger.warning("[intent] groq SDK not available; skipping classification")
        return

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.warning("[intent] GROQ_API_KEY not set; skipping classification")
        return

    transcript = _transcript_to_text(messages)
    if not transcript.strip():
        return
    transcript = _redact_pii(transcript)

    try:
        client = AsyncGroq(api_key=api_key)
        resp = await _call_groq_with_retry(client, transcript)
        raw = resp.choices[0].message.content or "{}"
        label, confidence, reason = _parse_response(raw)

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE sessions
                      SET intent = $1,
                          intent_confidence = $2,
                          intent_reason = $3,
                          intent_version = $4,
                          intent_model = $5
                    WHERE id = $6""",
                label, confidence, reason, INTENT_VERSION, _MODEL, session_id,
            )
        logger.info(
            f"[intent] {session_id}: {label} "
            f"(confidence={confidence:.2f}, version={INTENT_VERSION})"
        )
    except Exception as e:
        # Total failure after retry → leave intent NULL so the row remains a
        # re-drive candidate rather than being locked in as 'unknown'.
        logger.error(f"[intent] classification failed for {session_id}: {e}")
