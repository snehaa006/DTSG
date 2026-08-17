import { useState, type FormEvent } from "react";
import { sendMessage } from "../lib/api";
import type { FactOutcome } from "../lib/types";

interface Turn {
  role: "user" | "assistant";
  text: string;
  // Set on user turns once the message has been appended to the event log.
  // Showing it is how you confirm the write path is live without opening psql.
  eventId?: string;
  // What the pipeline did with each extracted fact. Rendering these is the
  // only way to see a SUPERSEDE happen without querying the database.
  outcomes?: FactOutcome[];
}

export function Chat({ onWrite }: { onWrite?: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const message = draft.trim();
    if (!message || pending) return;

    setTurns((prev) => [...prev, { role: "user", text: message }]);
    setDraft("");
    setPending(true);
    setError(null);

    try {
      const response = await sendMessage(message);
      setTurns((prev) => {
        const next = [...prev];
        // Stamp the event id onto the user turn it belongs to.
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].role === "user" && next[i].eventId === undefined) {
            next[i] = { ...next[i], eventId: response.event_id };
            break;
          }
        }
        return [
          ...next,
          {
            role: "assistant",
            text: response.reply,
            outcomes: response.outcomes,
          },
        ];
      });
      onWrite?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="chat">
      <div className="transcript">
        {turns.length === 0 && (
          <p className="empty">
            Send a message to confirm the browser can reach the API.
          </p>
        )}
        {turns.map((turn, i) => (
          <div key={i} className={`turn turn-${turn.role}`}>
            <span className="role">
              {turn.role}
              {turn.eventId && (
                <span className="event-id" title={`event ${turn.eventId}`}>
                  logged {turn.eventId.slice(0, 8)}
                </span>
              )}
            </span>
            <p>{turn.text}</p>
            {turn.outcomes && turn.outcomes.length > 0 && (
              <ul className="outcomes">
                {turn.outcomes.map((outcome, j) => (
                  <li key={j}>
                    <span
                      className={`resolution resolution-${outcome.resolution.toLowerCase()}`}
                    >
                      {outcome.resolution}
                    </span>
                    <code>
                      {outcome.fact.subject} · {outcome.fact.predicate} ·{" "}
                      {outcome.fact.object}
                    </code>
                    {outcome.expired_memory_ids.length > 0 && (
                      <span className="note">
                        expired {outcome.expired_memory_ids.length}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
        {pending && <div className="turn turn-assistant pending">…</div>}
      </div>

      {error && <p className="error">{error}</p>}

      <form onSubmit={submit} className="composer">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Tell DTSG something…"
          aria-label="Message"
        />
        <button type="submit" disabled={pending || !draft.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
