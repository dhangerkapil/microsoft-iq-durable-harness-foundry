import { useEffect, useRef, useState, type ComponentType } from "react";
import { api } from "./api";
import { PacksViewSchema, type PacksCard, type PacksView } from "./models";

export const DEFAULT_PACKS_PROMPT = "Prepare review packs for my three clients for next week.";
/** After the agent process stops (the crash test does so once), the step is sent again every few seconds, in the new
 * session the app opened for it, until a new process answers; the run resumes from the Foundry state store. */
export const RETRY_MS = 5000;
export const MAX_RETRIES = 36;

type Answer = { kind: "clients"; approve: boolean } | { kind: "draft"; approve: boolean; sha256: string };
type Progress = NonNullable<PacksView["progress"]>;

/** The structured answer for a card. A draft approval names the exact text it approves (its SHA-256). */
export function answerFor(card: PacksCard, approve: boolean): { call_id: string; answer: Answer } {
  return card.kind === "draft"
    ? { call_id: card.call_id, answer: { kind: "draft", approve, sha256: approve ? card.sha256 : "" } }
    : { call_id: card.call_id, answer: { kind: "clients", approve } };
}

/** Whether the panel sends the next step on its own: another pack, or the same step again after a stop. */
export function continues(view: PacksView | null, retries: number): boolean {
  return view !== null && (view.status === "working" || (view.status === "interrupted" && retries < MAX_RETRIES));
}

/** The view while a stopped step is sent again: working, without the stop's message, which isn't an error. */
export function resending(view: PacksView): PacksView {
  return { ...view, status: "working", error: null };
}

export function packsStatus(view: PacksView | null, seconds: number, retries = 0, progress: Progress | null = null): string {
  if (!view) return "Not started";
  if (view.status === "interrupted") {
    return retries >= MAX_RETRIES
      ? "The agent didn't come back. Close the panel and start another run."
      : `The agent process stopped. Sending the step again in a new session (attempt ${retries + 1}): a new process `
        + "resumes the run from the Foundry state store.";
  }
  if (view.status === "working") {
    const p = view.progress ?? progress;
    const which = p && p.total ? `pack ${Math.min(p.done + 1, p.total)} of ${p.total}${p.next ? ` (${p.next})` : ""}` : "the next pack";
    return `Working on ${which}: ${seconds} s. Each pack is checkpointed in the Foundry state store; a crash resumes `
      + "from the last one.";
  }
  if (view.status === "waiting") return "Waiting for your decision";
  if (view.status === "completed") return "Finished";
  return `The run ended ${view.status}${view.error ? `: ${view.error}` : ""}`;
}

export function PacksCardView({ card, disabled, decide }: {
  card: PacksCard; disabled: boolean; decide: (card: PacksCard, approve: boolean) => void;
}) {
  if (card.kind === "clients") {
    return <section className="approval-card packs-card" aria-label="Confirm the clients">
      <span className="eyebrow">CONFIRM THE CLIENTS</span>
      <h3>{card.question}</h3>
      <ul className="packs-clients">{card.clients.map((client) => <li key={client}>{client}</li>)}</ul>
      {card.note && <p>{card.note}</p>}
      <div className="approval-actions">
        <button className="primary" disabled={disabled} onClick={() => decide(card, true)}>Prepare the packs</button>
        <button disabled={disabled} onClick={() => decide(card, false)}>Cancel</button>
      </div>
    </section>;
  }
  return <section className="approval-card packs-card" aria-label={`Approve the draft for ${card.client}`}>
    <span className="eyebrow">FOLLOW-UP DRAFT · {card.client.toUpperCase()}</span>
    <h3>{card.question}</h3>
    <dl className="source-fields">
      <div><dt>To</dt><dd>{card.to}</dd></div>
      <div><dt>Subject</dt><dd>{card.subject}</dd></div>
    </dl>
    <pre className="source-body">{card.body}</pre>
    <p className="packs-hash">Approval applies to this exact text only (SHA-256 {card.sha256.slice(0, 12)}…).</p>
    <div className="approval-actions">
      <button className="primary" disabled={disabled} onClick={() => decide(card, true)}>Approve</button>
      <button disabled={disabled} onClick={() => decide(card, false)}>Reject</button>
    </div>
  </section>;
}

