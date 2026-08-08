# Voice Agent from Scratch — Pipecat + Soniox (Free Dev Stack)

A complete, opinionated walkthrough for building a **real-time voice agent** from an empty folder using [Pipecat](https://github.com/pipecat-ai/pipecat) with **Soniox** for STT and TTS, and a **free LLM** for the brain. Optimized for solo dev on Windows 11 with zero paid API commitments beyond Soniox's signup credits.

---

## 0. TL;DR — What you're building

```
 ┌────────┐   audio    ┌──────────┐   text    ┌─────┐   text    ┌──────────┐   audio   ┌────────┐
 │ Browser│ ─────────► │ Soniox   │ ────────► │ LLM │ ────────► │ Soniox   │ ────────► │ Browser│
 │  (mic) │  WebRTC    │  STT     │           │(Groq/│           │  TTS     │  WebRTC   │(speaker)│
 └────────┘            └──────────┘           │Gemini│           └──────────┘           └────────┘
                            ▲                 │/Ollama│                                     
                            │                 └─────┘                                       
                        Silero VAD                                                          
                     (voice activity)                                                       
                                                                                            
                     ── all glued by Pipecat pipeline ──                                    
```

Your user talks in the browser → Soniox streams transcripts → LLM generates a reply → Soniox streams speech back → user hears it. Pipecat orchestrates the whole thing as a **streaming pipeline** so latency stays low (interruption-friendly).

---

## 1. Why this stack

| Layer            | Choice                     | Why                                                                                       | Cost                                          |
| ---------------- | -------------------------- | ----------------------------------------------------------------------------------------- | --------------------------------------------- |
| Framework        | Pipecat                    | Open source; first-class Soniox services; handles VAD, interruption, turn-taking          | Free                                          |
| Transport        | `SmallWebRTCTransport`     | Ships with Pipecat, runs entirely on your machine, works from any browser                 | Free                                          |
| VAD              | Silero (via `silero-vad`)  | Local, fast, no API                                                                       | Free                                          |
| STT              | Soniox (`SonioxSTTService`) | Streaming, 60+ languages, low latency, native Pipecat integration                         | Free signup credits, then $ / hr             |
| LLM              | Groq **or** Gemini **or** Ollama | Groq: fastest hosted free tier. Gemini: generous free tier. Ollama: fully local & unlimited | Free tier / local                             |
| TTS              | Soniox (`SonioxTTSService`) | Streaming, 60+ languages, matches your STT vendor                                          | Free signup credits, then ~$0.70/hr           |

> **Fully-free-forever fallback** (if you burn Soniox credits): swap STT for `WhisperSTTService` (local `faster-whisper`) and TTS for `PiperTTSService` (local Piper). Guide covers this in §10.

---

## 2. Prerequisites

- **Python 3.10 or 3.11** (Pipecat supports 3.10+; some deps lag on 3.12+). Check: `python --version`
- **Git** for cloning examples if needed
- **A modern browser** (Chrome/Edge) for WebRTC mic + speaker
- **A working mic and speaker/headphones**
- **A Soniox account** at [console.soniox.com](https://console.soniox.com) — grab your API key
- **One free LLM key**: pick one
  - Groq: [console.groq.com](https://console.groq.com) — 1000 req/day free
  - Gemini: [aistudio.google.com](https://aistudio.google.com) — 1.5M tokens/day free on Flash
  - Ollama: install from [ollama.com](https://ollama.com) — no key, fully local

---

## 3. Project scaffold

Run in PowerShell from `Voice Agent -Scratch`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Create the file structure:

```
Voice Agent -Scratch/
├── .env                    # secrets (git-ignored)
├── .gitignore
├── requirements.txt
├── bot.py                  # the Pipecat pipeline
├── server.py               # HTTP + WebRTC signaling server
└── client/
    └── index.html          # browser mic/speaker UI
```

### 3.1 `requirements.txt`

```txt
pipecat-ai[silero,soniox,webrtc]>=0.0.60
python-dotenv>=1.0.1
fastapi>=0.115
uvicorn[standard]>=0.30
# pick ONE of these LLM adapters (or install all three to switch freely):
# pipecat-ai[groq]        # Groq (hosted free tier)
# pipecat-ai[google]      # Gemini (hosted free tier)
# pipecat-ai[ollama]      # Ollama (local)
```

> Pipecat uses **extras** (`[soniox]`, `[silero]`, `[groq]`, …) to keep installs lean. Install only what you use. `pip install "pipecat-ai[silero,soniox,webrtc,groq]"` is a fine one-liner.

Install:

```powershell
pip install -r requirements.txt
pip install "pipecat-ai[groq]"     # or [google] or [ollama]
```

### 3.2 `.env`

```env
SONIOX_API_KEY=sk_...
GROQ_API_KEY=gsk_...
# GOOGLE_API_KEY=...
# (Ollama needs no key — just run `ollama serve` locally)
```

### 3.3 `.gitignore`

```
.venv/
.env
__pycache__/
*.pyc
```

---

## 4. The pipeline — `bot.py`

This is the heart of the agent. Pipecat is built around a **frame pipeline**: audio frames flow in, get transcribed, sent to an LLM, converted back to audio frames, and streamed out. The framework handles interruption, turn-taking, and back-pressure.

```python
# bot.py
import os
from dotenv import load_dotenv

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMMessagesFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.soniox.stt import SonioxSTTService
from pipecat.services.soniox.tts import SonioxTTSService
# --- pick ONE LLM ---
from pipecat.services.groq.llm import GroqLLMService
# from pipecat.services.google.llm import GoogleLLMService
# from pipecat.services.ollama.llm import OLLamaLLMService

from pipecat.transports.network.small_webrtc import (
    SmallWebRTCTransport,
    SmallWebRTCConnection,
)
from pipecat.transports.base_transport import TransportParams

load_dotenv()


async def run_bot(webrtc_connection: SmallWebRTCConnection):
    # 1) Transport — browser mic/speaker over WebRTC, VAD gates when the user is speaking
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    # 2) STT — Soniox streaming
    stt = SonioxSTTService(
        api_key=os.environ["SONIOX_API_KEY"],
        # optional: language_hints=["en"], context="tech support call about billing"
    )

    # 3) LLM — Groq (free tier, ~sub-second responses)
    llm = GroqLLMService(
        api_key=os.environ["GROQ_API_KEY"],
        model="llama-3.3-70b-versatile",  # fast + smart on Groq's free tier
    )

    # 4) TTS — Soniox streaming
    tts = SonioxTTSService(
        api_key=os.environ["SONIOX_API_KEY"],
        # optional: voice="en_female_1"
    )

    # 5) Conversation context — system prompt + rolling history
    messages = [
        {
            "role": "system",
            "content": (
                "You are a friendly voice assistant. Keep replies short and "
                "natural — you are being spoken aloud. Avoid markdown, lists, "
                "and long paragraphs. One or two sentences per turn."
            ),
        }
    ]
    context = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)

    # 6) The pipeline — order matters
    pipeline = Pipeline(
        [
            transport.input(),              # audio frames from browser
            stt,                            # → transcription frames
            context_aggregator.user(),      # append user text to context
            llm,                            # → LLM text frames
            tts,                            # → audio frames
            transport.output(),             # send audio to browser
            context_aggregator.assistant(), # append assistant text to context
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,        # user can talk over the bot
            enable_metrics=True,
        ),
    )

    # Kick off with a greeting so the user hears something immediately
    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        messages.append(
            {"role": "user", "content": "Say hello and ask how you can help, in one short line."}
        )
        await task.queue_frames([LLMMessagesFrame(messages)])

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)
```

### 4.1 Swapping the LLM

Replace the Groq block with any of these — same shape, different provider:

```python
# --- Gemini (Google AI Studio free tier) ---
from pipecat.services.google.llm import GoogleLLMService
llm = GoogleLLMService(
    api_key=os.environ["GOOGLE_API_KEY"],
    model="gemini-1.5-flash",
)

# --- Ollama (local, fully free, needs `ollama serve` running) ---
from pipecat.services.ollama.llm import OLLamaLLMService
llm = OLLamaLLMService(
    model="llama3.1:8b",     # 8B fits on ~8GB VRAM / decent CPU
    base_url="http://localhost:11434/v1",
)
```

> For voice you want **latency < 500 ms** end-to-end. Ollama on CPU can be slow — try `llama3.2:3b` for a snappier local option, or use Groq if your machine can't keep up.

---

## 5. The signaling server — `server.py`

`SmallWebRTCTransport` needs a tiny HTTP endpoint to exchange WebRTC offer/answer with the browser. FastAPI works great.

```python
# server.py
import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pipecat.transports.network.small_webrtc import SmallWebRTCConnection

from bot import run_bot

load_dotenv()

# Track live connections so we can clean them up on shutdown
active_connections: dict[str, SmallWebRTCConnection] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for conn in list(active_connections.values()):
        await conn.disconnect()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="client"), name="static")


@app.get("/")
async def index():
    return FileResponse("client/index.html")


@app.post("/api/offer")
async def offer(body: dict, background_tasks: BackgroundTasks):
    pc_id = body.get("pc_id")

    if pc_id and pc_id in active_connections:
        # Renegotiation
        conn = active_connections[pc_id]
        await conn.renegotiate(sdp=body["sdp"], type=body["type"])
    else:
        conn = SmallWebRTCConnection(ice_servers=["stun:stun.l.google.com:19302"])
        await conn.initialize(sdp=body["sdp"], type=body["type"])

        @conn.event_handler("closed")
        async def _on_closed(c: SmallWebRTCConnection):
            active_connections.pop(c.pc_id, None)

        active_connections[conn.pc_id] = conn
        background_tasks.add_task(run_bot, conn)

    answer = conn.get_answer()
    return answer


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
```

---

## 6. The browser client — `client/index.html`

Bare-minimum page that grabs the mic, sends the offer, plays the incoming audio.

```html
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Voice Agent</title>
    <style>
      body { font-family: system-ui; max-width: 600px; margin: 4rem auto; padding: 1rem; }
      button { padding: 1rem 2rem; font-size: 1rem; cursor: pointer; }
      #status { margin-top: 1rem; color: #555; }
    </style>
  </head>
  <body>
    <h1>Voice Agent</h1>
    <button id="start">Start talking</button>
    <button id="stop" disabled>Stop</button>
    <p id="status">Idle</p>
    <audio id="remote" autoplay></audio>

    <script>
      const startBtn = document.getElementById("start");
      const stopBtn = document.getElementById("stop");
      const statusEl = document.getElementById("status");
      const remoteAudio = document.getElementById("remote");

      let pc = null;
      let localStream = null;

      startBtn.onclick = async () => {
        statusEl.textContent = "Requesting mic…";
        localStream = await navigator.mediaDevices.getUserMedia({ audio: true });

        pc = new RTCPeerConnection({
          iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
        });

        localStream.getTracks().forEach((t) => pc.addTrack(t, localStream));
        pc.addTransceiver("audio", { direction: "recvonly" });

        pc.ontrack = (e) => { remoteAudio.srcObject = e.streams[0]; };

        pc.onconnectionstatechange = () => {
          statusEl.textContent = "WebRTC: " + pc.connectionState;
        };

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        const resp = await fetch("/api/offer", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sdp: offer.sdp, type: offer.type }),
        });
        const answer = await resp.json();
        await pc.setRemoteDescription(answer);

        startBtn.disabled = true;
        stopBtn.disabled = false;
        statusEl.textContent = "Connected — start talking";
      };

      stopBtn.onclick = () => {
        pc?.close();
        localStream?.getTracks().forEach((t) => t.stop());
        startBtn.disabled = false;
        stopBtn.disabled = true;
        statusEl.textContent = "Stopped";
      };
    </script>
  </body>
</html>
```

---

## 7. Run it

```powershell
.\.venv\Scripts\Activate.ps1
python server.py
```

Open <http://localhost:7860>, click **Start talking**, allow mic access, and speak. You should hear a greeting within ~1 second.

---

## 8. What each component actually does

### 8.1 Transport (`SmallWebRTCTransport`)
- Terminates the WebRTC connection from the browser inside your Python process.
- Emits `AudioRawFrame` (mic in) and consumes `AudioRawFrame` (speaker out).
- Uses Google's public STUN server for NAT traversal — fine for local dev. For prod behind NAT, add a TURN server.

### 8.2 VAD (`SileroVADAnalyzer`)
- Runs a tiny neural net locally on every audio chunk.
- Tells the pipeline "user started speaking" / "user stopped speaking" so STT knows utterance boundaries and the bot knows to **stop talking** when interrupted.
- Zero API cost.

### 8.3 STT (`SonioxSTTService`)
- Opens a WebSocket to Soniox and streams raw PCM.
- Emits **interim** transcripts (updated as words arrive) and **final** transcripts (when Soniox commits an utterance).
- Options worth knowing:
  - `language_hints=["en", "hi"]` — for multilingual conversations
  - `context="…domain terminology…"` — improves rare-word accuracy
  - `endpoint_detection=True` (default) — Soniox marks end-of-utterance

### 8.4 LLM
- Receives the aggregated conversation context each turn.
- Streams tokens back — Pipecat pushes them into TTS **as they arrive**, so TTS can start speaking before the LLM finishes. This is where you save 500-1000ms of latency vs. naive "wait for full response then speak."
- Keep the system prompt SHORT and explicitly tell it "you are being spoken aloud" — LLMs default to markdown/lists which sound terrible.

### 8.5 TTS (`SonioxTTSService`)
- Streaming WebSocket, starts synthesizing after just a few tokens.
- Emits `AudioRawFrame`s at 24kHz (or configurable).
- If the user interrupts, Pipecat drops the in-flight audio frames automatically — no orphaned speech.

### 8.6 Context aggregators
- `context_aggregator.user()` — appends the final STT transcript to the conversation before the LLM sees it.
- `context_aggregator.assistant()` — appends the LLM's response after TTS. Split into two nodes because they run at different points in the pipeline.

---

## 9. Staying inside free limits

| Component | Free budget                                    | Rough voice-minutes                             |
| --------- | ---------------------------------------------- | ----------------------------------------------- |
| Soniox    | Signup credits (check console — usually $10+)  | ~14 hrs TTS @ $0.70/hr, plus STT               |
| Groq      | 1000 req/day, 30 req/min                       | ~1000 turns/day — plenty for dev                |
| Gemini    | 1.5M input tokens/day on Flash                 | Effectively unlimited for voice                 |
| Silero    | Local                                          | Unlimited                                       |
| Transport | Local WebRTC                                   | Unlimited                                       |

**Practical tips:**
- Add short guardrails in your system prompt ("one or two sentences") — every extra token costs both LLM quota and TTS seconds.
- Set `PipelineParams(enable_metrics=True)` and log Soniox usage — the metrics processor tells you exactly how many seconds you've synthesized.
- If you need to demo for hours, switch TTS to local (§10).

---

## 10. Fully-local, forever-free swap

When Soniox credits run out:

```powershell
pip install "pipecat-ai[whisper,piper]"
```

```python
# STT: local Whisper (faster-whisper backend, GPU or CPU)
from pipecat.services.whisper.stt import WhisperSTTService, Model
stt = WhisperSTTService(model=Model.DISTIL_LARGE_V2, device="cpu")

# TTS: local Piper (needs a downloaded voice .onnx file)
from pipecat.services.piper.tts import PiperTTSService
tts = PiperTTSService(base_url="http://localhost:5000")  # run piper-http locally
```

Trade-off: local STT/TTS costs you latency (200-500ms extra) and CPU/RAM. Fine for prototyping; iffy for production.

---

## 11. Extending the agent

Once the loop works, layer these in — each is a self-contained Pipecat processor you drop into the pipeline:

1. **Function calling** — let the LLM call Python functions ("check_weather", "book_meeting"). Groq and Gemini both support tool use; wire it via `LLMService.register_function()`.
2. **Memory** — swap `OpenAILLMContext` for a persistent store (SQLite, Redis) so conversations survive restarts.
3. **RAG** — insert a `FunctionFilter` before the LLM that retrieves relevant docs and stuffs them into context.
4. **Multi-language** — set `language_hints=["en", "hi", "es"]` on Soniox and Soniox will auto-detect + switch, including mid-sentence code-switching.
5. **Metrics / observability** — add `PipelineMetricsProcessor`; ship latencies to a local Grafana or just print them.
6. **Deploy** — swap `SmallWebRTCTransport` for `DailyTransport` (free tier: 10k participant-minutes/mo) to get a hosted WebRTC endpoint with TURN included.

---

## 12. Troubleshooting

| Symptom                                       | Likely cause / fix                                                                 |
| --------------------------------------------- | ---------------------------------------------------------------------------------- |
| "No module named `pipecat.services.soniox`"   | Missing extra — `pip install "pipecat-ai[soniox]"`                                 |
| Silero import error on Windows                | `pip install "pipecat-ai[silero]"` — pulls `onnxruntime`                           |
| Mic works, no transcript                      | Check `SONIOX_API_KEY`; watch server logs for websocket errors                     |
| Bot speaks over user                          | `allow_interruptions=True` must be set + VAD must be firing (check Silero install) |
| Long silence before first reply               | Ollama warming up its model on CPU — switch to Groq or a smaller local model       |
| Browser mic prompt never appears              | You must serve over `http://localhost` or HTTPS; browsers block mic on plain IPs   |
| Audio choppy                                  | Network jitter — for local dev usually harmless; check CPU is not pegged           |

---

## 13. Reference links

- Pipecat repo: <https://github.com/pipecat-ai/pipecat>
- Pipecat Soniox STT docs: <https://docs.pipecat.ai/api-reference/server/services/stt/soniox>
- Pipecat Soniox TTS docs: <https://docs.pipecat.ai/api-reference/server/services/tts/soniox>
- Soniox blog — Pipecat integration walkthrough: <https://soniox.com/blog/voice-bot-with-pipecat-and-soniox>
- Soniox docs — Pipecat: <https://soniox.com/docs/integrations/pipecat>
- Soniox pricing: <https://soniox.com/pricing>
- Groq console: <https://console.groq.com>
- Google AI Studio (Gemini): <https://aistudio.google.com>
- Ollama: <https://ollama.com>

---

## 14. Suggested build order

1. ✅ Get `bot.py` running end-to-end with Groq + Soniox — greeting audible.
2. Tune the system prompt so replies feel natural aloud (short, no lists).
3. Add function calling for one real capability (e.g. current time).
4. Add persistent memory via SQLite.
5. Swap to Ollama and measure the latency delta.
6. When happy, deploy behind Daily or on a small VPS.

Ship v1 in a day. Iterate the personality, tools, and memory over the week.
