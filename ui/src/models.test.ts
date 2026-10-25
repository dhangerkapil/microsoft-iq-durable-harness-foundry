import { describe, expect, it, vi } from "vitest";
import { ActivitySchema, ApprovalSchema, DocumentPageSchema, MessageBatcher, ReferenceSchema, SourceViewSchema, approvalTitle, citedPage, displayText, evidencePages, fileHome, groundingSummary, locationLabel, mergeRun, mergeStreamMessages, originalUrl, plainSentence, protocolMessages, toolDuration, type Run } from "./models";

describe("reference presentation", () => {
  it("keeps normal answer text and hides machine metadata, including unfinished streaming blocks", () => {
    expect(displayText("Answer\n```workiq_references\n[{\"title\":\"Plan\"}]\n```\nMore"))
      .toBe("Answer\n\nMore");
    expect(displayText("Answer\n```workiq_references\n[{")).toBe("Answer");
    expect(displayText("```python\nprint(1)\n```")).toContain("print(1)");
  });

  it("shows a sentence quoted from a draft as plain text", () => {
    expect(plainSentence("- NVDA exceeds the **25%** single-stock limit at **26.1%** (Fabric IQ): **CP-104 §3.1 (Foundry IQ)**."))
      .toBe("NVDA exceeds the 25% single-stock limit at 26.1% (Fabric IQ): CP-104 §3.1 (Foundry IQ).");
    expect(plainSentence("See [the policy](https://contoso.example/p), *now*, and `weight_pct`."))
      .toBe("See the policy, now, and weight_pct.");
    expect(plainSentence("1. A trim of   2,000 shares")).toBe("A trim of 2,000 shares");
    expect(plainSentence("We recommend Michael sell 2,000 NVDA shares this week."))
      .toBe("We recommend Michael sell 2,000 NVDA shares this week.");
  });

  it("labels reported and harness-verified positions and rejects unsafe links", () => {
    const reference = ReferenceSchema.parse({ title: "Plan", url: "https://contoso.sharepoint.com/plan", slide: 4 });
    expect(locationLabel(reference)).toBe("SharePoint · Reported slide 4");
    expect(reference.location_verified).toBe(false);
    const verified = ReferenceSchema.parse({ ...reference, slide: null, page: 2, location_verified: true });
    expect(locationLabel(verified)).toBe("SharePoint · Verified page 2");
    expect(ReferenceSchema.safeParse({ ...reference, location_verified: "yes" }).success).toBe(false);
    expect(ReferenceSchema.safeParse({ ...reference, url: "javascript:alert(1)" }).success).toBe(false);
  });

  it("names where a file lives: the user's OneDrive or a SharePoint site", () => {
    const onedrive = ReferenceSchema.parse({
      title: "Overview", url: "https://contoso-my.sharepoint.com/personal/adele_contoso_com/Documents/Overview.pdf",
    });
    expect(fileHome(onedrive)).toBe("OneDrive");
    expect(locationLabel(onedrive)).toBe("OneDrive");
    expect(locationLabel({ ...onedrive, page: 3, location_verified: true })).toBe("OneDrive · Verified page 3");
    const site = ReferenceSchema.parse({ title: "IPS v2", url: "https://contoso.sharepoint.com/sites/clients/IPS.docx" });
    expect(fileHome(site)).toBe("SharePoint");
    const other = ReferenceSchema.parse({ title: "Note", url: "https://files.contoso.com/note.pdf" });
    expect(fileHome(other)).toBeNull();
    expect(locationLabel(other)).toBe("File reference");
    expect(groundingSummary([onedrive, site, { ...site, url: "https://contoso.sharepoint.com/sites/clients/n.docx" }]))
      .toBe("3 files (1 in OneDrive, 2 in SharePoint)");
    expect(groundingSummary([onedrive, other])).toBe("2 files (1 in OneDrive)");
  });

  it("opens a OneDrive file in the OneDrive web app and leaves other links alone", () => {
    const onedrive = ReferenceSchema.parse({
      title: "Overview", url: "https://contoso-my.sharepoint.com/personal/adele_contoso_com/Documents/Client%20Files/Overview%20(Oct).pdf",
    });
    expect(originalUrl(onedrive)).toBe("https://contoso-my.sharepoint.com/personal/adele_contoso_com/_layouts/15/onedrive.aspx"
      + "?id=%2Fpersonal%2Fadele_contoso_com%2FDocuments%2FClient%20Files%2FOverview%20(Oct).pdf"
      + "&parent=%2Fpersonal%2Fadele_contoso_com%2FDocuments%2FClient%20Files");
    const site = ReferenceSchema.parse({ title: "IPS v2", url: "https://contoso.sharepoint.com/sites/clients/IPS.docx" });
    expect(originalUrl(site)).toBe(site.url);
    const office = "https://contoso-my.sharepoint.com/personal/adele_contoso_com/_layouts/15/Doc.aspx?sourcedoc=%7B1%7D";
    expect(originalUrl(onedrive, office)).toBe(office);
    expect(originalUrl({ ...onedrive, url: "https://contoso-my.sharepoint.com/personal/adele_contoso_com" }))
      .toBe("https://contoso-my.sharepoint.com/personal/adele_contoso_com");
  });

  it("labels Microsoft 365 sources by kind and defaults older references to files", () => {
    const file = ReferenceSchema.parse({ title: "Plan", url: "https://contoso.sharepoint.com/plan" });
    expect(file.kind).toBe("file");
    expect(file.entity_url).toBeNull();
    const email = ReferenceSchema.parse({
      title: "Agenda", url: "https://outlook.office365.com/owa/?ItemID=AAMk", kind: "email",
      entity_url: "/me/messages/AAMkAGI2TG93AAA=", page: 2,
    });
    expect(locationLabel(email)).toBe("Email");
    expect(locationLabel({ ...email, kind: "event" })).toBe("Meeting");
    expect(locationLabel({ ...email, kind: "chat" })).toBe("Teams message");
    expect(ReferenceSchema.safeParse({ ...email, kind: "fax" }).success).toBe(false);
  });

  it("opens a file at the cited page or slide and counts distinct cited items by kind", () => {
    const file = ReferenceSchema.parse({ title: "Overview", url: "https://contoso.sharepoint.com/o", page: 3 });
    expect(citedPage(file)).toBe(3);
    expect(citedPage({ ...file, page: null, slide: 5 })).toBe(5);
    expect(citedPage({ ...file, page: null })).toBe(1);
    expect(DocumentPageSchema.safeParse({ page: 3, page_count: 3, width: 1224, height: 1584, image: "iVBORw0KGgo=" })
      .success).toBe(true);
    expect(DocumentPageSchema.safeParse({ page: 3, page_count: 3, width: 1224, height: 1584, image: "<svg>" })
      .success).toBe(false);
    expect(DocumentPageSchema.safeParse({ page: 1, page_count: 1, width: 1, height: 1, image: "iVBORw0KGgo=",
      web_url: "javascript:alert(1)" }).success).toBe(false);
    const verified = { ...file, location_verified: true };
    expect(evidencePages([file, verified, { ...verified, url: "https://contoso.sharepoint.com/copy" },
      { ...verified, page: 2 }, { ...verified, page: 4 }])).toEqual([verified, { ...verified, page: 2 }]);
    const email = ReferenceSchema.parse({ title: "Agenda", url: "https://outlook.office365.com/owa/1", kind: "email" });
    const meeting = ReferenceSchema.parse({ title: "Review", url: "https://outlook.office365.com/cal/1", kind: "event" });
    expect(groundingSummary([email, { ...email, url: "https://outlook.office365.com/owa/2" }, meeting, file,
      { ...file, page: 2 }])).toBe("2 emails · 1 meeting · 1 file in SharePoint");
    expect(groundingSummary([])).toBeNull();
  });
});

