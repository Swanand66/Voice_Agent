import { useEffect, useState } from "react";

import { VoiceAgent } from "@/components/voice-agent";
import { Dashboard } from "@/components/dashboard";

function useHashRoute(): string {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export default function App() {
  const route = useHashRoute();
  if (route === "#/dashboard") return <Dashboard />;
  return <VoiceAgent />;
}