export function ReviewPacks({ token, onClose, Answer }: {
  token: () => Promise<string>; onClose: () => void; Answer: ComponentType<{ content: string }>;
}) {
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const [prompt, setPrompt] = useState(DEFAULT_PACKS_PROMPT);
  const [crashTest, setCrashTest] = useState(false);
  const [conversation, setConversation] = useState<string | null>(null);
  const [view, setView] = useState<PacksView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [since, setSince] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const [retries, setRetries] = useState(0);
  const progress = useRef<Progress | null>(null);
  const working = view !== null && (view.status === "working" || view.status === "interrupted");

  async function run(path: string, body: unknown, step = false) {
    setBusy(true);
    // A step sent again after a stop runs the pack in the new process: show it as working while it does.
    if (step && view?.status === "interrupted") setView(resending(view));
    setError(null);
    setSince(Date.now());
    setNow(Date.now());
    try {
      const next = await api(path, PacksViewSchema, await tokenRef.current(), {
        method: "POST", body: JSON.stringify(body),
      });
      if (next.conversation_id) setConversation(next.conversation_id);
      if (next.progress) progress.current = next.progress;
      if (next.status === "interrupted") setRetries((current) => current + 1); else setRetries(0);
      setView(next);
    } catch (failure) {
      if (step && view) {
        // The connection broke during a pack (a stopped process can do that): send the same step again.
        setRetries((current) => current + 1);
        setView({ ...view, status: "interrupted", error: null });
      } else {
        setError(failure instanceof Error ? failure.message : String(failure));
      }
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (busy || !conversation || !view || !continues(view, retries)) return;
    const timer = window.setTimeout(() => {
      void run(`/api/packs/${encodeURIComponent(conversation)}/next`, { run: view.run }, true);
    }, view.status === "interrupted" ? RETRY_MS : 0);
    return () => window.clearTimeout(timer);
  }, [view, busy, conversation, retries]);

  useEffect(() => {
    if (!working) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [working]);

  const decide = (card: PacksCard, approve: boolean) => {
    if (conversation && view) {
      void run(`/api/packs/${encodeURIComponent(conversation)}/reply`, { run: view.run, ...answerFor(card, approve) });
    }
  };

  return <div className="packs-overlay" role="dialog" aria-modal="true" aria-label="Review packs">
    <section className="packs-panel">
      <header>
        <div><span className="eyebrow">DURABLE WORKFLOW · ADVISOR-TEAM</span><h2>Review packs</h2></div>
        <button aria-label="Close review packs" onClick={onClose}>Close</button>
      </header>
      <p className="packs-intro">One brief per client from the agents across the four IQs, each checkpointed in the
        Foundry state store, then a follow-up draft per client that you approve one at a time. Drafts are shown, never
        sent.</p>
      {!view && <form className="packs-start" onSubmit={(event) => { event.preventDefault(); void run("/api/packs", { prompt, crash_test: crashTest }); }}>
        <textarea value={prompt} maxLength={2000} rows={2} onChange={(event) => setPrompt(event.target.value)} />
        <label><input type="checkbox" checked={crashTest} onChange={(event) => setCrashTest(event.target.checked)} />
          Crash test: stop the agent process once before the last pack; the run resumes from the state store in a new
          process</label>
        <button className="primary" disabled={busy || !prompt.trim()}>Start review packs</button>
      </form>}
      <p className="packs-status" aria-live="polite">
        {packsStatus(view, Math.max(0, Math.round((now - since) / 1000)), retries, progress.current)}</p>
      {error && <div className="error-banner" role="alert"><span>{error}</span></div>}
      {view?.status !== "interrupted" && view?.error && <div className="error-banner" role="alert"><span>{view.error}</span></div>}
      {view?.cards.map((card) => <PacksCardView key={card.call_id} card={card} disabled={busy || working} decide={decide} />)}
      {view?.text && <div className="packs-summary"><Answer content={view.text} /></div>}
      {view && !working && view.cards.length === 0 && <button onClick={() => {
        setView(null); setConversation(null); setRetries(0); progress.current = null;
      }}>Start another run</button>}
    </section>
  </div>;
}
