import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ConfigSchema, PacksViewSchema } from "./models";
import { answerFor, continues, MAX_RETRIES, PacksCardView, packsStatus, resending } from "./ReviewPacks";

const clientsCard = {
  call_id: "run-clients", kind: "clients" as const, question: "Prepare review packs for these 3 clients?",
  clients: ["Michael Scott", "Jan Levinson"], note: "Crash test armed",
};
const draftCard = {
  call_id: "run-draft-1", kind: "draft" as const, question: "Approve this follow-up draft?", client: "Jan Levinson",
  to: "jan.levinson@example.com", subject: "Quarterly review", body: "Hi Jan <img src=x onerror=alert(1)>",
  sha256: "a".repeat(64),
};
const RUN = "0123456789abcdef0123456789abcdef";

describe("review packs", () => {
  it("answers each card with a structured decision; a draft approval names its exact text", () => {
    expect(answerFor(clientsCard, true)).toEqual({ call_id: "run-clients", answer: { kind: "clients", approve: true } });
    expect(answerFor(draftCard, true)).toEqual({
      call_id: "run-draft-1", answer: { kind: "draft", approve: true, sha256: "a".repeat(64) },
    });
    expect(answerFor(draftCard, false).answer).toEqual({ kind: "draft", approve: false, sha256: "" });
  });

  it("says what the run is doing, pack by pack", () => {
    const view = PacksViewSchema.parse({
      run: RUN, status: "working", cards: [], progress: { done: 1, total: 3, next: "Jan Levinson" },
    });
    expect(packsStatus(null, 0)).toBe("Not started");
    expect(packsStatus(view, 42)).toContain("Working on pack 2 of 3 (Jan Levinson): 42 s");
    expect(packsStatus({ ...view, status: "waiting", cards: [clientsCard] }, 0)).toBe("Waiting for your decision");
    expect(packsStatus({ ...view, status: "completed" }, 0)).toBe("Finished");
    expect(packsStatus({ ...view, status: "failed", error: "boom" }, 0)).toBe("The run ended failed: boom");
  });

  it("sends a stopped step again until a new process answers, then gives up", () => {
    const working = PacksViewSchema.parse({ run: RUN, status: "working" });
    const stopped = PacksViewSchema.parse({ run: RUN, status: "interrupted", error: "stopped" });
    expect(continues(working, 0)).toBe(true);
    expect(continues(stopped, 1)).toBe(true);
    expect(continues(stopped, MAX_RETRIES)).toBe(false);
    expect(continues({ ...working, status: "waiting" }, 0)).toBe(false);
    expect(packsStatus(stopped, 0, 1)).toContain("The agent process stopped. Sending the step again in a new session (attempt 2)");
    expect(packsStatus(stopped, 0, 1)).toContain("Foundry state store");
    expect(packsStatus({ ...stopped }, 0, 0, { done: 2, total: 3, next: "Dwight Schrute" })).toContain("attempt 1");
    expect(packsStatus(stopped, 0, MAX_RETRIES)).toContain("didn't come back");
  });

  it("shows a step sent again after a stop as working, without the stop's message as an error", () => {
    const stopped = PacksViewSchema.parse({
      run: RUN, status: "interrupted", error: "The agent process stopped before this step finished.",
      progress: { done: 2, total: 3, next: "Dwight Schrute" },
    });
    const again = resending(stopped);
    expect(again.status).toBe("working");
    expect(again.error).toBeNull();
    expect(again.progress).toEqual(stopped.progress);
    expect(continues(again, 1)).toBe(true);
    expect(packsStatus(again, 5)).toContain("Working on pack 3 of 3 (Dwight Schrute): 5 s");
  });

  it("renders the cards with their facts and never as HTML", () => {
    const clients = renderToStaticMarkup(<PacksCardView card={clientsCard} disabled={false} decide={() => {}} />);
    expect(clients).toContain("<li>Michael Scott</li>");
    expect(clients).toContain("Prepare the packs");
    const draft = renderToStaticMarkup(<PacksCardView card={draftCard} disabled={true} decide={() => {}} />);
    expect(draft).toContain("FOLLOW-UP DRAFT · JAN LEVINSON");
    expect(draft).toContain("jan.levinson@example.com");
    expect(draft).toContain("SHA-256 aaaaaaaaaaaa");
    expect(draft).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(draft).toContain("disabled");
  });

  it("parses the server's view; the config no longer names a second agent", () => {
    const view = PacksViewSchema.parse({
      conversation_id: "conv_1", run: RUN, status: "waiting", cards: [clientsCard, draftCard],
      text: "## Review packs", error: null,
    });
    expect(view.cards.map((card) => card.kind)).toEqual(["clients", "draft"]);
    expect(view.progress).toBeNull();
    const config = ConfigSchema.parse({
      tenantId: "11111111-1111-4111-8111-111111111111", clientId: "33333333-3333-4333-8333-333333333333",
      scope: "api://x/access_as_user", agentName: "advisor-team",
    });
    expect(Object.keys(config)).not.toContain("packsAgentName");
  });
});
