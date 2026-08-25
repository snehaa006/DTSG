import { useEffect, useState, type FormEvent } from "react";
import { getEvents, sendMessage } from "../lib/api";
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
  // Replayed from the event log rather than sent this session. The log stores
  // the raw message only, so these carry no reply and no outcomes — marking
  // them keeps that gap visible instead of implying the facts were never found.
  restored?: boolean;
}

export function Chat({ onWrite }: { onWrite?: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(true);

  // Restore the transcript from the event log. Without this the conversation
  // lives only in component state, so a reload looks like the data was lost
  // when in fact every message is still in Postgres.
  useEffect(() => {
    let cancelled = false;
    getEvents()
      .then((response) => {
        if (cancelled) return;
        // The log comes back newest first; a transcript reads oldest first.
        const history: Turn[] = [...response.events].reverse().map((event) => ({
          role: "user",
          text: event.raw_text,
          eventId: event.id,
          restored: true,
        }));
        // Prepend rather than replace: a message sent while this was in flight
        // is newer than anything the log returned, so it belongs at the end.
        setTurns((prev) => [...history, ...prev]);
      })
      .catch(() => {
        // A failed restore is not worth blocking on — the composer still works
        // and the next send will succeed or surface its own error.
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
        {turns.length === 0 &&
          (loadingHistory ? (
            <p className="empty">Loading history…</p>
          ) : (
            <p className="empty">
              Send a message to confirm the browser can reach the API.
            </p>
          ))}
        {turns.map((turn, i) => (
          <div
            key={i}
            className={`turn turn-${turn.role}${turn.restored ? " turn-restored" : ""}`}
          >
            <span className="role">
              {turn.role}
              {turn.eventId && (
                <span className="event-id" title={`event ${turn.eventId}`}>
                  logged {turn.eventId.slice(0, 8)}
                </span>
              )}
              {turn.restored && <span className="note">from log</span>}
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
