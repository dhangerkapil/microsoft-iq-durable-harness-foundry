import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Activity, MarkdownAnswer } from "./App";
import type { Run } from "./models";

describe("assistant Markdown", () => {
  it("renders GFM tables, headings, bold text, lists and code as actual elements", () => {
    const content = [
      "## Holdings", "", "**Portfolio**", "",
      "| Ticker | Security | Quantity |", "| --- | --- | ---: |", "| ABC | Example Corp | 10 |", "",
      "- A source", "", "```text", "read-only", "```",
    ].join("\n");
    const html = renderToStaticMarkup(<MarkdownAnswer content={content} />);
    expect(html).toContain("<h2>Holdings</h2>");
    expect(html).toContain("<strong>Portfolio</strong>");
    expect(html).toContain("<table>");
    expect(html).toContain("<th>Ticker</th>");
    expect(html).toContain("<td>Example Corp</td>");
    expect(html).toContain('class="markdown-table"');
    expect(html).toContain("<li>A source</li>");
    expect(html).toContain("<pre><code");
  });

  it("does not execute raw HTML, fetch external images or expose machine source metadata", () => {
    const content = [
      '<script>alert("unsafe")</script>',
      "![source](https://evil.example.com/image.png)",
      "```workiq_references", '[{"title":"Hidden metadata"}]', "```",
    ].join("\n\n");
    const html = renderToStaticMarkup(<MarkdownAnswer content={content} />);
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("Hidden metadata");
    expect(html).toContain("Image omitted");
  });
});

