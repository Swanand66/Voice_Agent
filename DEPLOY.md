# Deploy Guide — Vercel (frontend) + Render (backend)

Two independent deploys. Do the backend first so you can point the frontend at it.

---

## Part 1 — Backend on Render

### 1.1 Push the repo to GitHub

Render deploys from a Git repo. From the project root:

```powershell
git init
git add .
git commit -m "Initial voice agent"
# create an empty repo on GitHub, then:
git remote add origin https://github.com/<you>/voice-agent.git
git branch -M main
git push -u origin main
```

Your `.env` file is gitignored — good. Real keys will be set on Render directly.

### 1.2 Create the Render service

1. Go to <https://dashboard.render.com/> → sign up (free, no card).
2. **New +** → **Blueprint** → connect your GitHub repo.
3. Render detects `render.yaml` at the root and proposes the `voice-agent-bot` service. Approve it.
4. When prompted for env vars, paste:
   - `SONIOX_API_KEY` = your Soniox key
   - `GROQ_API_KEY` = your Groq key
5. Deploy.

First build takes ~5-8 minutes (Silero + ONNX are chunky).

### 1.3 Note the URL

When the service is live you'll get a URL like `https://voice-agent-bot.onrender.com`. Test it:

```
https://voice-agent-bot.onrender.com/          → should return the Pipecat runner homepage
https://voice-agent-bot.onrender.com/client/   → the built-in demo UI (works standalone)
```

If that page loads, backend signaling is up. **Keep this URL — you'll need it for the frontend.**

### 1.4 Known limits on Render free tier

| Limit                    | Impact                                                    |
| ------------------------ | --------------------------------------------------------- |
| Sleeps after 15 min idle | First connect after sleep takes 30-60s to wake            |
| 512 MB RAM               | Tight but works. If you hit OOM, upgrade to Starter ($7/mo) |
| 750 hrs/month total      | Enough for one always-running service                     |
| **UDP not proxied**      | ⚠️ WebRTC media flow may fail — see §3                    |

---

## Part 2 — Frontend on Vercel

### 2.1 Deploy

Easiest path — the Vercel CLI:

```powershell
npm install -g vercel
cd ui
vercel                       # first time: link the project
vercel --prod                # actual production deploy
```

Or via the Vercel dashboard:

1. <https://vercel.com/new> → import your GitHub repo.
2. **Root Directory:** set to `ui`.
3. **Framework Preset:** Vite (autodetected).
4. **Environment Variables:** add
   - `VITE_BOT_URL` = `https://voice-agent-bot.onrender.com`  *(no trailing slash)*
5. Deploy.

Vercel picks up `ui/vercel.json` for the SPA rewrite so refreshes don't 404.

### 2.2 Redeploy after changing env vars

If you set `VITE_BOT_URL` after the first deploy, you must trigger a rebuild — Vite bakes the value in at build time. In the Vercel UI: **Deployments** → latest → **Redeploy**.

---

## Part 3 — The WebRTC / UDP problem on Render

**Symptom:** the "Connecting…" state hangs, or you connect but hear nothing.

**Why:** WebRTC audio is UDP-based. Render's load balancer only forwards TCP (HTTP/WebSocket). The HTTP `/api/offer` handshake succeeds, but the actual RTP audio stream can't traverse Render's proxy.

**Fixes, in order of effort:**

### A) Add a public TURN server (easiest fix, sometimes enough)

TURN servers relay UDP over TCP when direct peer connection fails. Free options:

- **Metered.ca** — free TURN, 500 MB/month: <https://www.metered.ca/tools/openrelay/>
- **Twilio** — pay-as-you-go, ~$0.40/GB

Once you have TURN credentials, set them on the SmallWebRTCTransport client:

```tsx
// ui/src/components/voice-agent.tsx
new SmallWebRTCTransport({
  iceServers: [
    { urls: "stun:stun.l.google.com:19302" },
    {
      urls: "turn:global.relay.metered.ca:80",
      username: "<from metered.ca>",
      credential: "<from metered.ca>",
    },
  ],
});
```

TURN relaying does everything over TCP:443, which Render will pass through.

### B) Switch transport to Daily (recommended for production)

Daily.co handles all WebRTC infra. Free tier: 10,000 participant-minutes/month.

1. Sign up at <https://dashboard.daily.co/>, get a `DAILY_API_KEY`.
2. On the Python side, install `pipecat-ai[daily]` and swap `TransportParams` for `DailyParams` in `bot.py`.
3. On the JS side, swap `@pipecat-ai/small-webrtc-transport` for `@pipecat-ai/daily-transport`.

The rest of the code stays the same. This is what Pipecat officially recommends for production.

### C) Fallback: ngrok

If you can't get either A or B working and just need a demo:

```powershell
# On your local PC, run the bot
python bot.py
# In another terminal, expose it publicly
ngrok http 7860
```

ngrok gives you a `https://xxx.ngrok-free.app` URL — use that as `VITE_BOT_URL` on Vercel. Free, works instantly, but the bot is only up while your PC is on.

---

## Part 4 — Troubleshooting

| Symptom                                     | Likely fix                                                    |
| ------------------------------------------- | ------------------------------------------------------------- |
| Build fails on Render: "onnxruntime" wheel  | Add `PYTHON_VERSION=3.12` in Render env vars (older Pythons don't have wheels) |
| Frontend loads but "Connecting…" hangs      | Check browser console. If "CORS" — the Pipecat runner allows all origins by default; verify with `curl -H "Origin: https://your.vercel.app" https://your-bot.onrender.com/api/offer -X OPTIONS` |
| Connect works but no audio                  | UDP / WebRTC media issue — see §3                             |
| "Failed to load prebuilt frontend"          | The `pip install pipecat-ai-prebuilt` in `buildCommand` failed — check Render build logs |
| Bot idle-times out mid-call                 | Bump `PIPECAT_IDLE_TIMEOUT_SECS` in Render env               |
| First call after sleep takes forever        | Free tier cold start; either upgrade to Starter, or ping `/` every 10 min from a cron |

---

## Part 5 — Cost check

| Component        | Cost                                                    |
| ---------------- | ------------------------------------------------------- |
| Vercel (Hobby)   | Free                                                    |
| Render (Free)    | Free — sleeps after 15 min idle                         |
| Soniox STT/TTS   | Signup credits, then metered                            |
| Groq LLM         | Free tier: 1000 req/day, 30 req/min                     |
| **Total to demo** | **$0** (until Soniox credits run out)                  |

Upgrade path if you need always-on:
- Render Starter: $7/mo (no sleep, 512 MB → 512 MB, same tier for now)
- Daily.co: $0 up to 10k min/mo, then $0.004/min
