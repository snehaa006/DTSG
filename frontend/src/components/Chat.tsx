import { useState, type FormEvent } from "react";
import { sendMessage } from "../lib/api";

interface Turn {
  role: "user" | "assistant";
  text: string;
}

export function Chat() {
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
      setTurns((prev) => [...prev, { role: "assistant", text: response.reply }]);
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
            <span className="role">{turn.role}</span>
            <p>{turn.text}</p>
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
