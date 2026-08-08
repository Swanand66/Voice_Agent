import { useEffect, useMemo, useState } from "react";
import { Mic, MicOff, Loader2 } from "lucide-react";

import { PipecatClient, RTVIEvent } from "@pipecat-ai/client-js";
import {
  PipecatClientAudio,
  PipecatClientProvider,
  usePipecatClient,
  usePipecatClientTransportState,
  useRTVIClientEvent,
} from "@pipecat-ai/client-react";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";

import { GradientOrb } from "@/components/ui/gradient-orb";
import { Button } from "@/components/ui/button";
import { StarButton } from "@/components/ui/star-button";
import { DotPattern } from "@/components/ui/dot-pattern";
import { cn } from "@/lib/utils";

type BotState = "idle" | "connecting" | "listening" | "thinking" | "speaking";

function useBotState(): BotState {
  const transportState = usePipecatClientTransportState();
  const [userSpeaking, setUserSpeaking] = useState(false);
  const [botSpeaking, setBotSpeaking] = useState(false);

  useRTVIClientEvent(RTVIEvent.UserStartedSpeaking, () => setUserSpeaking(true));
  useRTVIClientEvent(RTVIEvent.UserStoppedSpeaking, () => setUserSpeaking(false));
  useRTVIClientEvent(RTVIEvent.BotStartedSpeaking, () => setBotSpeaking(true));
  useRTVIClientEvent(RTVIEvent.BotStoppedSpeaking, () => setBotSpeaking(false));

  if (transportState === "disconnected" || transportState === "initialized") return "idle";
  if (
    transportState === "connecting" ||
    transportState === "authenticating" ||
    transportState === "connected"
  )
    return "connecting";
  if (botSpeaking) return "speaking";
  if (userSpeaking) return "listening";
  return "thinking";
}

const stateConfig: Record<BotState, { label: string; hue: number; rotation: number; noise: number }> = {
  idle:       { label: "Tap to talk",      hue:   0, rotation: 0.35, noise: 0.75 },
  connecting: { label: "Connecting…",      hue:  40, rotation: 1.2,  noise: 1.0  },
  listening:  { label: "Listening",        hue: 140, rotation: 0.5,  noise: 0.95 },
  thinking:   { label: "Thinking",         hue:  60, rotation: 0.8,  noise: 0.7  },
  speaking:   { label: "Speaking",         hue: 240, rotation: 0.7,  noise: 1.15 },
};

