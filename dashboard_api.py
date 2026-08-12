"""Read-only FastAPI service exposing per-client cost data from Supabase.

Auth model: a single shared password (DASHBOARD_PASSWORD env var). The client
sends it via the X-Dashboard-Password header on every request. Simple, matches
'CEO + managers only' scope.

Endpoints:
  POST /api/auth               → verify password (used by UI to gate access)
  GET  /api/clients            → list all clients with total spend
  GET  /api/clients/{id}       → single client + their sessions
  GET  /api/sessions/{id}      → session detail + all turns
  GET  /api/sessions/current   → latest session (optionally scoped to a client)

Run standalone:
    uvicorn dashboard_api:app --port 8081 --reload

Or let bot.py spawn it in a background thread (see bot.py main).
"""
from __future__ import annotations

import os
import sys
import threading

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from db import get_pool, close_pool


# Structured JSON logs, same as bot.py. When the dashboard runs as its
# own Render service this configures its logger; when imported by bot.py
# the config there wins (loguru is a singleton, last add() reconfigures).
logger.remove()
logger.add(sys.stdout, serialize=True)


# Sentry — same no-op-if-unset pattern as bot.py.
_sentry_dsn = os.environ.get("SENTRY_DSN")
if _sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=0.1,
        send_default_pii=False,
        environment=os.environ.get("SENTRY_ENV", "production"),
    )
    logger.info("Sentry initialised")


app = FastAPI(title="Voice Agent Metrics")

# CORS allowlist. Set DASHBOARD_ALLOWED_ORIGIN as a comma-separated list of
# origins in .env / Render env (e.g. "https://dash.mysite.com,http://localhost:5173").
# Falls back to localhost dev origins only — never allow "*" in production.
_default_dev_origins = "http://localhost:5173,http://127.0.0.1:5173"
_origins = [
    o.strip()
    for o in os.environ.get("DASHBOARD_ALLOWED_ORIGIN", _default_dev_origins).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_pool()


@app.get("/health")
def health() -> dict:
    """Unauthenticated health probe for Render / load balancers."""
    return {"ok": True}


def require_password(x_dashboard_password: str | None = Header(None)) -> None:
    """FastAPI dependency: enforce the shared password on protected endpoints."""
    expected = os.environ.get("DASHBOARD_PASSWORD")
    if not expected:
        raise HTTPException(500, "DASHBOARD_PASSWORD is not configured on server")
    if x_dashboard_password != expected:
        raise HTTPException(401, "Invalid dashboard password")


async def _fetch_all(sql: str, *args) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def _fetch_one(sql: str, *args) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


@app.post("/api/auth")
def check_auth(_: None = Depends(require_password)) -> dict:
    return {"ok": True}


@app.get("/api/clients", dependencies=[Depends(require_password)])
async def list_clients() -> list[dict]:
    return await _fetch_all(
        """SELECT c.id, c.name, c.created_at,
                  COALESCE(SUM(s.total_cost_usd), 0)::float AS total_cost_usd,
                  COALESCE(SUM(s.turn_count), 0)::int      AS total_turns,
                  COUNT(s.id)::int                          AS session_count,
                  MAX(s.ended_at)                           AS last_active
             FROM clients c
             LEFT JOIN sessions s ON s.client_id = c.id
            GROUP BY c.id, c.name, c.created_at
            ORDER BY total_cost_usd DESC"""
    )


@app.get("/api/clients/{client_id}", dependencies=[Depends(require_password)])
async def client_detail(client_id: str) -> dict:
    client = await _fetch_one(
        "SELECT id, name, created_at FROM clients WHERE id = $1", client_id
    )
    if not client:
        raise HTTPException(404, "Client not found")
    client["sessions"] = await _fetch_all(
        """SELECT id, started_at, ended_at, turn_count,
                  total_cost_usd::float AS total_cost_usd,
                  intent
             FROM sessions
            WHERE client_id = $1
            ORDER BY started_at DESC
            LIMIT 200""",
        client_id,
    )
    client["total_cost_usd"] = sum(s["total_cost_usd"] for s in client["sessions"])
    client["total_turns"] = sum(s["turn_count"] for s in client["sessions"])
    return client


@app.get("/api/sessions/{session_id}", dependencies=[Depends(require_password)])
async def session_detail(session_id: str) -> dict:
    session = await _fetch_one(
        """SELECT id, client_id, started_at, ended_at, turn_count,
                  total_cost_usd::float AS total_cost_usd,
                  intent
             FROM sessions WHERE id = $1""",
        session_id,
    )
    if not session:
        raise HTTPException(404, "Session not found")
    session["turns"] = await _fetch_all(
        """SELECT turn_index,
                  EXTRACT(EPOCH FROM ts)::float AS ts,
                  llm_prompt_tokens, llm_completion_tokens,
                  llm_cost_usd::float AS llm_cost_usd,
                  tts_chars,
                  tts_cost_usd::float AS tts_cost_usd,
                  stt_seconds::float  AS stt_seconds,
                  stt_cost_usd::float AS stt_cost_usd,
                  total_cost_usd::float AS total_cost_usd,
                  llm_ttfb_ms::float, tts_ttfb_ms::float, end_to_end_ms::float
             FROM turns WHERE session_id = $1 ORDER BY turn_index""",
        session_id,
    )
    return session


@app.get("/api/sessions/current", dependencies=[Depends(require_password)])
async def current_session(
    client_id: str | None = Query(None, description="Scope to a specific client"),
) -> dict | None:
    if client_id:
        session = await _fetch_one(
            """SELECT id FROM sessions
                WHERE client_id = $1
                ORDER BY started_at DESC LIMIT 1""",
            client_id,
        )
    else:
        session = await _fetch_one(
            "SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1"
        )
    if not session:
        return None
    return await session_detail(session["id"])


def start_in_thread(port: int | None = None) -> threading.Thread:
    """Launch this API in a daemon thread. Called from bot.py."""
    import uvicorn

    port = port or int(os.environ.get("DASHBOARD_PORT", "8081"))

    def _run() -> None:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

    t = threading.Thread(target=_run, name="dashboard-api", daemon=True)
    t.start()
    return t
