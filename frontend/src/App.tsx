import { useEffect, useState } from "react";
import { Chat } from "./components/Chat";
import { getHealth } from "./lib/api";
import type { HealthResponse } from "./lib/types";

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [unreachable, setUnreachable] = useState(false);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setUnreachable(true));
  }, []);

  return (
    <main>
      <header>
        <h1>DTSG</h1>
        <p className="tagline">Dynamic Temporal State Graph</p>
        <p className="status">
          {unreachable && <span className="bad">API unreachable</span>}
          {health && (
            <>
              <span className={health.database ? "good" : "bad"}>
                database {health.database ? "connected" : "unreachable"}
              </span>
              <span className="phase">phase {health.phase}</span>
            </>
          )}
        </p>
      </header>
      <Chat />
    </main>
  );
}
