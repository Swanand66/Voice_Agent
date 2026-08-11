"""Session ceilings — hard caps that stop a call from becoming unboundedly expensive.

Enforces three limits per session:
  1. Max wall-clock duration (MAX_SESSION_SECONDS, default 600s = 10 min)
  2. Max turn count (MAX_TURNS, default 40)
  3. Max messages retained in LLMContext (MAX_CONTEXT_MESSAGES, default 20)

The first two trigger a graceful close: bot says a warm farewell, then the
worker cancels. The third trims context in-place so prompt tokens don't grow
unbounded — every turn stays within a fixed budget.

Runs at the tail of the pipeline like MetricsCollector. Triggered on
BotStoppedSpeakingFrame (one per turn).
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

from loguru import logger

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


MAX_SESSION_SECONDS = int(os.environ.get("MAX_SESSION_SECONDS", "600"))
MAX_TURNS = int(os.environ.get("MAX_TURNS", "40"))
MAX_CONTEXT_MESSAGES = int(os.environ.get("MAX_CONTEXT_MESSAGES", "20"))

_GRACEFUL_CLOSER = (
    "I've really enjoyed chatting with you, but I need to go now. Take care!"
)


class SessionGuard(FrameProcessor):
    def __init__(
        self,
        context,
        max_seconds: int = MAX_SESSION_SECONDS,
        max_turns: int = MAX_TURNS,
        max_context_messages: int = MAX_CONTEXT_MESSAGES,
    ):
        super().__init__()
        self._context = context
        self._max_seconds = max_seconds
        self._max_turns = max_turns
        self._max_context_messages = max_context_messages
        self._start_ts = time.time()
        self._turn_count = 0
        self._closing = False
        # Set by bot.py after PipelineWorker is created — used to inject
        # the graceful closer and to cancel the worker cleanly.
        self.worker: Optional[object] = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStoppedSpeakingFrame):
            # Second BotStoppedSpeaking after we sent the closer = closer
            # finished playing. End the call.
            if self._closing:
                await self.push_frame(frame, direction)
                if self.worker is not None:
                    asyncio.create_task(self.worker.cancel())
                return

            self._turn_count += 1
            elapsed = time.time() - self._start_ts

            # Trim context every turn so LLM prompt cost stays bounded.
            self._trim_context()

            # Check ceilings — if breached, queue closer + set flag.
            if elapsed > self._max_seconds or self._turn_count >= self._max_turns:
                reason = "duration" if elapsed > self._max_seconds else "turns"
                logger.warning(
                    f"[guard] session capped ({reason}: {elapsed:.0f}s, "
                    f"{self._turn_count} turns) — sending closer"
                )
                self._closing = True
                if self.worker is not None:
                    await self.worker.queue_frames([TTSSpeakFrame(_GRACEFUL_CLOSER)])

        await self.push_frame(frame, direction)

    def _trim_context(self) -> None:
        """Drop oldest non-system messages to keep prompt tokens bounded.

        Keeps message[0] (system prompt / greeting) and the newest
        (max_context_messages - 1) turns. In-place mutation.
        """
        try:
            msgs = self._context.messages
            while len(msgs) > self._max_context_messages:
                del msgs[1]
        except Exception as e:
            logger.warning(f"[guard] context trim failed: {e}")