describe("approvals and source views", () => {
  it("parses an approval request and names the draft action", () => {
    const approval = ApprovalSchema.parse({
      id: "fc_1", name: "draft_follow_up_email", server: null, response_id: "resp_1",
      arguments: { to: "michael.scott@example.com", subject: "Thursday", body: "Hi Michael" },
    });
    expect(approvalTitle(approval)).toBe("Create this draft email in your Outlook Drafts?");
    expect(approvalTitle({ ...approval, name: "other_tool" })).toBe("Allow other_tool?");
    expect(ApprovalSchema.safeParse({ ...approval, id: "" }).success).toBe(false);
    expect(ApprovalSchema.safeParse({ ...approval, arguments: { to: 1 } }).success).toBe(false);
  });

  it("accepts only secure open links for email, meeting and Teams sources", () => {
    const view = {
      kind: "chat", title: "Chat", fields: [{ label: "Chat", value: "Jordan Lee" }], body: "",
      body_truncated: false, open_label: "Open in Teams", note: "Use the web app.",
      messages: [{ id: "1", sender: "Jordan Lee", sent: "Fri Oct 16, 2026, 6:05 PM ET", text: "NVDA note", cited: true }],
      open_url: "https://teams.microsoft.com/l/message/19:abc@unq.gbl.spaces/1",
    };
    expect(SourceViewSchema.parse(view).messages[0].cited).toBe(true);
    expect(SourceViewSchema.safeParse({ ...view, open_url: "http://teams.microsoft.com/l/message/1" }).success).toBe(false);
    expect(SourceViewSchema.safeParse({ ...view, kind: "file" }).success).toBe(false);
  });
});

