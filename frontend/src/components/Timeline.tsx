import { useCallback, useEffect, useState } from "react";
import { getTimeline } from "../lib/api";
import type { Memory, TimelineChain } from "../lib/types";

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** One row of a chain. Superseded entries are dimmed and struck, not hidden. */
function Entry({ memory, isLast }: { memory: Memory; isLast: boolean }) {
  const expired = memory.status === "EXPIRED";
  return (
    <li className={`entry ${expired ? "entry-expired" : "entry-active"}`}>
      <span className="marker" aria-hidden="true" />
      <div className="entry-body">
        <span className="value">{memory.object}</span>
        <span className="range">
          {formatDate(memory.valid_from)}
          {expired ? ` → ${formatDate(memory.valid_until)}` : " → now"}
        </span>
        {expired && isLast && (
          // An expired tail means the replacement isn't in this page, or the
          // fact simply stopped being true with nothing taking its place.
          <span className="note">no longer true</span>
        )}
      </div>
    </li>
  );
}

function Chain({ chain }: { chain: TimelineChain }) {
  return (
    <article className="chain">
      <header className="chain-head">
        <span className="predicate">
          {chain.subject} · {chain.predicate}
        </span>
        {chain.revisions > 0 && (
          <span className="revisions">
            {chain.revisions} change{chain.revisions === 1 ? "" : "s"}
          </span>
        )}
      </header>
      <ol className="entries">
        {chain.entries.map((entry, i) => (
          <Entry
            key={entry.id}
            memory={entry}
            isLast={i === chain.entries.length - 1}
          />
        ))}
      </ol>
    </article>
  );
}

export function Timeline({ refreshKey }: { refreshKey: number }) {
  const [chains, setChains] = useState<TimelineChain[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const response = await getTimeline();
      setChains(response.chains);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // refreshKey changes whenever a message is sent, so the timeline reflects
  // writes without the user having to reload.
  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  if (error) return <p className="error">{error}</p>;
  if (chains === null) return <p className="empty">Loading…</p>;
  if (chains.length === 0)
    return (
      <p className="empty">
        No memories yet. Tell DTSG something in the chat tab.
      </p>
    );

  const changed = chains.filter((c) => c.revisions > 0).length;

  return (
    <section className="timeline">
      <p className="timeline-summary">
        {chains.length} fact{chains.length === 1 ? "" : "s"}
        {changed > 0 && `, ${changed} revised over time`}
      </p>
      {chains.map((chain) => (
        <Chain key={chain.entries[chain.entries.length - 1].id} chain={chain} />
      ))}
    </section>
  );
}
