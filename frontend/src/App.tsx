import { useEffect, useState } from "react";
import { Chat } from "./components/Chat";
import { Timeline } from "./components/Timeline";
import { getHealth } from "./lib/api";
import type { HealthResponse } from "./lib/types";

type Tab = "chat" | "timeline";

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [tab, setTab] = useState<Tab>("chat");
  // Bumped after each write so the timeline refetches without a page reload.
  const [writeCount, setWriteCount] = useState(0);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setUnreachable(true));
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

      <nav className="tabs" role="tablist">
        {(["chat", "timeline"] as Tab[]).map((name) => (
          <button
            key={name}
            role="tab"
            aria-selected={tab === name}
            className={tab === name ? "tab tab-active" : "tab"}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </nav>

      {tab === "chat" ? (
        <Chat onWrite={() => setWriteCount((n) => n + 1)} />
      ) : (
        <Timeline refreshKey={writeCount} />
      )}
    </main>
  );
}
