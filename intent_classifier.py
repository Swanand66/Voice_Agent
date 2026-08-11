"""Post-call intent classifier.

Called fire-and-forget from bot.py's on_client_disconnected. Sends the full
transcript to Groq, parses a single high-level intent label, writes it to
sessions.intent. Swallows all errors — a failed classification must never
break a call.
"""
from __future__ import annotations

import json
import os

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
}

_SYSTEM_PROMPT = (
    "You classify voice-agent phone calls into exactly one high-level intent. "
    "Read the transcript and pick the single best label from this set:\n"
    "  - booking: caller wants to schedule, book, or commit to something\n"
    "  - inquiry: caller asked for information\n"
    "  - callback: caller wants to be called back later\n"
    "  - complaint: caller expressed a problem or dissatisfaction\n"
    "  - chat: casual conversation, no specific goal\n"
    "  - refused: caller said no or ended the call quickly\n"
    "  - other: anything that doesn't fit above\n\n"
    'Respond with JSON only: {"intent": "<label>"}. No extra keys, no prose.'
)

_MODEL = "llama-3.3-70b-versatile"


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

    try:
        client = AsyncGroq(api_key=api_key)
        resp = await client.chat.completions.create(
            model=_MODEL,
            temperature=0,
            max_tokens=40,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Transcript:\n{transcript}"},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        label = str(parsed.get("intent", "")).strip().lower()
        if label not in INTENT_LABELS:
            label = "other"

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE sessions SET intent = $1 WHERE id = $2",
                label, session_id,
            )
        logger.info(f"[intent] {session_id}: {label}")
    except Exception as e:
        logger.error(f"[intent] classification failed for {session_id}: {e}")
