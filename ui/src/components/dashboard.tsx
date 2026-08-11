import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { PasswordGate } from "@/components/password-gate";
import {
  clearStoredPassword,
  formatDate,
  formatTime,
  formatUsd,
  metricsApi,
  type Client,
  type Intent,
  type Session,
  type Turn,
} from "@/lib/metrics-api";

const INTENT_STYLE: Record<Intent, string> = {
  booking: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  inquiry: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  callback: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  complaint: "bg-red-500/15 text-red-300 border-red-500/30",
  chat: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  refused: "bg-zinc-500/15 text-zinc-300 border-zinc-500/30",
  other: "bg-white/5 text-muted-foreground border-white/10",
};

function IntentPill({ intent }: { intent: Intent | null }) {
  if (!intent) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${INTENT_STYLE[intent]}`}
    >
      {intent}
    </span>
  );
}

type View =
  | { kind: "clients" }
  | { kind: "client"; clientId: string }
  | { kind: "session"; sessionId: string; from?: string };

export function Dashboard() {
  return (
    <PasswordGate>
      <DashboardBody />
    </PasswordGate>
  );
}

function DashboardBody() {
  const [view, setView] = useState<View>({ kind: "clients" });
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="min-h-screen w-screen bg-background text-foreground">
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-12">
        <header className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-medium tracking-tight sm:text-3xl">
              Cost dashboard
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Per-client spend across STT · LLM · TTS
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                clearStoredPassword();
                window.location.reload();
              }}
            >
              Log out
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                window.location.hash = "";
              }}
            >
              <ArrowLeft className="mr-2 h-4 w-4" />
              Back to call
            </Button>
          </div>
        </header>

        {error && (
          <div className="mb-6 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
            {error}
          </div>
        )}

        {view.kind === "clients" && (
          <ClientList
            onSelect={(id) => setView({ kind: "client", clientId: id })}
            onError={setError}
          />
        )}
        {view.kind === "client" && (
          <ClientDetail
            clientId={view.clientId}
            onBack={() => setView({ kind: "clients" })}
            onSelectSession={(sid) =>
              setView({ kind: "session", sessionId: sid, from: view.clientId })
            }
            onError={setError}
          />
        )}
        {view.kind === "session" && (
          <SessionDetail
            sessionId={view.sessionId}
            onBack={() =>
              view.from
                ? setView({ kind: "client", clientId: view.from })
                : setView({ kind: "clients" })
            }
            onError={setError}
          />
        )}
      </div>
    </div>
  );
}

function ClientList({
  onSelect,
  onError,
}: {
  onSelect: (id: string) => void;
  onError: (msg: string) => void;
}) {
  const [clients, setClients] = useState<Client[] | null>(null);

  useEffect(() => {
    metricsApi
      .listClients()
      .then(setClients)
      .catch((e) => onError((e as Error).message));
  }, [onError]);

  if (clients === null) {
    return <div className="text-sm text-muted-foreground">Loading…</div>;
  }
  if (clients.length === 0) {
    return (
      <div className="rounded-lg border border-border/40 bg-background/40 px-6 py-12 text-center text-sm text-muted-foreground">
        No clients yet. Add one via SQL or wait for the first call to come in.
      </div>
    );
  }

  const grandTotal = clients.reduce((a, c) => a + c.total_cost_usd, 0);

  return (
    <div>
      <div className="mb-6 rounded-xl border border-border/40 bg-background/40 px-4 py-3">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          All clients — total spend
        </div>
        <div className="mt-1 text-2xl font-medium tabular-nums">
          {formatUsd(grandTotal)}
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-border/40">
        <table className="w-full text-sm">
          <thead className="bg-white/5 text-left text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">Client</th>
              <th className="px-4 py-3 font-medium">Last active</th>
              <th className="px-4 py-3 text-right font-medium">Sessions</th>
              <th className="px-4 py-3 text-right font-medium">Turns</th>
              <th className="px-4 py-3 text-right font-medium">Total</th>
            </tr>
          </thead>
          <tbody>
            {clients.map((c) => (
              <tr
                key={c.id}
                className="cursor-pointer border-t border-border/30 transition hover:bg-white/5"
                onClick={() => onSelect(c.id)}
              >
                <td className="px-4 py-3">
                  <div className="font-medium">{c.name}</div>
                  <div className="font-mono text-xs text-muted-foreground">
                    {c.id}
                  </div>
                </td>
                <td className="px-4 py-3 text-xs text-muted-foreground">
                  {c.last_active ? formatDate(c.last_active) : "—"}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {c.session_count}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {c.total_turns}
                </td>
                <td className="px-4 py-3 text-right font-medium tabular-nums">
                  {formatUsd(c.total_cost_usd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ClientDetail({
  clientId,
  onBack,
  onSelectSession,
  onError,
}: {
  clientId: string;
  onBack: () => void;
  onSelectSession: (sessionId: string) => void;
  onError: (msg: string) => void;
}) {
  const [client, setClient] = useState<Client | null>(null);

  useEffect(() => {
    metricsApi
      .clientDetail(clientId)
      .then(setClient)
      .catch((e) => onError((e as Error).message));
  }, [clientId, onError]);

  if (!client) return <div className="text-sm text-muted-foreground">Loading…</div>;

  const sessions = client.sessions ?? [];

  return (
    <div>
      <Button variant="ghost" size="sm" onClick={onBack} className="mb-4">
        <ArrowLeft className="mr-2 h-4 w-4" />
        All clients
      </Button>

      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Client
          </div>
          <div className="text-2xl font-medium">{client.name}</div>
          <div className="font-mono text-xs text-muted-foreground">
            {client.id}
          </div>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <SummaryCard label="Total" value={formatUsd(client.total_cost_usd)} />
          <SummaryCard label="Sessions" value={String(sessions.length)} />
          <SummaryCard label="Turns" value={String(client.total_turns)} />
        </div>
      </div>

      {sessions.length === 0 ? (
        <div className="rounded-lg border border-border/40 bg-background/40 px-6 py-12 text-center text-sm text-muted-foreground">
          No sessions for this client yet.
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border/40">
          <table className="w-full text-sm">
            <thead className="bg-white/5 text-left text-xs uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Session</th>
                <th className="px-4 py-3 font-medium">Intent</th>
                <th className="px-4 py-3 font-medium">Started</th>
                <th className="px-4 py-3 text-right font-medium">Turns</th>
                <th className="px-4 py-3 text-right font-medium">Total</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr
                  key={s.id}
                  className="cursor-pointer border-t border-border/30 transition hover:bg-white/5"
                  onClick={() => onSelectSession(s.id)}
                >
                  <td className="px-4 py-3 font-mono text-xs">{s.id}</td>
                  <td className="px-4 py-3">
                    <IntentPill intent={s.intent} />
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {formatDate(s.started_at)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {s.turn_count}
                  </td>
                  <td className="px-4 py-3 text-right font-medium tabular-nums">
                    {formatUsd(s.total_cost_usd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SessionDetail({
  sessionId,
  onBack,
  onError,
}: {
  sessionId: string;
  onBack: () => void;
  onError: (msg: string) => void;
}) {
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    metricsApi
      .sessionDetail(sessionId)
      .then(setSession)
      .catch((e) => onError((e as Error).message));
  }, [sessionId, onError]);

  if (!session) return <div className="text-sm text-muted-foreground">Loading…</div>;

  const turns = session.turns ?? [];
  const llmTotal = turns.reduce((a, t) => a + t.llm_cost_usd, 0);
  const ttsTotal = turns.reduce((a, t) => a + t.tts_cost_usd, 0);
  const sttTotal = turns.reduce((a, t) => a + t.stt_cost_usd, 0);

  return (
    <div>
      <Button variant="ghost" size="sm" onClick={onBack} className="mb-4">
        <ArrowLeft className="mr-2 h-4 w-4" />
        Back
      </Button>

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <SummaryCard label="Total" value={formatUsd(session.total_cost_usd)} />
        <SummaryCard label="LLM" value={formatUsd(llmTotal)} />
        <SummaryCard label="TTS" value={formatUsd(ttsTotal)} />
        <SummaryCard label="STT" value={formatUsd(sttTotal)} />
      </div>

      <div className="mb-4 text-xs text-muted-foreground">
        Session <span className="font-mono">{session.id}</span> ·{" "}
        {formatDate(session.started_at)}
        {session.client_id && (
          <> · client <span className="font-mono">{session.client_id}</span></>
        )}
      </div>

      <div className="overflow-x-auto rounded-xl border border-border/40">
        <table className="w-full text-sm">
          <thead className="bg-white/5 text-left text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">#</th>
              <th className="px-3 py-2 font-medium">Time</th>
              <th className="px-3 py-2 font-medium text-right">LLM tok</th>
              <th className="px-3 py-2 font-medium text-right">LLM $</th>
              <th className="px-3 py-2 font-medium text-right">TTS chars</th>
              <th className="px-3 py-2 font-medium text-right">TTS $</th>
              <th className="px-3 py-2 font-medium text-right">STT sec</th>
              <th className="px-3 py-2 font-medium text-right">STT $</th>
              <th className="px-3 py-2 font-medium text-right">E2E ms</th>
              <th className="px-3 py-2 font-medium text-right">LLM TTFB</th>
              <th className="px-3 py-2 font-medium text-right">TTS TTFB</th>
              <th className="px-3 py-2 font-medium text-right">Total</th>
            </tr>
          </thead>
          <tbody>
            {turns.map((t) => (
              <TurnRow key={t.turn_index} turn={t} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TurnRow({ turn: t }: { turn: Turn }) {
  const ms = (v: number) => (v > 0 ? `${Math.round(v)}` : "—");
  return (
    <tr className="border-t border-border/30">
      <td className="px-3 py-2 tabular-nums text-muted-foreground">
        {t.turn_index}
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">
        {formatTime(t.ts)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {t.llm_prompt_tokens}/{t.llm_completion_tokens}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {formatUsd(t.llm_cost_usd)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{t.tts_chars}</td>
      <td className="px-3 py-2 text-right tabular-nums">
        {formatUsd(t.tts_cost_usd)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {t.stt_seconds.toFixed(2)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {formatUsd(t.stt_cost_usd)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {ms(t.end_to_end_ms)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
        {ms(t.llm_ttfb_ms)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
        {ms(t.tts_ttfb_ms)}
      </td>
      <td className="px-3 py-2 text-right font-medium tabular-nums">
        {formatUsd(t.total_cost_usd)}
      </td>
    </tr>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border/40 bg-background/40 px-4 py-3">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-lg font-medium tabular-nums">{value}</div>
    </div>
  );
}
