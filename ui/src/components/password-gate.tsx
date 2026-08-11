import { useState, type FormEvent } from "react";
import { Lock } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  getStoredPassword,
  metricsApi,
  setStoredPassword,
} from "@/lib/metrics-api";

type Props = {
  children: React.ReactNode;
};

export function PasswordGate({ children }: Props) {
  const [authed, setAuthed] = useState(() => !!getStoredPassword());
  const [pw, setPw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await metricsApi.verifyPassword(pw);
      setStoredPassword(pw);
      setAuthed(true);
    } catch (err) {
      setError((err as Error).message || "Invalid password");
    } finally {
      setBusy(false);
    }
  };

  if (authed) return <>{children}</>;

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-4 rounded-2xl border border-border/40 bg-background/60 p-6 backdrop-blur"
      >
        <div className="flex items-center gap-3">
          <div className="rounded-full bg-white/5 p-2">
            <Lock className="h-4 w-4 text-muted-foreground" />
          </div>
          <div>
            <div className="text-sm font-medium">Dashboard access</div>
            <div className="text-xs text-muted-foreground">
              CEO and managers only
            </div>
          </div>
        </div>
        <div>
          <label htmlFor="pw" className="sr-only">
            Password
          </label>
          <input
            id="pw"
            type="password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            placeholder="Enter password"
            autoFocus
            className="w-full rounded-lg border border-border/60 bg-background/60 px-3 py-2 text-sm outline-none focus:border-border"
          />
        </div>
        {error && <div className="text-xs text-red-400">{error}</div>}
        <Button type="submit" disabled={busy || !pw} className="w-full">
          {busy ? "Checking…" : "Continue"}
        </Button>
      </form>
    </div>
  );
}