function VoiceAgentInner() {
  const client = usePipecatClient();
  const state = useBotState();
  const cfg = stateConfig[state];

  const [isMuted, setIsMuted] = useState(false);

  const connected = state !== "idle" && state !== "connecting";

  const handleToggle = async () => {
    if (!client) return;
    if (connected) {
      await client.disconnect();
      return;
    }
    if (state === "connecting") return;
    try {
      const botUrl = (import.meta.env.VITE_BOT_URL ?? "").replace(/\/$/, "");
      const endpoint = botUrl ? `${botUrl}/api/offer` : "/api/offer";
      await client.connect({ webrtcRequestParams: { endpoint } });
    } catch (err) {
      console.error("Failed to connect:", err);
    }
  };

  const toggleMute = () => {
    if (!client) return;
    const next = !isMuted;
    setIsMuted(next);
    client.enableMic(!next);
  };

  return (
    <div className="relative h-screen w-screen overflow-hidden bg-background">
      <div className="absolute inset-0">
        <GradientOrb
          config={{
            background: "#0a0a0a",
            hue: cfg.hue,
            rotationSpeed: cfg.rotation,
            noiseScale: cfg.noise,
          }}
        />
      </div>

      <DotPattern
        cx={1}
        cy={1}
        cr={1}
        className={cn(
          "fill-white/25",
          "[mask-image:radial-gradient(900px_circle_at_center,white,transparent)]"
        )}
      />

      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-black/30 via-transparent to-black/60" />

      <div className="relative z-10 flex h-full flex-col items-center justify-between px-4 py-6 sm:px-6 sm:py-10">
        <header className="flex w-full max-w-4xl items-center justify-between gap-3">
          <div className="text-xs font-medium tracking-widest text-muted-foreground sm:text-sm">
            MAYA
          </div>
          <div className="flex shrink-0 items-center gap-2 rounded-full border border-border/50 bg-background/40 px-2.5 py-1 text-[11px] text-muted-foreground backdrop-blur sm:px-3 sm:text-xs">
            <span
              className={`h-2 w-2 shrink-0 rounded-full ${
                state === "idle" ? "bg-muted-foreground" :
                state === "connecting" ? "bg-yellow-400 animate-pulse" :
                state === "speaking" ? "bg-blue-400 animate-pulse" :
                state === "listening" ? "bg-green-400 animate-pulse" :
                "bg-orange-400 animate-pulse"
              }`}
            />
            {cfg.label}
          </div>
        </header>

        <div className="flex w-full flex-col items-center gap-6 text-center sm:gap-8">
          <div className="w-full max-w-xl px-2">
            <h1 className="text-2xl font-medium tracking-tight text-foreground break-words sm:text-4xl md:text-5xl">
              {connected ? cfg.label : "Say hello to Maya."}
            </h1>
            <p className="mt-3 text-sm text-muted-foreground">
              {connected
                ? "Talk naturally. Interrupt anytime."
                : "Pipecat · Soniox · Groq"}
            </p>
          </div>
        </div>

        <footer className="flex items-center gap-3">
          {connected && (
            <Button
              variant="outline"
              size="icon"
              onClick={toggleMute}
              className="h-14 w-14 rounded-full border-white/20 bg-background/40 backdrop-blur hover:bg-background/60"
              aria-label={isMuted ? "Unmute" : "Mute"}
            >
              {isMuted ? <MicOff className="h-5 w-5" /> : <Mic className="h-5 w-5" />}
            </Button>
          )}

          <StarButton
            onClick={handleToggle}
            disabled={state === "connecting"}
            lightColor="transparent"
            backgroundColor="rgba(0,0,0,0.08)"
            duration={connected ? 1.5 : 3}
            className="h-12 px-8 text-base bg-white shadow-xl shadow-black/40 font-semibold"
          >
            {state === "connecting" ? (
              <>
                <Loader2
                  className="h-4 w-4 animate-spin"
                  style={{ color: "#262626" }}
                />
                Connecting
              </>
            ) : connected ? (
              "End call"
            ) : (
              "Start talking"
            )}
          </StarButton>
        </footer>
      </div>

      <PipecatClientAudio />
    </div>
  );
}

function buildIceServers(): RTCIceServer[] {
  const servers: RTCIceServer[] = [{ urls: "stun:stun.l.google.com:19302" }];
  const turnUrl = import.meta.env.VITE_TURN_URL;
  const turnUser = import.meta.env.VITE_TURN_USERNAME;
  const turnCred = import.meta.env.VITE_TURN_CREDENTIAL;
  if (turnUrl && turnUser && turnCred) {
    servers.push({ urls: turnUrl, username: turnUser, credential: turnCred });
  }
  return servers;
}

export function VoiceAgent() {
  const client = useMemo(
    () =>
      new PipecatClient({
        transport: new SmallWebRTCTransport({
          iceServers: buildIceServers(),
          // Explicit mic constraints — echo cancellation, noise suppression,
          // and auto-gain control help prevent bot audio bleeding back into
          // the mic and triggering false interruptions.
          mediaManager: undefined,
        }),
        enableMic: true,
        enableCam: false,
      }),
    []
  );

  useEffect(() => {
    // Override default getUserMedia constraints to explicitly enable browser
    // audio processing (echo cancellation, noise suppression, AGC). These are
    // usually on by default but some browsers/devices disable them; force on.
    const originalGetUserMedia = navigator.mediaDevices.getUserMedia.bind(
      navigator.mediaDevices
    );
    navigator.mediaDevices.getUserMedia = (constraints) => {
      const audio = constraints?.audio;
      const stricter: MediaTrackConstraints = {
        echoCancellation: { ideal: true },
        noiseSuppression: { ideal: true },
        autoGainControl: { ideal: true },
        ...(typeof audio === "object" && audio !== null ? audio : {}),
      };
      return originalGetUserMedia({ ...constraints, audio: stricter });
    };
    return () => {
      navigator.mediaDevices.getUserMedia = originalGetUserMedia;
      client.disconnect().catch(() => {});
    };
  }, [client]);

  return (
    <PipecatClientProvider client={client}>
      <VoiceAgentInner />
    </PipecatClientProvider>
  );
}
