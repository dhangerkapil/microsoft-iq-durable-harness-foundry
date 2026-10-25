import { HttpAgent, type AgentSubscriber } from "@ag-ui/client";
import { BrowserAuthError, type AccountInfo } from "@azure/msal-browser";
import { memo, useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { z } from "zod";
import { api, apiUnlessUnsupported, checkedResponse, SessionBootstrap, SignIn } from "./api";
import { ReviewPacks } from "./ReviewPacks";
import {
  ApprovalSchema,
  ChatMessageSchema,
  ConfigSchema,
  ConsentSchema,
  ConversationSchema,
  DocumentPageSchema,
  PreviewSchema,
  ReferencesEventSchema,
  RunSchema,
  SessionSchema,
  SourceViewSchema,
  ToolPayloadSchema,
  MessageBatcher,
  approvalTitle,
  citedPage,
  displayText,
  plainSentence,
  durationLabel,
  errorMessage,
  evidencePages,
  groundingSummary,
  locationLabel,
  mergeRun,
  mergeStreamMessages,
  originalUrl,
  protocolMessages,
  toolDuration,
  runProgressLabel,
  type Approval,
  type ApprovalDecision,
  type Consent,
  type Conversation,
  type DocumentPage,
  type FileReference,
  type Preview,
  type Run,
  type Session,
  type SourceView,
  type ToolPayload,
} from "./models";

type LoadToolDetails = (run: Run, callId: string, signal: AbortSignal) => Promise<ToolPayload>;

type IconName = "chat" | "plus" | "send" | "file" | "close" | "external" | "activity" | "stop" | "mail" | "calendar" | "message";
const paths: Record<IconName, string> = {
  chat: "M4 4h16v12H8l-4 4V4Z",
  plus: "M12 5v14M5 12h14",
  send: "m4 4 17 8-17 8 3-8-3-8Zm3 8h14",
  file: "M6 3h8l4 4v14H6V3Zm8 0v5h4M9 12h6M9 16h6",
  close: "m6 6 12 12M18 6 6 18",
  external: "M14 4h6v6M20 4 10 14M10 4H4v16h16v-6",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  stop: "M6 6h12v12H6Z",
  mail: "M3 6h18v12H3V6Zm0 0 9 7 9-7",
  calendar: "M4 5h16v15H4V5Zm0 5h16M8 3v4M16 3v4",
  message: "M4 5h16v11h-9l-5 4v-4H4V5Zm4 5h8M8 13h5",
};
const referenceIcons: Record<FileReference["kind"], IconName> = {
  file: "file", email: "mail", event: "calendar", chat: "message",
};
// The first two show all four Microsoft IQs: the brief, then one question that needs every IQ at once. Advisors ask in
// business terms and never name a source; the agent picks the IQs from what each question is about.
const SUGGESTIONS = [
  "Prepare me for my Thursday meeting with Michael Scott.",
  "Michael Scott needs $400,000 for his home closing. How could we fund it, and what has to happen before the money moves?",
  "What changed since my last discussion with Michael Scott?",
];
// One question per IQ, in the advisor's words; each is answered from that IQ alone. The button names the IQ.
const ONE_IQ: Array<{ iq: string; tone: string; question: string }> = [
  { iq: "Work IQ", tone: "work", question: "Bring me up to speed on Michael Scott: when we next meet, what he has asked us for, what we agreed at the last review, what the team is saying, how the new policy draft differs from the signed one, what the NVIDIA chart in his portfolio overview shows, and what is still open." },
  { iq: "Fabric IQ", tone: "fabric", question: "How has Michael Scott's NVIDIA position moved over the last month, and where do NVIDIA, his technology exposure and his cash stand against his limits today?" },
  { iq: "Foundry IQ", tone: "foundry", question: "What does our firm require when a client's single stock goes above its limit, and before we release a large wire?" },
  { iq: "Web IQ", tone: "web", question: "What's moving NVIDIA this week, and where is the stock trading now?" },
];

function Icon({ name }: { name: IconName }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]} /></svg>;
}

export const MarkdownAnswer = memo(function MarkdownAnswer({ content }: { content: string }) {
  return <Markdown skipHtml remarkPlugins={[remarkGfm]} components={{
    a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    img: ({ alt }) => <span>[Image omitted: {alt ?? "source image"}]</span>,
    table: ({ children }) => <div className="markdown-table" tabIndex={0} role="region" aria-label="Data table">
      <table>{children}</table>
    </div>,
  }}>{displayText(content)}</Markdown>;
});

function ToolDetails({ run, callId, load }: { run: Run; callId: string; load: LoadToolDetails }) {
  const [open, setOpen] = useState(false);
  const [payload, setPayload] = useState<ToolPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open || run.status === "running" || payload) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void load(run, callId, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setPayload(value); })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); };
  }, [open, run.status, run.id, run.session_id, callId, load, retry, payload]);
  return <details className="tool-details" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Input / output</summary>
    {open && <div>
      {run.status === "running" && <p>Payloads will be available when this prompt finishes.</p>}
      {loading && <p role="status">Loading actual tool input and output...</p>}
      {error && <p className="tool-error" role="alert">{error} <button onClick={() => setRetry((value) => value + 1)}>Retry</button></p>}
      {payload && <>
        <h4>Input {payload.input_truncated && <small>(truncated)</small>}</h4>
        <pre>{payload.input ?? "No input was recorded."}</pre>
        <h4>Output {payload.output_truncated && <small>(truncated)</small>}</h4>
        <pre>{payload.output ?? "No output was recorded."}</pre>
        <p className="activity-note">Loaded from Foundry, not copied into Cosmos. Credential fields and temporary authentication URLs are redacted.</p>
      </>}
    </div>}
  </details>;
}

