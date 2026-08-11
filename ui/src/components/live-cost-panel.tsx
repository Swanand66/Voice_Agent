import { useEffect, useState } from "react";

import { metricsApi, formatUsd, type Session } from "@/lib/metrics-api";

type Props = {
  active: boolean;
};

export function LiveCostPanel({ active }: Props) {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;

    const tick = async () => {
      try {
        const data = await metricsApi.currentSession();
        if (!cancelled) {
          setSession(data);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    };

    tick();
    const id = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [active]);

  if (!active) return null;

  const lastTurn = session?.turns?.[session.turns.length - 1];

  return (
    <div className="pointer-events-auto rounded-xl border border-white/10 bg-black/50 px-4 py-3 text-xs text-white/90 backdrop-blur">
      <div className="mb-2 flex items-center justify-between gap-4">
        <span className="text-[10px] uppercase tracking-widest text-white/50">
          Live cost
        </span>
        {error && (
          <span className="text-[10px] text-red-400" title={error}>
            offline
          </span>
        )}
      </div>
      <div className="grid grid-cols-3 gap-4 tabular-nums">
        <Stat label="Session" value={formatUsd(session?.total_cost_usd ?? 0)} />
        <Stat label="Turns" value={String(session?.turn_count ?? 0)} />
        <Stat
          label="Last turn"
          value={formatUsd(lastTurn?.total_cost_usd ?? 0)}
        />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-white/40">
        {label}
      </div>
      <div className="text-sm font-medium">{value}</div>
    </div>
  );
}