describe("run progress", () => {
  it("shows real source progress separately from model tool calls and displays measured response latency", () => {
    const run: Run = {
      id: "11111111-1111-4111-8111-111111111111",
      session_id: "22222222-2222-4222-8222-222222222222",
      started_at: "2026-10-15T10:00:00Z", prompt_preview: "Find sources", status: "running",
      response_id: "resp1", assistant_message_ids: [], stage: "searching",
      latency: { preparation_ms: 10, first_event_ms: 20, first_text_ms: 3500, response_ms: null },
      activity: [{
        call_id: "source1", name: "workiq_source_search___Email", kind: "search", source: "Email",
        parent_call_id: "parent", skill: null, status: "completed", started_at: "2026-10-15T10:00:01Z",
        duration_ms: 2400, timing_source: "runtime",
      }],
    };
    const html = renderToStaticMarkup(<Activity run={run} loadDetails={async () => ({
      input: null, output: null, input_truncated: false, output_truncated: false,
    })} />);
    expect(html).toContain("Source search: Email");
    expect(html).toContain("Searching your work sources");
    expect(html).toContain("0 skill calls / 0 tool calls");
    expect(html).toContain("First text: 3.5s");
    expect(html).not.toContain("Input / output");
    expect(html).not.toContain("Response ready");
  });

  it("shows an obvious active state before the first tool call without inventing tool activity", () => {
    const run: Run = {
      id: "11111111-1111-4111-8111-111111111111",
      session_id: "22222222-2222-4222-8222-222222222222",
      started_at: "2026-10-14T10:00:00Z", prompt_preview: "Hello", status: "running",
      response_id: null, assistant_message_ids: [], activity: [],
    };
    const html = renderToStaticMarkup(<Activity run={run} loadDetails={async () => ({
      input: null, output: null, input_truncated: false, output_truncated: false,
    })} />);
    expect(html).toContain('class="activity is-running"');
    expect(html).toContain('class="spinner"');
    expect(html).toContain("Request in progress");
    expect(html).toContain("Waiting for the hosted agent");
    expect(html).not.toContain("No tool or skill calls were reported");
  });

  it("provides an expandable input/output control for an actual completed call", () => {
    const run: Run = {
      id: "11111111-1111-4111-8111-111111111111",
      session_id: "22222222-2222-4222-8222-222222222222",
      started_at: "2026-10-14T10:00:00Z", prompt_preview: "Hello", status: "completed",
      response_id: "resp1", assistant_message_ids: [], activity: [{
        call_id: "call1", name: "workiq___ask", status: "completed", kind: "tool",
        skill: null, started_at: "2026-10-14T10:00:00Z", duration_ms: 0, timing_source: null,
      }],
    };
    const html = renderToStaticMarkup(<Activity run={run} loadDetails={async () => ({
      input: "{}", output: "Hello", input_truncated: false, output_truncated: false,
    })} />);
    expect(html).toContain('class="tool-details"');
    expect(html).toContain("Input / output");
    expect(html).toContain("workiq / ask");
    expect(html).toContain("0 skill calls / 1 tool call");
    expect(html).not.toContain("Skill loaded");
    expect(html).toContain("Timing unavailable");
    expect(html).not.toContain("0.0s");
  });

  it("shows the agents' verdict, and what the checks held back from a revised brief", () => {
    const run: Run = {
      id: "11111111-1111-4111-8111-111111111111",
      session_id: "22222222-2222-4222-8222-222222222222",
      started_at: "2026-10-23T10:00:00Z", prompt_preview: "Brief", status: "completed",
      response_id: "resp1", assistant_message_ids: [], activity: [{
        call_id: "call1", name: "search_firm_knowledge", status: "completed", kind: "tool", skill: null,
        started_at: "2026-10-23T10:00:00Z", duration_ms: 1200, timing_source: "runtime", lane: "reviewer",
        lane_title: "Compliance reviewer · Foundry IQ",
      }],
      team_verdict: {
        cleared: true, attempts: 2, findings: [],
        held_back: [{ sentence: "We recommend Michael sell 2,000 NVDA shares this week.", issue: "Reads as a trade recommendation", rule: "COM-310 §2" },
          { sentence: "- NVDA is at **26.1%**, over its **25%** limit.", issue: "States the rule without its condition", rule: "CP-104 §3.1" }],
      },
    };
    const html = renderToStaticMarkup(<Activity run={run} loadDetails={async () => ({
      input: null, output: null, input_truncated: false, output_truncated: false,
    })} />);
    expect(html).toContain("after 1 revision");
    expect(html).toContain("Held back from the first draft:");
    expect(html).toContain("<q>We recommend Michael sell 2,000 NVDA shares this week.</q>");
    expect(html).toContain("(COM-310 §2)");
    // A sentence quoted from the draft's Markdown is shown as plain text.
    expect(html).toContain("<q>NVDA is at 26.1%, over its 25% limit.</q>");
    // A brief cleared on its first draft shows no held-back list.
    const clean = renderToStaticMarkup(<Activity run={{ ...run, team_verdict: { cleared: true, attempts: 1, findings: [] } }}
      loadDetails={async () => ({ input: null, output: null, input_truncated: false, output_truncated: false })} />);
    expect(clean).not.toContain("Held back");
  });

  it("shows the Work IQ calls a harness tool made inside it, under that tool", () => {
    const run: Run = {
      id: "11111111-1111-4111-8111-111111111111",
      session_id: "22222222-2222-4222-8222-222222222222",
      started_at: "2026-10-17T10:00:00Z", prompt_preview: "Chart", status: "completed",
      response_id: "resp1", assistant_message_ids: [], activity: [{
        call_id: "call1", name: "inspect_document", status: "completed", kind: "tool", skill: null,
        started_at: "2026-10-17T10:00:00Z", duration_ms: 6358, timing_source: "runtime",
        detail: "Inside: Work IQ fetch 2.8s · fetch_blob 3.3s, as you · page matched by the harness",
      }],
    };
    const html = renderToStaticMarkup(<Activity run={run} loadDetails={async () => ({
      input: "{}", output: "{}", input_truncated: false, output_truncated: false,
    })} />);
    expect(html).toContain("inspect_document");
    expect(html).toContain("Inside: Work IQ fetch 2.8s · fetch_blob 3.3s, as you · page matched by the harness");
    expect(html).toContain("inspect_document also list the Work IQ calls they made inside");
  });

  it.each(["completed", "failed"] as const)("shows an actual %s skill load without claiming rule verification", (status) => {
    const run: Run = {
      id: "11111111-1111-4111-8111-111111111111",
      session_id: "22222222-2222-4222-8222-222222222222",
      started_at: "2026-10-14T10:00:00Z", prompt_preview: "Find a presentation", status: "completed",
      response_id: "resp1", assistant_message_ids: [], activity: [{
        call_id: "skill1", name: "load_skill", status, kind: "skill",
        skill: "document-finder", started_at: "2026-10-14T10:00:00Z", duration_ms: 2, timing_source: "runtime",
      }],
    };
    const html = renderToStaticMarkup(<Activity run={run} loadDetails={async () => ({
      input: '{"skill_name":"document-finder"}', output: "Skill instructions",
      input_truncated: false, output_truncated: false,
    })} />);
    expect(html).toContain("1 skill call / 0 tool calls");
    expect(html).toContain("Skill: document-finder");
    expect(html).toContain("not that every rule was followed");
    expect(html.includes("Skill loaded")).toBe(status === "completed");
    if (status === "failed") expect(html).toContain("Error");
    expect(html).toContain("Input / output");
    expect(html).toContain("2 ms");
  });
});