export const Activity = memo(function Activity({ run, loadDetails }: { run: Run; loadDetails: LoadToolDetails }) {
  const labels = {
    running: "Running", awaiting_approval: "Awaiting your approval", completed: "Returned", failed: "Error",
    cancelled: "Interrupted",
  };
  const active = run.status === "running";
  // The crash test stops the agent process mid-run; its unfinished calls are interrupted, not failed.
  const stopped = run.status === "failed"
    && run.activity.some((call) => call.name === "run_durable_meeting_pack" && call.status === "cancelled");
  const skillCount = run.activity.filter((call) => call.kind === "skill").length;
  const toolCount = run.activity.filter((call) => call.kind === "tool").length;
  return <details className={`activity ${active ? "is-running" : ""}`} open={active}>
    <summary>
      {active ? <span className="spinner" aria-hidden="true" /> : <Icon name="activity" />}
      <span>{active && run.activity.length === 0
        ? "Request in progress"
        : `${skillCount} skill ${skillCount === 1 ? "call" : "calls"} / ${toolCount} tool ${toolCount === 1 ? "call" : "calls"}`}</span>
      <span className={`run-state ${stopped ? "cancelled" : run.status}`}>
        {active ? "In progress" : stopped ? "process stopped" : run.status}</span>
    </summary>
    <div className="activity-body">
      <div className="activity-prompt">{run.prompt_preview}</div>
      {stopped && <p className="location-warning">The agent process stopped before the durable meeting pack
        finished. Phases that finished are checkpointed in the Foundry state store; a new session can resume them.</p>}
      {active && run.stage && <p className="waiting-for-agent" role="status">{runProgressLabel(run)}</p>}
      {run.latency && <p className="activity-note">
        {run.latency.preparation_ms !== null && <>Preparation: {durationLabel(run.latency.preparation_ms)}. </>}
        {run.latency.first_event_ms !== null && <>First event: {durationLabel(run.latency.first_event_ms)}. </>}
        {run.latency.first_text_ms !== null && <>First text: {durationLabel(run.latency.first_text_ms)}. </>}
        {run.latency.response_ms !== null && <>Response ready: {durationLabel(run.latency.response_ms)}. </>}
      </p>}
      {run.activity.length === 0
        ? <p className={active ? "waiting-for-agent" : undefined}>{active
          ? "Waiting for the hosted agent. Tool calls will appear as they are reported."
          : "No tool or skill calls were reported for this prompt."}</p>
        : <>{teamLanes(run)}{run.activity.map((call) => <div className={`activity-entry${call.lane ? " nested" : ""}`} key={call.call_id}>
          <div className="activity-call">
            <span className={`status-dot ${call.status}`} />
            <div>{call.lane && <span className={`lane-badge lane-${call.lane}`}>{call.lane_title ?? call.lane}</span>}<code>{call.kind === "search"
              ? `Source search: ${call.source === "Email" ? "Email" : "OneDrive / SharePoint"}`
              : call.name.replace("___", " / ")}</code>
              {call.kind === "skill" && <small>Skill: {call.skill ?? "name not reported"}</small>}
              {call.detail && <small className="activity-detail">{call.detail}</small>}
            </div>
            <span>{call.kind === "skill" && call.name === "load_skill" && call.status === "completed"
              ? "Skill loaded" : labels[call.status]}</span>
            <small title={call.timing_source === "runtime"
              ? "Tool-call wall time measured inside the agent; excludes model time and delay before invocation."
              : "No runtime execution timing was recorded. Stream-delivery gaps are not execution times."}>
              {toolDuration(call)}
            </small>
          </div>
          {call.kind !== "search" && !call.lane && <ToolDetails run={run} callId={call.call_id} load={loadDetails} />}
        </div>)}</>}
      <p className="activity-note">Actual hosted-agent calls only: Work IQ, Fabric IQ, Foundry IQ, Web IQ, client memory and harness checks. Private reasoning is not shown.</p>
      {run.activity.some((call) => call.detail) && <p className="activity-note">
        The line under each call names the Microsoft IQ or harness step it reached, and as whom; harness tools such as
        inspect_document also list the Work IQ calls they made inside.
      </p>}
      {run.activity.some((call) => call.kind === "search") && <p className="activity-note">
        Source search entries show the parent retrieval&apos;s concurrent SDK requests, not additional model tool calls.
      </p>}
      {skillCount > 0 && <p className="activity-note">A successful skill load confirms its instructions were retrieved, not that every rule was followed or every source location verified.</p>}
    </div>
  </details>;
});

function consentLabel(server: string | null | undefined, index: number): string {
  const name = (server ?? "").toLowerCase().replace(/[^a-z]/g, "");
  if (name.includes("workiqchat")) return "Work IQ Chat (A2A): questions you delegate to Work IQ's own agent";
  if (name.includes("workiq")) return "Work IQ: your Microsoft 365 email, meetings, chats and files";
  if (name.includes("fabric")) return "Fabric IQ: governed portfolio data (Portfolio Analyst)";
  return server ? `Data source: ${server}` : `Data source ${index + 1}`;
}

type LoadEvidencePage = (source: FileReference) => Promise<DocumentPage | null>;

/** A verified page shown under the answer it supports, loaded when it scrolls into view. */
function EvidencePage({ source, load, open }: { source: FileReference; load: LoadEvidencePage; open: () => void }) {
  const [page, setPage] = useState<DocumentPage | null>(null);
  const [failed, setFailed] = useState(false);
  const holder = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const element = holder.current;
    if (!element) return;
    let cancelled = false;
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      load(source)
        .then((value) => { if (!cancelled) { if (value) setPage(value); else setFailed(true); } })
        .catch(() => { if (!cancelled) setFailed(true); });
    }, { rootMargin: "300px" });
    observer.observe(element);
    return () => { cancelled = true; observer.disconnect(); };
  }, [source, load]);
  if (failed) return null;
  return <button ref={holder} className="evidence-page" onClick={open}
    aria-label={`Open ${source.title}, page ${citedPage(source)}`}>
    {page
      ? <img alt={`${source.title}, page ${page.page} of ${page.page_count}`} width={page.width} height={page.height}
        src={`data:image/png;base64,${page.image}`} />
      : <span className="evidence-loading"><span className="spinner" aria-hidden="true" />Loading the cited page...</span>}
    <span className="evidence-caption"><Icon name="file" />Evidence: {source.title}, page {citedPage(source)}, verified.
      Select to open it.</span>
  </button>;
}

