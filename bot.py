import asyncio
import os

from dotenv import load_dotenv
from loguru import logger

from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, Frame
from pipecat.turns.user_mute.base_user_mute_strategy import BaseUserMuteStrategy


def _install_ice_servers_patch() -> None:
    """Inject STUN + TURN into the runner's WebRTC handler at import time.

    The Pipecat runner instantiates SmallWebRTCRequestHandler without
    ice_servers, so on hosted platforms (Render) the server only advertises
    private IPs. Reading TURN_* env vars here and monkey-patching lets the
    server advertise a TURN-relayed candidate that browsers can reach.
    """
    turn_url = os.environ.get("TURN_URL")
    if not turn_url:
        return
    from pipecat.transports.smallwebrtc.connection import IceServer
    from pipecat.transports.smallwebrtc.request_handler import (
        SmallWebRTCRequestHandler,
    )

    ice_servers = [
        IceServer(urls=["stun:stun.l.google.com:19302"]),
        IceServer(
            urls=[turn_url],
            username=os.environ.get("TURN_USERNAME"),
            credential=os.environ.get("TURN_CREDENTIAL"),
        ),
    ]
    _orig = SmallWebRTCRequestHandler.__init__

    def _patched(self, *args, **kwargs):
        _orig(self, *args, **kwargs)
        self.update_ice_servers(ice_servers)
        logger.info(f"Injected ICE servers: STUN + TURN ({turn_url})")

    SmallWebRTCRequestHandler.__init__ = _patched


load_dotenv(override=True)
_install_ice_servers_patch()

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_start.vad_user_turn_start_strategy import (
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_start.transcription_user_turn_start_strategy import (
    TranscriptionUserTurnStartStrategy,
)
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.soniox.stt import SonioxSTTService
from pipecat.services.soniox.tts import SonioxTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.workers.runner import WorkerRunner


class MuteDuringBotSpeechStrategy(BaseUserMuteStrategy):
    """Mute the user mic whenever the bot is speaking.

    Prevents the bot's own audio (echoed back through the user's speakers or
    room mic) from triggering VAD-detected "user speaking" events that cause
    the pipeline to interrupt itself mid-word.
    """

    def __init__(self):
        super().__init__()
        self._bot_speaking = False

    async def process_frame(self, frame: Frame) -> bool:
        await super().process_frame(frame)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        return self._bot_speaking


transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        # 80ms chunks — fewer packets → less per-packet TCP overhead over the
        # TURN relay. Still well inside conversational latency.
        audio_out_10ms_chunks=8,
        # CRITICAL for smooth playback: do NOT insert silence when the queue
        # briefly empties (Soniox chunk jitter, first-response cold start,
        # Render CPU hiccups). Default True causes audible mid-word cuts and
        # choppy first-word playback. Waiting a few ms for real audio is
        # imperceptible; inserted silence is not.
        audio_out_auto_silence=False,
    ),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info("Starting bot")

    stt = SonioxSTTService(
        api_key=os.environ["SONIOX_API_KEY"],
        settings=SonioxSTTService.Settings(
            language_hints=[Language.EN],
            language_hints_strict=True,
            enable_language_identification=True,
        ),
    )

    llm = GroqLLMService(
        api_key=os.environ["GROQ_API_KEY"],
        settings=GroqLLMService.Settings(
            model="llama-3.3-70b-versatile",
            system_instruction=(
                "You are Maya — a warm, kind, gentle voice assistant who "
                "talks like a helpful friend on a phone call. Always polite, "
                "always welcoming. Never robotic, never rude.\n\n"
                "How you speak:\n"
                "• Use natural contractions: I'm, you're, don't, that's, it's.\n"
                "• Warm, spoken English. Friendly and gentle at all times.\n"
                "• Vary sentence length naturally.\n"
                "• Small conversational glue when it fits: 'sure', 'of course', "
                "'oh', 'right', 'okay', 'honestly'. Sparingly.\n"
                "• React kindly first, then respond. 'Oh nice!', 'That sounds "
                "great', 'Sure, happy to help with that.'\n"
                "• Ask a gentle follow-up if it helps you help them.\n"
                "• Match the user's energy but always stay warm and calm.\n\n"
                "What to avoid:\n"
                "• Never say 'As an AI', 'I'm a language model', or similar.\n"
                "• No lists, bullet points, headings, or markdown — this is spoken.\n"
                "• No emojis. No stage directions in asterisks.\n"
                "• Don't over-explain. Keep it to one or two sentences most "
                "turns. Three max if the question really needs it.\n"
                "• Never sound curt, dismissive, or cold. If unsure, err on the "
                "side of warmth.\n\n"
                "If asked your name: Maya. If asked what you are: 'I'm Maya, "
                "here to help.' Never break character."
            ),
        ),
    )

    tts = SonioxTTSService(
        api_key=os.environ["SONIOX_API_KEY"],
        settings=SonioxTTSService.Settings(
            voice=os.environ.get("SONIOX_VOICE", "Priya"),
        ),
    )

    # Aggressive VAD tuning to reduce false triggers from bot echo / room noise.
    # Defaults: confidence=0.7, start_secs=0.2, stop_secs=0.2, min_volume=0.6
    vad_params = VADParams(
        confidence=0.85,
        start_secs=0.4,
        stop_secs=0.6,
        min_volume=0.7,
    )
    vad = SileroVADAnalyzer(params=vad_params)

    context = LLMContext()
    turn_strategies = UserTurnStrategies(
        start=[VADUserTurnStartStrategy(), TranscriptionUserTurnStartStrategy()],
        stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.6)],
    )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad,
            user_turn_strategies=turn_strategies,
            user_mute_strategies=[MuteDuringBotSpeechStrategy()],
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected — warming up media path before greeting")
        # on_client_connected fires when the peer connection is established,
        # but RTP media flow needs a moment to stabilize (especially over TURN
        # relay). Delaying the first TTS output lets the audio channel warm up
        # so the greeting doesn't stutter.
        await asyncio.sleep(1.0)
        context.add_message(
            {
                "role": "developer",
                "content": (
                    "Greet the user with exactly this line, warmly: "
                    "'Hey, this is Maya. How can I help you today?'"
                ),
            }
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