describe("streaming message updates", () => {
  it("preserves completed messages and references while replacing only changed text", () => {
    const source = ReferenceSchema.parse({ title: "Plan", url: "https://contoso.sharepoint.com/plan" });
    const previous = [
      { id: "old", role: "assistant" as const, content: "Completed answer", references: [source] },
      { id: "live", role: "assistant" as const, content: "Part", references: [] },
    ];
    const next = mergeStreamMessages(previous, [
      { id: "old", role: "assistant", content: "Completed answer" },
      { id: "live", role: "assistant", content: "Partial answer" },
    ]);
    expect(next[0]).toBe(previous[0]);
    expect(next[0].references).toBe(previous[0].references);
    expect(next[1].content).toBe("Partial answer");
    expect(mergeStreamMessages(next, protocolMessages(next))).toBe(next);
  });

  it("coalesces token bursts, flushes final text immediately and cancels stale updates", () => {
    vi.useFakeTimers();
    try {
      const apply = vi.fn();
      const batch = new MessageBatcher(apply, (error) => { throw error; });
      batch.push([{ id: "live", role: "assistant", content: "A" }]);
      batch.push([{ id: "live", role: "assistant", content: "AB" }]);
      vi.advanceTimersByTime(49);
      expect(apply).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1);
      expect(apply).toHaveBeenCalledExactlyOnceWith([{ id: "live", role: "assistant", content: "AB" }]);
      batch.push([{ id: "live", role: "assistant", content: "ABC" }]);
      batch.flush();
      expect(apply).toHaveBeenLastCalledWith([{ id: "live", role: "assistant", content: "ABC" }]);
      batch.push([{ id: "live", role: "assistant", content: "Stale" }]);
      batch.cancel();
      vi.runAllTimers();
      expect(apply).toHaveBeenCalledTimes(2);
    } finally { vi.useRealTimers(); }
  });
});

describe("per-prompt activity", () => {
  it("recognizes source metadata without requiring a wire kind that breaks older clients", () => {
    const call = ActivitySchema.parse({
      call_id: "source1", name: "workiq_source_search___Email", kind: "tool", skill: null,
      source: "Email", parent_call_id: "parent", status: "completed",
      started_at: "2026-10-15T10:00:00Z", duration_ms: 100, timing_source: "runtime",
    });
    expect(call.kind).toBe("search");
    expect(call.source).toBe("Email");
  });

  it("formats runtime execution time and does not trust legacy arrival-based durations", () => {
    const call = ActivitySchema.parse({
      call_id: "call1", name: "workiq___ask", kind: "tool", skill: null, status: "completed",
      started_at: "2026-10-15T10:00:00Z", duration_ms: 35_020, timing_source: "runtime",
    });
    expect(toolDuration(call)).toBe("35.0s");
    expect(toolDuration({ ...call, duration_ms: 2 })).toBe("2 ms");
    expect(toolDuration({ ...call, duration_ms: 0 })).toBe("<1 ms");
    expect(toolDuration({ ...call, timing_source: null })).toBe("Timing unavailable");
    expect(toolDuration({ ...call, timing_source: null, duration_ms: 0 })).toBe("Timing unavailable");
    expect(toolDuration({ ...call, timing_source: null, status: "running" })).toBe("Timing pending");
  });

  it("updates a streaming run in place instead of duplicating its calls or replacing another prompt", () => {
    const first: Run = {
      id: "11111111-1111-4111-8111-111111111111",
      session_id: "22222222-2222-4222-8222-222222222222",
      started_at: "2026-10-14T10:00:00Z", prompt_preview: "First", status: "running",
      response_id: null, assistant_message_ids: [], activity: [],
    };
    const second: Run = { ...first, id: "33333333-3333-4333-8333-333333333333", prompt_preview: "Second" };
    const updated = { ...first, status: "completed" as const, assistant_message_ids: ["msg_1"] };
    const runs = mergeRun([first, second], updated);
    expect(runs).toHaveLength(2);
    expect(runs.find((run) => run.id === first.id)?.status).toBe("completed");
    expect(runs.find((run) => run.id === second.id)?.prompt_preview).toBe("Second");
  });

  it("does not send preview metadata back as user-controlled agent tools", () => {
    const messages = protocolMessages([{
      id: "msg_1", role: "assistant", content: "A source",
      references: [ReferenceSchema.parse({ title: "Plan", url: "https://contoso.sharepoint.com/plan" })],
    }]);
    expect(messages).toEqual([{ id: "msg_1", role: "assistant", content: "A source" }]);
  });
});
