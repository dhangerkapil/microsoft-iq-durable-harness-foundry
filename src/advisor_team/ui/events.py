from __future__ import annotations

import json
import re
from urllib.parse import urlsplit
from uuid import uuid4

from ag_ui.core import (
    BaseEvent,
    CustomEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from fastapi import HTTPException

from ..activity import now_iso, unwrap_team_output
from ..execution import TIMING_KEY, tool_output_failed, unwrap_tool_output
from ..progress import SOURCE_SEARCH_PREFIX, SearchSource
from ..tool_arguments import call_arguments
from .models import RunRecord, ToolActivity
from .references import INSPECT_TOOL, VerifiedPages, references_from_text
from .store import now

SKILL_TOOLS = {"load_skill", "read_skill_resource", "run_skill_script"}
APPROVAL_ID = re.compile(r"[\x21-\x7E]{1,256}")
REJECTED = "tool call invocation was rejected by user"
DURABLE_PACK = "run_durable_meeting_pack"
DURABLE_STOP = (
    "The request ended before the durable meeting pack finished (in the crash test, the agent process stops here). "
    "Phases that finished are checkpointed in the Foundry state store: select New session and ask to resume the "
    "durable meeting pack."
)


def timed_name(output: object) -> str | None:
    """The tool name in the hosted runtime's timing envelope, if the output has one."""
    value = output
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    timing = value.get(TIMING_KEY) if isinstance(value, dict) else None
    name = timing.get("name") if isinstance(timing, dict) else None
    return name[:256] if isinstance(name, str) and name else None


# Harness tools that call Work IQ themselves, out of the model's sight, and what the harness then does.
HARNESS_WORKIQ_TOOLS = {
    "inspect_document": "page matched by the harness",
    "read_chart": "chart read by the harness's own model",
    "list_client_documents": "the client's folders listed by the harness",
}


def tool_json(output: object, depth: int = 0) -> dict | None:
    """A tool's JSON object result: from JSON text, an object, or text content blocks."""
    if depth > 3:
        return None
    if isinstance(output, dict):
        return output
    if isinstance(output, str):
        try:
            return tool_json(json.loads(output), depth + 1)
        except json.JSONDecodeError:
            return None
    if isinstance(output, list):
        # Text content blocks, or plain strings (how the team's activity record keeps a specialist's tool result).
        for block in output:
            if isinstance(block, dict) and block.get("type") == "text":
                value = tool_json(block.get("text"), depth + 1)
            elif isinstance(block, str):
                value = tool_json(block, depth + 1)
            else:
                continue
            if value is not None:
                return value
    return None


def duration_text(milliseconds: object) -> str:
    if not isinstance(milliseconds, int) or isinstance(milliseconds, bool) or milliseconds < 0:
        return ""
    return f"{milliseconds} ms" if milliseconds < 1000 else f"{milliseconds / 1000:.1f}s"


def usd(value: object) -> str:
    return f"${value:,.2f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "an unknown amount"


# The as-of date at the start of a Fabric IQ answer, such as "As of 10/19/2026" or "as of **2026-10-19**".
AS_OF = re.compile(r"\b[Aa]s of\s*\**\s*(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4})")
# Work IQ retrieve capabilities, by what the audience would call them.
WORKIQ_SOURCES = {
    "Meetings": "meetings and recordings", "TeamsMessages": "Teams chats", "OneDriveAndSharePoint": "OneDrive and SharePoint",
    "Email": "mail", "Calendar": "calendar", "People": "people",
}
# Matched in order against a fetch result's @odata.context: a Teams chat or channel message's context also names
# "messages", so Teams comes before mail.
GRAPH_KINDS = (("calendarview", "calendar"), ("/events", "calendar"), ("chats", "Teams"), ("channels", "Teams"),
               ("messages", "mail"), ("drive", "files"), ("people", "people"))


def call_values(arguments: str) -> dict:
    try:
        # The streamed arguments can hold the whole document twice ('{...}{...}'); keep one copy.
        values = json.loads(call_arguments(arguments)) if arguments else {}
    except (json.JSONDecodeError, TypeError):
        return {}
    return values if isinstance(values, dict) else {}


def graph_reads(results: list) -> str:
    """The Microsoft Graph reads a Work IQ fetch made, by kind, with the number of items each returned."""
    parts = []
    for result in results[:6]:
        if not isinstance(result, dict):
            continue
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        context = str(data.get("@odata.context", "")).lower()
        kind = next((label for key, label in GRAPH_KINDS if key in context), "Graph")
        code = result.get("statusCode")
        if isinstance(code, int) and code >= 400:
            # Only 401 and 403 are a refusal; a 404 or a bad request is a read that failed.
            parts.append(f"{kind} {'denied' if code in (401, 403) else 'failed'} ({code})")
        else:
            items = data.get("value")
            parts.append(f"{kind} ({len(items)})" if isinstance(items, list) else kind)
    return ", ".join(parts)


def workiq_detail(name: str, output: dict, arguments: str) -> str:
    """For the X-ray: which Work IQ capability a call used, on which Microsoft 365 sources, as the signed-in user."""
    tool = name.removeprefix("workiq___")
    values = call_values(arguments)
    if tool == "fetch":
        reads = graph_reads(output.get("results") if isinstance(output.get("results"), list) else [])
        return f"Work IQ fetch: exact Microsoft Graph reads, as you{': ' + reads if reads else ''}"[:300]
    if tool == "retrieve":
        hits = next((v for v in output.values() if isinstance(v, dict) and "resultCount" in v), output)
        count = hits.get("resultCount")
        names = [c.get("name") for c in values.get("capabilities") or [] if isinstance(c, dict)]
        sources = ", ".join(WORKIQ_SOURCES.get(n, n) for n in names if isinstance(n, str)) or "Microsoft 365"
        return (f"Work IQ retrieve: search by meaning in {sources}, as you"
                + (f" · {count} result(s)" if isinstance(count, int) and not isinstance(count, bool) else ""))[:300]
    if tool == "ask":
        files = values.get("fileUrls") if isinstance(values.get("fileUrls"), list) else []
        if values.get("agentId"):
            return f"Work IQ ask: a Copilot agent answered, as you (agent {str(values['agentId'])[:24]})"[:300]
        if files:
            return f"Work IQ ask: Microsoft 365 Copilot reasoned over {len(files)} exact file(s), as you"
        return "Work IQ ask: Microsoft 365 Copilot answered, as you"
    if tool == "list_agents":
        items = next((v for v in (output.get("agents"), output.get("value"), output.get("items")) if isinstance(v, list)), None)
        return "Work IQ list_agents: the Copilot agents you can reach" + (f" ({len(items)})" if items is not None else "")
    if tool == "fetch_blob":
        return "Work IQ fetch_blob: file content downloaded, as you"
    if tool == "create_entity":
        return "Work IQ create_entity: draft saved in Outlook Drafts after your approval, never sent"
    return f"Work IQ {tool}: as you"[:300]


def harness_detail(name: str, output: object) -> str | None:
    """For the X-ray: the Work IQ calls a harness tool made inside it, then what the harness itself did."""
    action = HARNESS_WORKIQ_TOOLS.get(name)
    value = tool_json(output)
    if action is None or value is None:
        return None
    calls = [
        call for call in value.get("workiq_calls") or []
        if isinstance(call, dict) and isinstance(call.get("tool"), str) and call["tool"].isidentifier()
    ]
    if calls:
        made = " · ".join(f"{call['tool'][:20]} {duration_text(call.get('ms'))}".strip() for call in calls[:6])
        inside = f"Inside: Work IQ {made}, as you"
    elif value.get("reused_download") is True:
        inside = "Inside: the file Work IQ downloaded earlier in this turn, as you"
    else:
        inside = "Inside: Work IQ fetch and fetch_blob, as you"
    model = value.get("model_call")
    if isinstance(model, dict) and isinstance(model.get("deployment"), str):
        took = duration_text(model.get("ms"))
        action = f"chart read by {model['deployment'][:40]} in Foundry" + (f", {took}" if took else "")
    documents = value.get("documents")
    if isinstance(documents, list):
        counts = {where: sum(1 for item in documents if isinstance(item, dict) and item.get("where") == where)
                  for where in ("OneDrive", "SharePoint")}
        action = f"the client's folders: OneDrive ({counts['OneDrive']}), SharePoint ({counts['SharePoint']})"
    return f"{inside} · {action}"[:300]


def iq_detail(name: str, output: object, arguments: str = "") -> str | None:
    """For the X-ray: which Microsoft IQ (or which harness step) a call reached, as whom, and what came back."""
    value = tool_json(output)
    if name.startswith("workiq___"):
        return workiq_detail(name, value or {}, arguments)
    if value is None:
        return None
    status = value.get("status")
    if name in ("search_public_web", "read_public_web_page"):
        if status == "WEB_QUERY_BLOCKED":
            reason = str(value.get("reason", "")).split(" because ", 1)[-1].rstrip(".")
            return f"Web IQ: blocked at the boundary before sending, {reason}"[:300]
        if status == "WEB_UNAVAILABLE":
            return "Web IQ: unavailable; the public web was not checked"
        if name == "read_public_web_page":
            return "Web IQ: public page read, outside the Microsoft 365 and Azure boundary"
        results = value.get("results") if isinstance(value.get("results"), list) else []
        return f"Web IQ {value.get('kind', 'news')}: {len(results)} public result(s), outside the Microsoft 365 boundary"
    if name == "search_firm_knowledge":
        if status == "FIRM_KNOWLEDGE_UNAVAILABLE":
            return "Foundry IQ: unavailable; the firm library was not checked"
        passages = value.get("passages") if isinstance(value.get("passages"), list) else []
        codes = list(dict.fromkeys(str(p.get("document", "")).split(" ", 1)[0] for p in passages
                                   if isinstance(p, dict) and p.get("document")))
        return (f"Foundry IQ knowledge base: {len(passages)} passage(s)" + (f" from {', '.join(codes[:5])}" if codes else "")
                + ", as the project's managed identity")[:300]
    if name == "query_portfolio_data":
        if status == "FABRIC_CONSENT_REQUIRED":
            return "Fabric IQ: your one-time consent is needed; select Authorize in the banner"
        if status == "PORTFOLIO_DATA_UNAVAILABLE":
            return "Fabric IQ: unavailable; the portfolio system was not checked"
        found = AS_OF.search(str(value.get("answer", "")))
        return ("Fabric IQ: the Portfolio Analyst data agent answered from the portfolio lakehouse"
                + (f", as of {found.group(1)}" if found else "") + ", as you")[:300]
    if name == "delegate_to_work_iq":
        if status == "WORKIQ_CHAT_CONSENT_REQUIRED":
            return "Work IQ Chat (A2A): your one-time consent is needed; select Authorize in the banner"
        if status == "WORKIQ_CHAT_UNAVAILABLE":
            return "Work IQ Chat (A2A): unavailable; answered with the Work IQ tools instead"
        task = str(value.get("task_id") or "")[:8]
        took = duration_text(value.get("duration_ms"))
        return (f"Work IQ Chat over A2A: task {task} {value.get('state', status)}" + (f" in {took}" if took else "")
                + ", answered by Work IQ's own agent as you")[:300]
    if name == "recall_client_memory":
        found = [label for key, label in (("last_discussion", "last discussion"), ("last_brief", "last brief"))
                 if value.get(key)]
        preferences = value.get("advisor_preferences")
        if isinstance(preferences, list) and preferences:
            found.append(f"{len(preferences)} preference(s)")
        return ("Durable client memory in the Foundry state store, yours only: "
                + (", ".join(found) if found else "nothing saved yet"))[:300]
    if name == "record_brief" and value.get("saved") is True:
        return "Durable client memory: this brief saved in the Foundry state store for next time"
    if name == "liquidity_check":
        short = value.get("shortfall_usd")
        return (f"Harness check, in code: {usd(value.get('available_above_reserve_usd'))} above the reserve"
                + (f"; shortfall {usd(short)}" if isinstance(short, (int, float)) and short > 0
                   else "; the needs are covered"))[:300]
    if name == "rebalancing_check":
        weight, limit = value.get("current_weight_pct"), value.get("limit_pct")
        if not value.get("above_limit"):
            return f"Harness check, in code: {weight}% is within the {limit}% limit"[:300]
        return (f"Harness check, in code: {weight}% against the {limit}% limit; a trim to the limit sells "
                f"{usd(value.get('trim_to_limit_usd'))} and realizes {usd(value.get('realized_gain_on_trim_usd'))}"
                + (", over the tax budget" if value.get("trim_exceeds_tax_budget") else ", within the tax budget"))[:300]
    if name == "run_durable_meeting_pack":
        report = value.get("durability") if isinstance(value.get("durability"), dict) else {}
        marker = report.get("progress_marker") if isinstance(report.get("progress_marker"), dict) else {}
        counts = [len(report.get(k) or []) for k in ("restored_checkpoints", "reconciled_checkpoints",
                                                       "executed_in_this_process")]
        return (f"Attempt {value.get('attempt')}: restored {counts[0]} checkpoint(s), reused {counts[1]} written "
                f"before a stop, ran {counts[2]} phase(s); progress marker {marker.get('next_wave')}")[:300]
    return None


class ResponseEvents:
    def __init__(self, record: RunRecord, project_endpoint: str, *, continuation: bool = False) -> None:
        self.record = record
        self.project_host = urlsplit(project_endpoint).hostname
        # A continuation answers an approval: the approved tool's call belongs to the previous response.
        self.continuation = continuation
        self.text: dict[str, str] = {}
        self.ended_messages: set[str] = set()
        self.calls: dict[str, ToolActivity] = {}
        self.arguments: dict[str, str] = {}
        self.ended_arguments: set[str] = set()
        self.returned_calls: set[str] = set()
        self.item_calls: dict[str, str] = {}
        self.approvals: set[str] = set()
        self.verified = VerifiedPages()
        self.terminal = False

    def activity_event(self) -> CustomEvent:
        return CustomEvent(name="workiq.activity", value=self.record.model_dump(mode="json"))

    def start_text(self, identifier: str) -> list[BaseEvent]:
        if identifier in self.text:
            return []
        self.text[identifier] = ""
        self.record.assistant_message_ids.append(identifier)
        self.record.stage = "answering"
        return [
            self.activity_event(), TextMessageStartEvent(message_id=identifier, role="assistant"),
        ]

    def finish_text(self, identifier: str) -> list[BaseEvent]:
        if identifier in self.ended_messages:
            return []
        self.ended_messages.add(identifier)
        return [TextMessageEndEvent(message_id=identifier)]

    def start_call(self, item: dict) -> list[BaseEvent]:
        identifier, name = item.get("call_id"), item.get("name")
        if not isinstance(identifier, str) or not isinstance(name, str):
            raise HTTPException(status_code=502, detail="Foundry returned an invalid tool call.")
        if identifier in self.calls:
            return []
        source: SearchSource | None = None
        if name == f"{SOURCE_SEARCH_PREFIX}Email":
            source = "Email"
        elif name == f"{SOURCE_SEARCH_PREFIX}OneDriveAndSharePoint":
            source = "OneDriveAndSharePoint"
        activity = ToolActivity(
            call_id=identifier, name=name, started_at=now(),
            kind="skill" if name in SKILL_TOOLS else "tool", source=source,
        )
        self.record.stage = (
            "searching" if source or name == "workiq___retrieve"
            else "loading_skill" if activity.kind == "skill" else "running_tool"
        )
        self.calls[identifier] = activity
        if isinstance(item.get("id"), str):
            self.item_calls[item["id"]] = identifier
        self.arguments[identifier] = ""
        self.record.activity.append(activity)
        return [
            ToolCallStartEvent(tool_call_id=identifier, tool_call_name=name),
            self.activity_event(),
        ]

    def finish_arguments(self, identifier: str, arguments: str) -> list[BaseEvent]:
        if identifier in self.ended_arguments:
            return []
        arguments = call_arguments(arguments)
        events: list[BaseEvent] = []
        if not self.arguments.get(identifier) and arguments:
            events.append(ToolCallArgsEvent(tool_call_id=identifier, delta=arguments))
            self.arguments[identifier] = arguments
        activity = self.calls[identifier]
        if activity.kind == "skill" or activity.source is not None:
            try:
                values = json.loads(arguments)
            except json.JSONDecodeError as error:
                raise HTTPException(
                    status_code=502, detail="Foundry returned invalid activity arguments.",
                ) from error
            if activity.source is not None:
                parent = values.get("parent_call_id") if isinstance(values, dict) else None
                if (
                    not isinstance(values, dict) or set(values) != {"source", "parent_call_id"}
                    or values["source"] != activity.source or not isinstance(parent, str)
                    or parent not in self.calls
                    or self.calls[parent].name != "workiq___retrieve"
                ):
                    raise HTTPException(
                        status_code=502, detail="Foundry returned invalid source search metadata.",
                    )
                activity.parent_call_id = parent
            elif isinstance(values, dict):
                name = values.get("skill_name") or values.get("skill") or values.get("name")
                if isinstance(name, str) and name:
                    activity.skill = name[:256]
        self.ended_arguments.add(identifier)
        events.extend([ToolCallEndEvent(tool_call_id=identifier), self.activity_event()])
        return events

    def finish_call(self, item: dict) -> list[BaseEvent]:
        identifier = item.get("call_id")
        events: list[BaseEvent] = []
        if identifier not in self.calls:
            if not self.continuation or not isinstance(identifier, str) or not identifier:
                raise HTTPException(status_code=502, detail="Foundry returned a result for an unknown tool call.")
            # The approved tool runs now; its call was in the response that asked for approval.
            rejected = isinstance(item.get("output"), str) and REJECTED in item["output"].lower()
            events.extend(self.start_call({
                "call_id": identifier,
                "name": timed_name(item.get("output")) or ("rejected_action" if rejected else "approved_action"),
            }))
            events.extend(self.finish_arguments(identifier, ""))
        if identifier in self.returned_calls:
            return events
        self.returned_calls.add(identifier)
        activity = self.calls[identifier]
        # The team's envelope and the timing envelope can come in either order.
        raw, team = unwrap_team_output(item.get("output"))
        try:
            output, timing = unwrap_tool_output(
                raw, call_id=identifier, name=activity.name,
            )
        except ValueError as error:
            raise HTTPException(status_code=502, detail="Foundry returned invalid tool timing.") from error
        output, inner_team = unwrap_team_output(output)
        team = team or inner_team
        nested_consent = self.add_team_records(identifier, team) if team else []
        failed = tool_output_failed(output, skill=activity.kind == "skill") or bool(timing and timing.failed)
        activity.status = "failed" if failed else "completed"
        # A skill's instructions can quote the rejection text; only a tool result can be a rejection.
        if activity.kind != "skill" and isinstance(output, str) and REJECTED in output.lower():
            activity.status = "cancelled"
        if activity.source is not None:
            if (
                timing is None or not isinstance(output, dict) or set(output) != {"status"}
                or output["status"] not in {"completed", "failed", "cancelled"}
                or timing.failed != (output["status"] != "completed")
            ):
                raise HTTPException(status_code=502, detail="Foundry returned invalid source search status.")
            activity.status = output["status"]
        if activity.name == INSPECT_TOOL and not failed:
            # Only a real inspect_document result can mark a cited page as verified.
            self.verified.record(output)
        if not failed:
            activity.detail = (harness_detail(activity.name, output)
                               or iq_detail(activity.name, output, self.arguments.get(identifier, "")))
        if timing:
            activity.started_at = timing.started_at.isoformat()
            activity.duration_ms = timing.duration_ms
            activity.timing_source = "runtime"
        if not any(call.status == "running" for call in self.record.activity):
            self.record.stage = "planning"
        return [
            *events,
            ToolCallResultEvent(
                message_id=f"tool-{uuid4()}", tool_call_id=identifier,
                content=json.dumps({"status": activity.status}),
            ),
            self.activity_event(),
            *self.tool_consent(activity.name, output),
            *nested_consent,
        ]

    def add_team_records(self, parent: str, team: dict) -> list[BaseEvent]:
        """The specialists' calls inside one of the lead's delegations, as nested X-ray entries in their lanes. Returns
        the consent banners for a specialist's Fabric IQ or Work IQ Chat call that needs the user's consent: its link
        reaches the app only here, intact (the specialist's model would retell it)."""
        consent: list[BaseEvent] = []
        links: set[str] = set()
        titles = {"client-context": "Client context · Work IQ", "portfolio": "Portfolio · Fabric IQ",
                  "firm-policy": "Firm policy · Foundry IQ", "market": "Market · Web IQ",
                  "writer": "Writer · math in code", "reviewer": "Compliance reviewer · Foundry IQ",
                  "what-if": "What-if analyst · code in a sandbox"}
        for index, call in enumerate(team.get("calls") or []):
            if not isinstance(call, dict) or not call.get("tool"):
                continue
            identifier = f"{parent}/{call.get('lane', 'lane')}/{call.get('call_id') or index}"
            if identifier in self.calls:
                continue
            raw = call.get("output") or ""
            value = tool_json(raw) or raw
            failed = bool(call.get("failed"))
            activity = ToolActivity(
                call_id=identifier[:512], name=str(call["tool"])[:512],
                kind="skill" if call["tool"] == "load_skill" else "tool",
                parent_call_id=parent, status="failed" if failed else "completed",
                started_at=str(call.get("started_at") or now_iso()), duration_ms=int(call.get("duration_ms") or 0),
                timing_source="runtime", lane=str(call.get("lane") or "")[:64] or None,
                lane_title=titles.get(str(call.get("lane")), str(call.get("lane") or ""))[:64] or None,
            )
            if not failed:
                try:
                    detail = harness_detail(activity.name, value) or iq_detail(activity.name, value, call.get("arguments") or "")
                    activity.detail = detail[:300] if detail else None
                except Exception:
                    activity.detail = None
            self.calls[identifier] = activity
            self.record.activity.append(activity)
            for event in self.tool_consent(activity.name, value):
                url = event.value.get("url") if isinstance(getattr(event, "value", None), dict) else None
                if url not in links:
                    links.add(url)
                    consent.append(event)
        steps = [s for s in (team.get("steps") or []) if isinstance(s, dict)]
        self.record.team_steps.extend(steps[:200])
        if isinstance(team.get("verdict"), dict):
            self.record.team_verdict = team["verdict"]
        return consent

    def tool_consent(self, name: str, output: object) -> list[BaseEvent]:
        """The consent banner for a link a tool returned: Fabric IQ and Work IQ Chat (A2A) are called inside a tool,
        so Foundry can't raise their consent requests, and the model must not be trusted to copy a long link intact."""
        value = tool_json(output)
        server = {("query_portfolio_data", "FABRIC_CONSENT_REQUIRED"): "fabric_portfolio",
                  ("delegate_to_work_iq", "WORKIQ_CHAT_CONSENT_REQUIRED"): "workiq_chat_a2a"}.get(
            (name, value.get("status") if value else None))
        if value is None or server is None:
            return []
        try:
            return [self.consent_event({"consent_link": value.get("consent_link"), "server_label": server})]
        except HTTPException:
            return []

    def approval_event(self, item: dict) -> list[BaseEvent]:
        """An approval-gated tool call waiting for the advisor: show what it will do, with Approve and Reject."""
        identifier = item.get("id")
        if not isinstance(identifier, str) or not APPROVAL_ID.fullmatch(identifier):
            raise HTTPException(status_code=502, detail="Foundry returned an invalid approval request.")
        if identifier in self.approvals:
            return []
        self.approvals.add(identifier)
        name = item.get("name") if isinstance(item.get("name"), str) else None
        arguments: object = item.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(call_arguments(arguments))
            except json.JSONDecodeError:
                arguments = {}
        values = {
            key[:64]: value[:4000] for key, value in arguments.items()
            if isinstance(key, str) and isinstance(value, str)
        } if isinstance(arguments, dict) else {}
        for activity in self.record.activity:
            if activity.status == "running" and activity.name == name:
                activity.status = "awaiting_approval"
        server = item.get("server_label")
        return [
            CustomEvent(name="workiq.approval", value={
                "id": identifier, "name": name[:256] if name else None,
                "server": server[:256] if isinstance(server, str) else None,
                "arguments": values, "response_id": self.record.response_id,
            }),
            self.activity_event(),
        ]

    def consent_event(self, item: dict) -> CustomEvent:
        candidates = [item.get(key) for key in ("consent_link", "consent_url", "url", "authorization_url")]
        for value in candidates:
            if not isinstance(value, str):
                continue
            parsed = urlsplit(value)
            hostname = parsed.hostname or ""
            if (
                parsed.scheme == "https" and not parsed.username and not parsed.password
                and not any(ord(character) < 32 for character in value)
                and (
                    hostname in {"login.microsoftonline.com", "consent.azure-apim.net", self.project_host}
                    or hostname.endswith(".consent.azure-apim.net")
                )
            ):
                return CustomEvent(
                    name="workiq.consent",
                    value={
                        "url": value, "response_id": self.record.response_id,
                        "server": item.get("server_label") if isinstance(item.get("server_label"), str) else None,
                    },
                )
        raise HTTPException(
            status_code=502, detail="Foundry requested consent without a supported HTTPS link.",
        )

    def completed_item(self, item: dict) -> list[BaseEvent]:
        kind = item.get("type")
        events: list[BaseEvent] = []
        if kind == "message" and item.get("role") == "assistant":
            identifier = item["id"]
            content = item.get("content", [])
            text = "".join(
                block.get("refusal", "") if block.get("type") == "refusal" else block.get("text", "")
                for block in content if block.get("type") in ("output_text", "refusal")
            )
            if identifier not in self.text and text:
                events.extend(self.start_text(identifier))
                self.text[identifier] = text
                events.append(TextMessageContentEvent(message_id=identifier, delta=text))
            if identifier in self.text:
                events.extend(self.finish_text(identifier))
                annotations = [
                    annotation for block in content for annotation in block.get("annotations", [])
                ]
                references = references_from_text(
                    text or self.text[identifier], annotations, verified=self.verified,
                )
                if references:
                    events.append(CustomEvent(
                        name="workiq.references",
                        value={
                            "message_id": identifier,
                            "references": [reference.model_dump(mode="json") for reference in references],
                        },
                    ))
        elif kind == "function_call":
            events.extend(self.start_call(item))
            events.extend(self.finish_arguments(item["call_id"], item.get("arguments", "{}")))
        elif kind == "function_call_output":
            events.extend(self.finish_call(item))
        elif kind == "oauth_consent_request":
            events.append(self.consent_event(item))
        elif kind in ("mcp_approval_request", "function_approval_request"):
            events.extend(self.approval_event(item))
        return events

    def consume(self, event: dict) -> list[BaseEvent]:
        kind = event["type"]
        if kind == "response.created":
            self.record.response_id = event["response"]["id"]
            self.record.stage = "planning"
            return [self.activity_event()]
        if kind in ("response.output_text.delta", "response.refusal.delta"):
            identifier, delta = event["item_id"], event["delta"]
            events = self.start_text(identifier)
            self.text[identifier] += delta
            events.append(TextMessageContentEvent(message_id=identifier, delta=delta))
            return events
        if kind == "response.output_item.added" and event["item"].get("type") == "function_call":
            return self.start_call(event["item"])
        if kind in ("response.function_call_arguments.delta", "response.function_call_arguments.done"):
            identifier = event.get("call_id")
            item_id = event.get("item_id")
            if not isinstance(identifier, str):
                identifier = self.item_calls.get(item_id) if isinstance(item_id, str) else None
            if not isinstance(identifier, str):
                raise HTTPException(status_code=502, detail="Foundry returned arguments for an unknown tool.")
            if kind == "response.function_call_arguments.delta":
                self.arguments[identifier] += event["delta"]
                return [ToolCallArgsEvent(tool_call_id=identifier, delta=event["delta"])]
            return self.finish_arguments(identifier, event["arguments"])
        if kind == "response.output_item.done":
            return self.completed_item(event["item"])
        if kind in ("response.completed", "response.incomplete", "response.failed"):
            response = event["response"]
            self.record.response_id = response["id"]
            events = [
                mapped for item in response.get("output", []) for mapped in self.completed_item(item)
            ]
            if response.get("error"):
                raise HTTPException(
                    status_code=502,
                    detail=f"Hosted agent failed ({response['error'].get('code', 'server_error')}).",
                )
            status = response.get("status")
            if status not in ("completed", "incomplete"):
                raise HTTPException(status_code=502, detail="Hosted agent did not complete its response.")
            self.record.status = status
            self.terminal = True
            events.append(self.activity_event())
            return events
        if kind == "error":
            raise HTTPException(status_code=502, detail="Foundry returned a streaming error.")
        return []

    def stopped_durable_pack(self) -> bool:
        """Whether the request ended while the durable meeting pack was still running, as in the crash test."""
        return any(
            activity.name == DURABLE_PACK and activity.status == "running" for activity in self.record.activity
        )

    def interrupt(self, failed: bool, stopped: bool = False) -> None:
        """End the run; with stopped, unfinished calls were cut off by a stopped process, not failed by themselves."""
        self.record.status = "failed" if failed else "cancelled"
        for activity in self.record.activity:
            if activity.status == "running":
                activity.status = "failed" if failed and not stopped else "cancelled"