function PreviewFrame({ preview }: { preview: Preview }) {
  const frameName = useRef(`preview-${crypto.randomUUID()}`).current;
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => {
    if (preview.post_url) form.current?.submit();
  }, [preview]);
  const parameters = preview.post_parameters ? [...new URLSearchParams(preview.post_parameters)] : [];
  return <>
    {preview.post_url && <form ref={form} action={preview.post_url} method="post" target={frameName} hidden>
      {parameters.map(([name, value], index) => <input key={index} name={name} value={value} readOnly />)}
    </form>}
    <iframe title="Referenced document preview" name={frameName}
      src={preview.get_url ?? "about:blank"} referrerPolicy="no-referrer"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads" />
  </>;
}

function SourcePanel({ view }: { view: SourceView }) {
  return <div className="source-view">
    {view.fields.length > 0 && <dl className="source-fields">
      {view.fields.map((field) => <div key={field.label}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}
    </dl>}
    {view.body && <pre className="source-body">{view.body}</pre>}
    {view.body_truncated && <p className="activity-note">Long content shortened. Open the original for the full text.</p>}
    {view.messages.length > 0 && <ol className="chat-thread" aria-label="Teams messages">
      {view.messages.map((message) => <li key={message.id} className={message.cited ? "cited" : undefined}>
        <div className="chat-meta"><strong>{message.sender}</strong>{message.sent && <small>{message.sent}</small>}
          {message.cited && <span className="cited-badge">Cited</span>}</div>
        <p>{message.text}</p>
      </li>)}
    </ol>}
  </div>;
}

function ApprovalCard({ approval, disabled, decide }: {
  approval: Approval; disabled: boolean; decide: (decision: ApprovalDecision) => void;
}) {
  const { to, subject, body, ...rest } = approval.arguments;
  const draft = approval.name === "draft_follow_up_email";
  return <section className="approval-card" aria-label="Approval needed">
    <span className="eyebrow">APPROVAL NEEDED</span>
    <h3>{approvalTitle(approval)}</h3>
    <p>{draft
      ? "Work IQ will create this draft as you, in your Outlook Drafts folder. Nothing is sent. Your tenant's Work IQ policy still decides whether creating is allowed."
      : "The agent will run this action as you only after you approve it."}</p>
    <dl className="source-fields">
      {to && <div><dt>To</dt><dd>{to}</dd></div>}
      {subject && <div><dt>Subject</dt><dd>{subject}</dd></div>}
      {Object.entries(rest).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}
    </dl>
    {body && <pre className="source-body">{body}</pre>}
    <div className="approval-actions">
      <button className="primary" disabled={disabled} onClick={() => decide({ id: approval.id, approve: true })}>
        {draft ? "Approve: create draft" : "Approve"}</button>
      <button disabled={disabled} onClick={() => decide({ id: approval.id, approve: false })}>Reject</button>
    </div>
  </section>;
}

export function App() {
  const [signIn, setSignIn] = useState<SignIn | null>(null);
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [authRetry, setAuthRetry] = useState(false);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [consent, setConsent] = useState<Consent[]>([]);
  const [openedConsents, setOpenedConsents] = useState<Set<string>>(new Set());
  const [reference, setReference] = useState<FileReference | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [documentPage, setDocumentPage] = useState<DocumentPage | null>(null);
  const [sourceView, setSourceView] = useState<SourceView | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [packsOpen, setPacksOpen] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const accountRef = useRef<string | null>(null);
  const authEpoch = useRef(0);
  const selectedRef = useRef<string | null>(null);
  const cache = useRef(new Map<string, Conversation>());
  const bootstrap = useRef(new SessionBootstrap());
  const consents = useRef(new Map<string, Consent[]>());
  const approvalRequests = useRef(new Map<string, Approval[]>());
  const agents = useRef(new Map<string, HttpAgent>());
  const pending = useRef(new Map<string, string>());
  const selection = useRef(0);
  const previewAbort = useRef<AbortController | null>(null);
  const messagesContainer = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  useEffect(() => {
    let alive = true;
    async function initialize() {
      try {
        const response = await checkedResponse(await fetch("/api/config", { cache: "no-store" }));
        const login = new SignIn(ConfigSchema.parse(await response.json()));
        const user = await login.initialize();
        if (!alive) return;
        setSignIn(login);
        setCatalogBusy(Boolean(user));
        setAccount(user);
        accountRef.current = user ? `${user.homeAccountId}:${authEpoch.current}` : null;
      } catch (failure) {
        if (alive) setError(errorMessage(failure));
      }
    }
    void initialize();
    return () => { alive = false; };
  }, []);

  async function token(): Promise<string> {
    if (!signIn || !account) throw new Error("Sign in before using the chat.");
    return signIn.token(account);
  }

  async function refreshSessions() {
    const identity = accountRef.current;
    const value = await api("/api/sessions", z.array(SessionSchema), await token());
    if (identity === accountRef.current) {
      setSessions(value);
      for (const session of value) {
        updateConversation(session.id, (chat) => ({ ...chat, session }));
      }
    }
    return value;
  }

  useEffect(() => {
    if (!account || !signIn) return;
    let alive = true;
    const identity = accountRef.current;
    if (!identity) return;
    setCatalogBusy(true);
    void bootstrap.current.load(identity, () => signIn.token(account), () => identity === accountRef.current)
      .then(async (value) => {
        if (!alive || identity !== accountRef.current || !value) return;
        setSessions(value.sessions);
        if (value.created) {
          cache.current.set(value.created.id, { session: value.created, messages: [], runs: [] });
          await chooseSession(value.created);
        }
      })
      .catch((failure: unknown) => {
        if (alive && identity === accountRef.current) setError(errorMessage(failure));
      })
      .finally(() => {
        if (alive && identity === accountRef.current) setCatalogBusy(false);
      });
    return () => { alive = false; };
  }, [account, signIn]);

  useEffect(() => {
    if (!stickToBottom.current) return;
    const frame = requestAnimationFrame(() => {
      const container = messagesContainer.current;
      if (container && stickToBottom.current) container.scrollTop = container.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [conversation?.messages, conversation?.runs]);

  function updateConversation(identifier: string, update: (value: Conversation) => Conversation) {
    const current = cache.current.get(identifier);
    if (!current) return;
    const next = update(current);
    if (next === current) return;
    cache.current.set(identifier, next);
    if (selectedRef.current === identifier) setConversation(next);
  }

  async function chooseSession(session: Session) {
    const sequence = ++selection.current;
    const identity = accountRef.current;
    selectedRef.current = session.id;
    stickToBottom.current = true;
    setSelected(session.id);
    setConsent(consents.current.get(session.id) ?? []);
    setApprovals(approvalRequests.current.get(session.id) ?? []);
    setError(null);
    previewAbort.current?.abort();
    setReference(null);
    setPreview(null);
    setSourceView(null);
    setPreviewError(null);
    setDraft("");
    const cached = cache.current.get(session.id);
    setConversation(cached ?? null);
    if (agents.current.has(session.id) && cached) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const value = await api(`/api/sessions/${session.id}`, ConversationSchema, await token());
      if (sequence !== selection.current || identity !== accountRef.current) return;
      cache.current.set(session.id, value);
      setConversation(value);
    } catch (failure) {
      if (sequence === selection.current && identity === accountRef.current) setError(errorMessage(failure));
    } finally {
      if (sequence === selection.current) setLoading(false);
    }
  }

  async function newSession() {
    if (catalogBusy || creating) return;
    setError(null);
    setCreating(true);
    // Leave the current session at once: creating a session can take seconds, and a message typed meanwhile must not
    // go to the session on screen before it.
    ++selection.current;
    selectedRef.current = null;
    setSelected(null);
    setConversation(null);
    setConsent([]);
    setApprovals([]);
    previewAbort.current?.abort();
    setReference(null);
    setPreview(null);
    setSourceView(null);
    setPreviewError(null);
    setDraft("");
    try {
      const identity = accountRef.current;
      const session = await api("/api/sessions", SessionSchema, await token(), { method: "POST", body: "{}" });
      if (identity !== accountRef.current) return;
      cache.current.set(session.id, { session, messages: [], runs: [] });
      setSessions((values) => [session, ...values]);
      await chooseSession(session);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setCreating(false);
    }
  }

  async function renameSession(session: Session) {
    const title = window.prompt("Session title", session.title)?.trim();
    if (!title || title === session.title) return;
    try {
      const changed = await api(`/api/sessions/${session.id}`, SessionSchema, await token(), {
        method: "PATCH", body: JSON.stringify({ title }),
      });
      setSessions((values) => values.map((value) => value.id === changed.id ? changed : value));
      updateConversation(session.id, (value) => ({ ...value, session: changed }));
    } catch (failure) { setError(errorMessage(failure)); }
  }

  async function deleteSession(session: Session) {
    if (!window.confirm(`Delete "${session.title}" and its Foundry conversation?`)) return;
    try {
      await api(`/api/sessions/${session.id}`, z.object({ deleted: z.literal(true) }), await token(), {
        method: "DELETE",
      });
      cache.current.delete(session.id);
      consents.current.delete(session.id);
      approvalRequests.current.delete(session.id);
      setSessions((values) => values.filter((value) => value.id !== session.id));
      if (selectedRef.current === session.id) {
        ++selection.current;
        selectedRef.current = null;
        setSelected(null);
        setConversation(null);
        setReference(null);
        setPreview(null);
        setSourceView(null);
        setConsent([]);
        setApprovals([]);
      }
    } catch (failure) { setError(errorMessage(failure)); }
  }

  async function send(text: string, approval?: ApprovalDecision) {
    const identifier = selectedRef.current;
    const identity = accountRef.current;
    const current = identifier ? cache.current.get(identifier) : null;
    if (
      !identifier || !current || !identity || (!approval && !text.trim())
      || agents.current.has(identifier) || pending.current.has(identifier)
    ) return;
    pending.current.set(identifier, identity);
    const submitted = performance.now();
    let receivedEvent = false;
    let receivedText = false;
    setRunning(new Set([...agents.current.keys(), ...pending.current.keys()]));
    setError(null);
    let agent: HttpAgent | undefined;
    let messages: MessageBatcher | undefined;
    try {
      const accessToken = await token();
      if (identity !== accountRef.current) return;
      // An approval decision continues the last response; it adds no user message.
      const initialMessages = approval ? current.messages : [...current.messages, ChatMessageSchema.parse({
        id: crypto.randomUUID(), role: "user", content: text.trim(), references: [],
      })];
      stickToBottom.current = true;
      updateConversation(identifier, (value) => ({ ...value, messages: initialMessages }));
      agent = new HttpAgent({
        url: "/api/chat", threadId: identifier, headers: { Authorization: `Bearer ${accessToken}` },
        initialMessages: protocolMessages(initialMessages), debug: false,
        fetch: async (url, options) => checkedResponse(await fetch(url, options)),
      });
      messages = new MessageBatcher((incoming) => {
        if (identity !== accountRef.current) return;
        updateConversation(identifier, (value) => {
          const next = mergeStreamMessages(value.messages, incoming);
          return next === value.messages ? value : { ...value, messages: next };
        });
      }, (failure) => {
        if (identity === accountRef.current) {
          setError(errorMessage(failure));
          agent?.abortRun();
        }
      });
      agents.current.set(identifier, agent);
      pending.current.delete(identifier);
      setRunning(new Set(agents.current.keys()));
      consents.current.delete(identifier);
      approvalRequests.current.delete(identifier);
      if (selectedRef.current === identifier) {
        if (!approval) setDraft("");
        setConsent([]);
        setApprovals([]);
      }
      const messageBuffer = messages;
      const subscriber: AgentSubscriber = {
        onRunStartedEvent() {
          if (identity !== accountRef.current) return;
          if (!receivedEvent) {
            receivedEvent = true;
            performance.measure("workiq:browser:first-event", { start: submitted });
          }
        },
        onTextMessageContentEvent() {
          if (identity !== accountRef.current) return;
          if (!receivedText) {
            receivedText = true;
            performance.measure("workiq:browser:first-text-event", { start: submitted });
          }
        },
        onMessagesChanged({ messages }) {
          if (identity !== accountRef.current) return;
          messageBuffer.push(messages);
        },
        onCustomEvent({ event }) {
          if (identity !== accountRef.current) return;
          if (event.name === "workiq.activity") {
            const run = RunSchema.parse(event.value);
            if (run.session_id !== identifier) throw new Error("Mismatched session activity.");
            updateConversation(identifier, (value) => ({ ...value, runs: mergeRun(value.runs, run) }));
          } else if (event.name === "workiq.references") {
            messageBuffer.flush();
            const value = ReferencesEventSchema.parse(event.value);
            updateConversation(identifier, (chat) => ({
              ...chat,
              messages: chat.messages.map((message) => message.id === value.message_id
                ? { ...message, references: value.references } : message),
            }));
          } else if (event.name === "workiq.consent") {
            const value = ConsentSchema.parse(event.value);
            // Each data source sends its own consent request; keep one (the newest) per source.
            const pendingConsents = [
              ...(consents.current.get(identifier) ?? []).filter((item) => (item.server ?? item.url) !== (value.server ?? value.url)),
              value,
            ];
            consents.current.set(identifier, pendingConsents);
            if (selectedRef.current === identifier) setConsent(pendingConsents);
          } else if (event.name === "workiq.approval") {
            const value = ApprovalSchema.parse(event.value);
            const pendingApprovals = [
              ...(approvalRequests.current.get(identifier) ?? []).filter((item) => item.id !== value.id), value,
            ];
            approvalRequests.current.set(identifier, pendingApprovals);
            if (selectedRef.current === identifier) setApprovals(pendingApprovals);
          }
        },
        onRunErrorEvent({ event }) {
          if (selectedRef.current === identifier && identity === accountRef.current) setError(event.message);
        },
      };
      await agent.runAgent({
        runId: crypto.randomUUID(), ...(approval ? { forwardedProps: { approval } } : {}),
      }, subscriber);
    } catch (failure) {
      if (selectedRef.current === identifier && identity === accountRef.current) setError(errorMessage(failure));
    } finally {
      performance.measure("workiq:browser:run-ended", { start: submitted });
      try { messages?.flush(); }
      catch (failure) { if (identity === accountRef.current) setError(errorMessage(failure)); }
      messages?.cancel();
      if (agents.current.get(identifier) === agent) agents.current.delete(identifier);
      if (pending.current.get(identifier) === identity) pending.current.delete(identifier);
      setRunning(new Set([...agents.current.keys(), ...pending.current.keys()]));
      if (identity === accountRef.current) {
        void refreshSessions().catch((failure: unknown) => setError(errorMessage(failure)));
      }
    }
  }

  async function openPreview(source: FileReference) {
    if (!selected) return;
    const identifier = selected;
    previewAbort.current?.abort();
    const controller = new AbortController();
    previewAbort.current = controller;
    setReference(source);
    setPreview(null);
    setDocumentPage(null);
    setSourceView(null);
    setPreviewError(null);
    setPreviewLoading(false);
    if (source.kind !== "file" && !source.entity_url) {
      setPreviewError("This source has no Microsoft 365 item ID to show here. Open the original instead.");
      return;
    }
    setPreviewLoading(true);
    try {
      const request = {
        method: "POST", body: JSON.stringify({ reference: source }), signal: controller.signal,
      };
      if (source.kind === "file") {
        // The server renders the cited page (PDFs as they are, Office files converted by Graph), so it looks the
        // same in every browser; Graph's embeddable viewer stays blank where third-party storage is blocked, as in
        // InPrivate windows. Only file types that can't become a PDF fall back to that viewer.
        const page = await apiUnlessUnsupported(`/api/sessions/${identifier}/document`, DocumentPageSchema,
          await token(), { ...request, body: JSON.stringify({ reference: source, page: citedPage(source) }) });
        if (controller.signal.aborted || selectedRef.current !== identifier) return;
        if (page) {
          setDocumentPage(page);
        } else {
          const value = await api(`/api/sessions/${identifier}/preview`, PreviewSchema, await token(), request);
          if (!controller.signal.aborted && selectedRef.current === identifier) setPreview(value);
        }
      } else {
        const value = await api(`/api/sessions/${identifier}/source`, SourceViewSchema, await token(), request);
        if (!controller.signal.aborted && selectedRef.current === identifier) setSourceView(value);
      }
    } catch (failure) {
      if (!controller.signal.aborted && selectedRef.current === identifier) setPreviewError(errorMessage(failure));
    } finally {
      if (!controller.signal.aborted) setPreviewLoading(false);
    }
  }

  async function showPage(page: number) {
    if (!selected || !reference) return;
    const identifier = selected;
    const source = reference;
    previewAbort.current?.abort();
    const controller = new AbortController();
    previewAbort.current = controller;
    setPreviewError(null);
    setPreviewLoading(true);
    try {
      const value = await api(`/api/sessions/${identifier}/document`, DocumentPageSchema, await token(), {
        method: "POST", body: JSON.stringify({ reference: source, page }), signal: controller.signal,
      });
      if (!controller.signal.aborted && selectedRef.current === identifier) setDocumentPage(value);
    } catch (failure) {
      if (!controller.signal.aborted && selectedRef.current === identifier) setPreviewError(errorMessage(failure));
    } finally {
      if (!controller.signal.aborted) setPreviewLoading(false);
    }
  }

  async function login(retry = false) {
    if (!signIn || authBusy) return;
    setAuthBusy(true);
    setAuthRetry(false);
    setError(null);
    try {
      const user = await signIn.login(retry);
      accountRef.current = `${user.homeAccountId}:${++authEpoch.current}`;
      setCatalogBusy(true);
      setAccount(user);
    } catch (failure) {
      setAuthRetry(failure instanceof BrowserAuthError && failure.errorCode === "interaction_in_progress");
      setError(errorMessage(failure));
    }
    finally { setAuthBusy(false); }
  }

  async function logout() {
    if (!signIn || !account || authBusy) return;
    setAuthBusy(true);
    setAuthRetry(false);
    accountRef.current = null;
    ++authEpoch.current;
    ++selection.current;
    selectedRef.current = null;
    for (const agent of agents.current.values()) agent.abortRun();
    agents.current.clear();
    pending.current.clear();
    cache.current.clear();
    bootstrap.current.clear();
    consents.current.clear();
    approvalRequests.current.clear();
    previewAbort.current?.abort();
    setSessions([]);
    setSelected(null);
    setConversation(null);
    setCatalogBusy(false);
    setRunning(new Set());
    setConsent([]);
    setApprovals([]);
    setReference(null);
    setPreview(null);
    setDocumentPage(null);
    setSourceView(null);
    const user = account;
    setAccount(null);
    try { await signIn.logout(user); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setAuthBusy(false); }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void send(draft);
  }

  const isRunning = selected !== null && running.has(selected);
  const activeRun = conversation?.runs.filter((run) => run.status === "running").at(-1);
  const loadToolDetails = useCallback(async (run: Run, callId: string, signal: AbortSignal) => {
    if (!signIn || !account) throw new Error("Sign in to view tool details.");
    const identity = accountRef.current;
    const accessToken = await signIn.token(account);
    if (signal.aborted || identity !== accountRef.current) throw new DOMException("Cancelled", "AbortError");
    const value = await api(
      `/api/sessions/${run.session_id}/runs/${run.id}/calls/${encodeURIComponent(callId)}`,
      ToolPayloadSchema, accessToken, { signal },
    );
    if (identity !== accountRef.current) throw new DOMException("Cancelled", "AbortError");
    return value;
  }, [signIn, account]);
  const evidenceCache = useRef(new Map<string, Promise<DocumentPage | null>>());
  const loadEvidencePage = useCallback((source: FileReference): Promise<DocumentPage | null> => {
    if (!signIn || !account || !selected) return Promise.resolve(null);
    const identifier = selected;
    const key = `${account.homeAccountId}|${identifier}|${source.url}|${citedPage(source)}`;
    const cached = evidenceCache.current.get(key);
    if (cached) return cached;
    const pending = (async () => apiUnlessUnsupported(`/api/sessions/${identifier}/document`, DocumentPageSchema,
      await signIn.token(account), {
        method: "POST", body: JSON.stringify({ reference: source, page: citedPage(source) }),
      }))();
    pending.catch(() => evidenceCache.current.delete(key));
    evidenceCache.current.set(key, pending);
    return pending;
  }, [signIn, account, selected]);
  const messages = conversation?.messages.filter((message) => message.content.trim()) ?? [];
  const lastUserMessage = messages.filter((message) => message.role === "user").at(-1)?.content ?? null;
  const unattached = conversation?.runs.filter((run) =>
    !run.assistant_message_ids.some((identifier) => messages.some((message) => message.id === identifier))) ?? [];

  return <div className={`app ${reference ? "has-preview" : ""}`}>
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark"><Icon name="activity" /></span>
        <div>Investment Advisor<span>Contoso Wealth · a multi-agent solution with Microsoft IQ</span></div>
      </div>
      <button className="new-session" onClick={() => void newSession()} disabled={!account || catalogBusy || creating || authBusy}>
        <Icon name="plus" />New session
      </button>
      <button className="new-session packs-open" onClick={() => setPacksOpen(true)}
        disabled={!account || authBusy}>
        <Icon name="activity" />Review packs
      </button>
      <div className="sidebar-label">YOUR SESSIONS <span>{sessions.length}</span></div>
      <nav aria-label="Chat sessions" className="session-list">
        {sessions.map((session) => <div key={session.id} className={`session ${selected === session.id ? "selected" : ""}`}>
          <button className="session-select" onClick={() => void chooseSession(session)}>
            <Icon name="chat" /><span>{session.title}</span>
            {(running.has(session.id) || session.running) && <span className="live-dot" />}
          </button>
          <div className="session-actions">
            <button aria-label={`Rename ${session.title}`} onClick={() => void renameSession(session)}>Rename</button>
            <button aria-label={`Delete ${session.title}`} disabled={running.has(session.id) || session.running}
              onClick={() => void deleteSession(session)}>Delete</button>
          </div>
        </div>)}
        {account && sessions.length === 0 && <p className="no-sessions">Start a session to begin researching. Your sessions stay private to your account.</p>}
      </nav>
      <div className="account">
        <span className="avatar">{account?.name?.slice(0, 1) ?? "W"}</span>
        <div><strong>{account?.name ?? "Not signed in"}</strong>
          <button disabled={!signIn || authBusy} onClick={() => void (account ? logout() : login())}>
            {account ? "Sign out" : authBusy ? "Signing in..." : "Sign in with Microsoft"}
          </button>
        </div>
      </div>
    </aside>

    <main className="chat">
      <header className="chat-header">
        <div><span className="eyebrow">FOUNDRY HOSTED AGENT</span>
          <h1>{conversation?.session.title ?? "Investment Advisor"}</h1>
        </div>
        <span className="connection-badge"><span />Work IQ · Foundry hosted agent</span>
      </header>
      {error && <div className="error-banner" role="alert">
        <span>{authRetry
          ? "A previous sign-in attempt is still pending. Close any old sign-in window, then retry."
          : error}</span>
        {authRetry && <button disabled={authBusy} onClick={() => void login(true)}>Retry sign-in</button>}
        <button aria-label="Dismiss error" onClick={() => setError(null)}><Icon name="close" /></button>
      </div>}
      {consent.length > 0 && <div className="consent-banner" role="alert">
        <strong>Action needed: authorize {consent.length === 1 ? "one data source" : `${consent.length} data sources`} (one time per user)</strong>
        <p>The agent can't run until you consent. Click each <b>Authorize</b> link once, sign in, and wait for the
          success page (each link works only once). Then select <b>Retry</b>. Links expire after 10 minutes; Retry also gets fresh links.</p>
        <ol className="consent-list">
          {consent.map((item, index) => <li key={item.server ?? item.url}>
            <span>{consentLabel(item.server, index)}</span>
            {openedConsents.has(item.url)
              ? <span className="consent-opened">Opened: finish sign-in in the new tab</span>
              : <a href={item.url} target="_blank" rel="noopener noreferrer"
                onClick={() => setOpenedConsents((values) => new Set(values).add(item.url))}>Authorize <Icon name="external" /></a>}
          </li>)}
        </ol>
        <button className="consent-retry" disabled={isRunning || !lastUserMessage}
          onClick={() => lastUserMessage && void send(lastUserMessage)}>Retry: send my last message again</button>
      </div>}
      <div className="messages" ref={messagesContainer} aria-live="polite" aria-busy={loading || isRunning}
        onScroll={(event) => {
          const container = event.currentTarget;
          stickToBottom.current = container.scrollHeight - container.scrollTop - container.clientHeight < 80;
        }}>
        {catalogBusy && <div className="loading" role="status"><span className="spinner" aria-hidden="true" /> Preparing your workspace...</div>}
        {creating && <div className="loading" role="status"><span className="spinner" aria-hidden="true" /> Creating a session...</div>}
        {loading && <div className="loading">Loading conversation...</div>}
        {!loading && messages.length === 0 && <div className="welcome">
          <div className="welcome-symbol"><Icon name="file" /></div>
          <span className="eyebrow">CLIENT CONTEXT, WITH EVIDENCE</span>
          <h2>Prepare for every client.<br />Know what changed.</h2>
          <p>AI agents that work together across Microsoft IQ: your Microsoft 365 work from Work IQ (mail, meetings, chats and files, as you), portfolio data from Fabric IQ, the firm's policies and guidance from Foundry IQ, and public news from Web IQ, with durable memory and evidence checks from the Foundry harness. Every tool call is shown.</p>
          {!account
            ? <button className="primary" disabled={!signIn || authBusy} onClick={() => void login()}>Sign in with Microsoft</button>
            : !selected
              ? <button className="primary" disabled={catalogBusy || creating} onClick={() => void newSession()}>Create a session</button>
              : <><div className="suggestions">
                {SUGGESTIONS.map((text) =>
                  <button key={text} onClick={() => void send(text)}>{text}<Icon name="send" /></button>)}
              </div>
              <div className="one-iq" role="group" aria-label="Ask one Microsoft IQ at a time">
                <span>One IQ at a time</span>
                {ONE_IQ.map(({ iq, tone, question }) =>
                  <button key={iq} className={`iq-${tone}`} title={question} onClick={() => void send(question)}>{iq}</button>)}
              </div></>}
          <small>Synthetic demo data. Sources and access depend on your permissions. Not investment advice.</small>
        </div>}
        {messages.map((message) => <article key={message.id} className={`message ${message.role}`}>
          <div className="message-author">{message.role === "user" ? "You" : "Advisor assistant"}</div>
          <div className="message-content">
            {message.role === "user" ? <p>{message.content}</p> : <MarkdownAnswer content={message.content} />}
          </div>
          {message.references.length > 0 && <p className="grounding-summary">
            Grounded in {groundingSummary(message.references)}, read as you through Work IQ. Select one to open it.</p>}
          {evidencePages(message.references).map((source) => <EvidencePage key={`${source.url}-${citedPage(source)}`}
            source={source} load={loadEvidencePage} open={() => void openPreview(source)} />)}
          {message.references.length > 0 && <div className="references">
            {message.references.map((source, index) => <button className="reference" key={`${source.url}-${index}`}
              onClick={() => void openPreview(source)}>
              <Icon name={referenceIcons[source.kind]} /><span><strong>{source.title}</strong>
                <small>{locationLabel(source)}{source.kind === "file" && (source.page || source.slide) && !source.location_verified ? " - not verified" : ""}</small>
              </span><Icon name="external" />
            </button>)}
          </div>}
          {conversation?.runs.filter((run) => run.assistant_message_ids.at(-1) === message.id)
            .map((run) => <Activity key={run.id} run={run} loadDetails={loadToolDetails} />)}
        </article>)}
        {unattached.map((run) => <Activity key={run.id} run={run} loadDetails={loadToolDetails} />)}
        {account && selected && approvals.map((item) => <ApprovalCard key={item.id} approval={item} disabled={isRunning}
          decide={(decision) => void send("", decision)} />)}
      </div>
      <footer className="composer-area">
        {isRunning && <div className="request-progress" role="status">
          <span className="spinner" aria-hidden="true" />
          <div><strong>{activeRun ? runProgressLabel(activeRun) : "Submitting your request"}</strong>
            <span>You can draft your next message, switch sessions, or stop streaming below.</span>
          </div>
        </div>}
        <form className="composer" onSubmit={submit}>
          <textarea aria-label="Message" placeholder={isRunning ? "Draft your next message..." : selected
            ? "Ask about a client..." : "Create or select a session to start"}
            value={draft} disabled={!selected || !account} maxLength={20000} rows={2}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (!isRunning) void send(draft);
              }
            }} />
          {isRunning
            ? <button type="button" aria-label="Stop streaming" onClick={() => selected && agents.current.get(selected)?.abortRun()}><Icon name="stop" /></button>
            : <button type="submit" aria-label="Send message" disabled={!selected || !draft.trim()}><Icon name="send" /></button>}
        </form>
        <div className="composer-note">Research is read-only. Drafting an email asks for your approval first and never sends. Verify important facts against the original source.</div>
      </footer>
    </main>

    {reference && <aside className="preview-pane" aria-label="Source preview">
      <header><div><span className="eyebrow">{reference.kind === "email" ? "EMAIL" : reference.kind === "event"
        ? "MEETING" : reference.kind === "chat" ? "TEAMS MESSAGE" : "SOURCE PREVIEW"}</span>
        <h2>{sourceView?.title ?? reference.title}</h2></div>
        <button aria-label="Close preview" onClick={() => { previewAbort.current?.abort(); setReference(null); setPreview(null); setDocumentPage(null); setSourceView(null); }}><Icon name="close" /></button>
      </header>
      <div className="preview-toolbar"><span>{locationLabel(reference)}</span>
        {reference.kind === "file"
          ? <a href={originalUrl(reference, documentPage?.web_url)} target="_blank" rel="noopener noreferrer">Open original <Icon name="external" /></a>
          : <a href={sourceView?.open_url ?? reference.url} target="_blank" rel="noopener noreferrer">
            {sourceView?.open_label ?? "Open original"} <Icon name="external" /></a>}
      </div>
      {reference.kind === "file" && (reference.page || reference.slide) && !reference.location_verified && <div className="location-warning">Location reported by the agent, not verified. The file viewer may not support jumping to this page or slide.</div>}
      {reference.kind === "file" && reference.location_verified && <div className="location-verified">Page verified by the agent: the cited text was matched on this page of the PDF. The viewer opens at that page.</div>}
      {sourceView?.note && <div className="location-warning">{sourceView.note}</div>}
      {preview?.warning && <div className="location-warning">{preview.warning}</div>}
      {previewLoading && !documentPage && <div className="preview-placeholder" role="status">
        <span className="spinner" aria-hidden="true" /><p>{reference.kind === "file"
          ? "Requesting a preview with your permissions..." : "Opening the source with your permissions..."}</p>
      </div>}
      {previewError && <div className="preview-placeholder"><Icon name={referenceIcons[reference.kind]} /><p>{previewError}</p>
        <a href={reference.url} target="_blank" rel="noopener noreferrer">Open the original</a></div>}
      {preview && <PreviewFrame key={`${reference.url}-${reference.page}-${reference.slide}`} preview={preview} />}
      {documentPage && <>
        <div className="page-nav">
          <button disabled={previewLoading || documentPage.page <= 1}
            onClick={() => void showPage(documentPage.page - 1)}>Previous page</button>
          <span role="status">{previewLoading ? "Loading..." : `Page ${documentPage.page} of ${documentPage.page_count}`}
            {reference.location_verified && documentPage.page === citedPage(reference) && <b> · verified</b>}</span>
          <button disabled={previewLoading || documentPage.page >= documentPage.page_count}
            onClick={() => void showPage(documentPage.page + 1)}>Next page</button>
        </div>
        <div className="page-view">
          <img alt={`${reference.title}, page ${documentPage.page} of ${documentPage.page_count}`}
            width={documentPage.width} height={documentPage.height}
            src={`data:image/png;base64,${documentPage.image}`} />
        </div>
      </>}
      {sourceView && <SourcePanel view={sourceView} />}
    </aside>}
    {packsOpen && <ReviewPacks token={token} onClose={() => setPacksOpen(false)} Answer={MarkdownAnswer} />}
  </div>;
}

