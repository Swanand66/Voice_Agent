"""Per-turn metrics collector that persists cost + latency data to Supabase.

Sits at the tail of the Pipecat pipeline. Observes:
  - MetricsFrame (usage counts + TTFB per service) — for cost & TTFB latency
  - UserStoppedSpeakingFrame → BotStartedSpeakingFrame — for end-to-end latency
  - BotStoppedSpeakingFrame — turn flush signal

Each turn is written to the shared asyncpg pool tagged with client_id + session_id.
Set METRICS_DEBUG=1 to log the raw contents of every MetricsFrame; useful when
values look wrong.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    MetricsFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import (
    LLMUsageMetricsData,
    STTUsageMetricsData,
    TTFBMetricsData,
    TTSUsageMetricsData,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import pricing
from db import get_pool


DEBUG = os.environ.get("METRICS_DEBUG", "0") == "1"


class MetricsCollector(FrameProcessor):
    def __init__(self, session_id: str, client_id: str):
        super().__init__()
        self._session_id = session_id
        self._client_id = client_id
        self._turn_index = 0
        self._pending = self._empty_bucket()
        self._user_stop_ts: Optional[float] = None
        self._session_row_created = False
        self._create_lock = asyncio.Lock()

    @staticmethod
    def _empty_bucket() -> dict:
        return {
            "llm_prompt_tokens": 0,
            "llm_completion_tokens": 0,
            "llm_cost_usd": 0.0,
            "tts_chars": 0,
            "tts_cost_usd": 0.0,
            "stt_seconds": 0.0,
            "stt_cost_usd": 0.0,
            "llm_ttfb_ms": 0.0,
            "tts_ttfb_ms": 0.0,
            "end_to_end_ms": 0.0,
        }

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, MetricsFrame):
            self._absorb_metrics(frame)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_stop_ts = time.time()
        elif isinstance(frame, BotStartedSpeakingFrame):
            if self._user_stop_ts is not None:
                self._pending["end_to_end_ms"] = (
                    time.time() - self._user_stop_ts
                ) * 1000
                self._user_stop_ts = None
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self._flush_turn()

        await self.push_frame(frame, direction)

    def _absorb_metrics(self, frame: MetricsFrame) -> None:
        if DEBUG:
            for item in frame.data:
                logger.info(
                    f"[metrics-debug] {type(item).__name__} "
                    f"processor={getattr(item, 'processor', '?')} "
                    f"value={getattr(item, 'value', '?')!r}"
                )

        for item in frame.data:
            processor = getattr(item, "processor", "") or ""

            if isinstance(item, LLMUsageMetricsData):
                prompt = getattr(item.value, "prompt_tokens", 0) or 0
                completion = getattr(item.value, "completion_tokens", 0) or 0
                self._pending["llm_prompt_tokens"] += prompt
                self._pending["llm_completion_tokens"] += completion
                self._pending["llm_cost_usd"] += pricing.calc_llm_cost(
                    processor, prompt, completion
                )

            elif isinstance(item, TTSUsageMetricsData):
                chars = int(item.value or 0)
                self._pending["tts_chars"] += chars
                self._pending["tts_cost_usd"] += pricing.calc_tts_cost(
                    processor, chars
                )

            elif isinstance(item, STTUsageMetricsData):
                secs = float(getattr(item.value, "audio_seconds", 0) or 0)
                self._pending["stt_seconds"] += secs
                self._pending["stt_cost_usd"] += pricing.calc_stt_cost(
                    processor, secs
                )

            elif isinstance(item, TTFBMetricsData):
                secs = float(getattr(item, "value", 0) or 0)
                if secs <= 0:
                    continue
                ms = secs * 1000
                if "LLM" in processor and self._pending["llm_ttfb_ms"] == 0:
                    self._pending["llm_ttfb_ms"] = ms
                elif "TTS" in processor and self._pending["tts_ttfb_ms"] == 0:
                    self._pending["tts_ttfb_ms"] = ms

    async def _ensure_session_row(self) -> None:
        """Insert the session row on first successful flush.

        Deferred so we don't pay a DB write for empty warmup sessions
        (client connects but never actually talks).
        """
        if self._session_row_created:
            return
        async with self._create_lock:
            if self._session_row_created:
                return
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO sessions (id, client_id, started_at)
                       VALUES ($1, $2, now())
                       ON CONFLICT (id) DO NOTHING""",
                    self._session_id, self._client_id,
                )
            self._session_row_created = True

    async def _flush_turn(self) -> None:
        bucket = self._pending
        empty = (
            bucket["llm_prompt_tokens"] == 0
            and bucket["tts_chars"] == 0
            and bucket["stt_seconds"] == 0
            and bucket["end_to_end_ms"] == 0
        )
        if empty:
            self._pending = self._empty_bucket()
            return

        self._turn_index += 1
        total = (
            bucket["llm_cost_usd"] + bucket["tts_cost_usd"] + bucket["stt_cost_usd"]
        )

        try:
            await self._ensure_session_row()
            pool = await get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO turns (
                            session_id, client_id, turn_index,
                            llm_prompt_tokens, llm_completion_tokens, llm_cost_usd,
                            tts_chars, tts_cost_usd,
                            stt_seconds, stt_cost_usd,
                            total_cost_usd,
                            llm_ttfb_ms, tts_ttfb_ms, end_to_end_ms
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",
                        self._session_id, self._client_id, self._turn_index,
                        bucket["llm_prompt_tokens"], bucket["llm_completion_tokens"], bucket["llm_cost_usd"],
                        bucket["tts_chars"], bucket["tts_cost_usd"],
                        bucket["stt_seconds"], bucket["stt_cost_usd"],
                        total,
                        bucket["llm_ttfb_ms"], bucket["tts_ttfb_ms"], bucket["end_to_end_ms"],
                    )
                    await conn.execute(
                        """UPDATE sessions
                           SET ended_at = now(),
                               turn_count = turn_count + 1,
                               total_cost_usd = total_cost_usd + $1
                           WHERE id = $2""",
                        total, self._session_id,
                    )
        except Exception as e:
            # Never let a metrics write break the call. Log and drop.
            logger.error(f"[metrics] write failed: {e}")

        logger.info(
            f"[metrics] {self._client_id}/turn {self._turn_index}: ${total:.5f} "
            f"| llm={bucket['llm_prompt_tokens']}/{bucket['llm_completion_tokens']}tok "
            f"tts={bucket['tts_chars']}ch stt={bucket['stt_seconds']:.1f}s "
            f"| e2e={bucket['end_to_end_ms']:.0f}ms "
            f"llm_ttfb={bucket['llm_ttfb_ms']:.0f}ms "
            f"tts_ttfb={bucket['tts_ttfb_ms']:.0f}ms"
        )
        self._pending = self._empty_bucket()