// The agents' lanes: one bar per agent across the turn, so parallel work is visible, plus the review verdict.
function teamLanes(run: Run) {
  const nested = run.activity.filter((call) => call.lane);
  if (nested.length === 0 && !run.team_verdict) return null;
  const spans = new Map<string, { title: string; start: number; end: number; calls: number }>();
  for (const call of nested) {
    const start = Date.parse(call.started_at);
    const end = start + (call.duration_ms ?? 0);
    const lane = spans.get(call.lane!) ?? { title: call.lane_title ?? call.lane!, start, end, calls: 0 };
    lane.start = Math.min(lane.start, start); lane.end = Math.max(lane.end, end); lane.calls += 1;
    spans.set(call.lane!, lane);
  }
  const all = [...spans.values()];
  const t0 = Math.min(...all.map((s) => s.start)); const t1 = Math.max(...all.map((s) => s.end));
  const width = Math.max(t1 - t0, 1);
  const verdict = run.team_verdict;
  return <div className="team-lanes" aria-label="The agents' lanes">
    {all.length > 0 && <p className="activity-note">{all.length} {all.length === 1 ? "agent" : "agents"}; bars show when each worked (overlap = in parallel).</p>}
    {[...spans.entries()].map(([lane, span]) => <div className="lane-row" key={lane}>
      <span className={`lane-badge lane-${lane}`}>{span.title}</span>
      <span className="lane-track"><span className={`lane-bar lane-${lane}`} style={{
        left: `${((span.start - t0) / width) * 100}%`, width: `${Math.max(((span.end - span.start) / width) * 100, 1.5)}%` }} /></span>
      <small>{span.calls} {span.calls === 1 ? "call" : "calls"} · {Math.round((span.end - span.start) / 1000)} s</small>
    </div>)}
    {verdict && <div className={`verdict ${verdict.cleared ? "cleared" : "not-cleared"}`} role="status">
      <strong>{verdict.cleared ? "Cleared" : "Not cleared"}</strong> by the number check and the compliance reviewer
      {verdict.attempts && verdict.attempts > 1 ? ` after ${verdict.attempts - 1} revision` : ""}.
      {(verdict.findings ?? []).length > 0 && <ul>{(verdict.findings ?? []).map((f, i) =>
        <li key={i}>{f.issue}{f.rule ? ` (${f.rule})` : ""}{f.sentence ? `: "${plainSentence(f.sentence)}"` : ""}</li>)}</ul>}
      {verdict.cleared && (verdict.held_back ?? []).length > 0 && <div className="held-back">
        <span>Held back from the first draft:</span>
        <ul>{(verdict.held_back ?? []).map((f, i) =>
          <li key={i}>{f.sentence ? <q>{plainSentence(f.sentence)}</q> : null} {f.issue}{f.rule ? ` (${f.rule})` : ""}</li>)}</ul>
      </div>}
    </div>}
  </div>;
}
