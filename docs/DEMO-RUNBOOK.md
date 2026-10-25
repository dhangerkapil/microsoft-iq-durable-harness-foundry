# Demo runbook

Everything around the live demo: the one-time setup, the checklist from T-60 to T-0, how to reset the presenter
controls, the prewarm, the fallbacks, the timing table and what to do afterwards. The live steps are in
[DEMO-SCRIPT.md](DEMO-SCRIPT.md); the detail behind each step is in [DEMO-WALKTHROUGH.md](DEMO-WALKTHROUGH.md).

> **Terms.** Investment Advisor is a multi-agent solution: a **lead agent** plans and delegates to four **specialist agents**, one per Microsoft IQ (the client context, portfolio, firm policy and market agents); the **writer agent** drafts the brief, and the **compliance reviewer agent** and a **number check** in code check it. *The advisor* is the person using the app.

Run every command in PowerShell 7 from the repository root. Each PowerShell script loads the isolated profile from
`.env.local` itself (`scripts/Use-IsolatedAzure.ps1`); if one says the profile isn't signed in, run
`.\scripts\Connect-IsolatedAzure.ps1`. The Python scripts don't load it, so start their session with:

```powershell
. .\scripts\Use-IsolatedAzure.ps1 -RequireLogin            # az, terraform and the work folder of this deployment
$py = Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe'   # the profile's Python (DEPLOYMENT.md, "Your machine")
```

## Last verified

Hosted, on 2026-10-23, with the rehearsal runner: every segment against `advisor-team` version 30, then the steps the
day's last fix touched (1.1, 1.5, 2.1, 2.2, 2.4, 2.5, 3.2, 3.4, 4.1
and 9.3) against version 31, with the advisor's Work IQ consent, Fabric running and that morning's note published
(`Invoke-MorningNote.ps1 -Approve`). Every step passed except 6.4, which needs the analyst's own sign-in (checked by
hand with the analyst account on 2026-10-21). 9.3b ran 189-192 s, now inside its budget of 215 s: 2.1's, for the same
prompt. Each step's time is in the [timing table](#timing-table).

- **Review packs on the one hosted agent** (2026-10-25, `advisor-team` version 36). Workflow W3 now runs as durable
  steps on `advisor-team` itself. `review_packs.py --crash-test --approve` passed in 308 s: process 1 planned, wrote
  packs 1 and 2 and stopped before pack 3; the step went again in a new session, and process 2 restored packs 1-2 from
  the state store and wrote pack 3; each draft was previewed. The app's panel does the same on its own. Run segment
  `packs` in the next full rehearsal.

- **The brief** (1.1, 9.2b) arrives in one piece under the lead agent's one line: code adds it exactly as the workflow
  returned it (107-132 s and 155 s). Each fact carries its item, date and IQ; each breach has its own bullet and only a
  rule whose section names it; Risks and conflicts ends with the firm's tax disclosure.
- **The sources open.** A specialist agent that mis-copies a cited item's ID (about one in seven Outlook items in
  rehearsals) gets it repaired when its report is recorded, against everything its tools returned; an item that still
  can't be matched is left as cited, and the app offers **Open original** instead of guessing. The source check
  (`scripts/check_sources.py`) opened every cited item of both runs: 67 and 48 of them.
- **Fabric IQ is the slowest IQ.** One long question to Fabric's data agent took about 100 s, the limit on a tool call,
  and timed out when two runs used Fabric at the same moment; the portfolio agent now sends two short questions in
  parallel (2.2 in 93-122 s). Don't run rehearsals, evaluations or the optimizer while presenting.
- **Specialist agent rate limits.** The four specialist agents share one deployment (`team-specialist`, gpt-5.6-luna, 100,000
  tokens a minute; the EU Data Zone quota for the model is fully used). Back-to-back Work IQ-heavy steps can get a 429:
  6.3 did twice in a row in an earlier rehearsal and passed when run with a quiet minute before it. At a presenter's
  pace this is rare; for headroom, request more gpt-5.6-luna Data Zone quota and raise `team_specialist_capacity`.
- **Purview on** (2026-10-24, `advisor-team` version 35). Every prompt and answer of
  the lead agent's turns goes through Purview: 2.6 came back blocked in 11 s, a full brief took 127 s and cleared, and
  the answer appears when it is complete rather than streaming. Purview recorded each block (`DLPRuleMatch`) and its
  alert, and they reached Defender within about fifteen minutes; Defender's 6.5 query and `Get-WorkIqAuditTrail.ps1`
  showed the Work IQ calls of the day's runs. The Agent 365 records (`InvokeAgent`) were the slowest: runs from the two
  hours before weren't in Defender yet, hence the evening run in [Before the demo](#before-the-demo). Not yet in a full
  rehearsal: run one with `purview` in `--have` ([Rehearse](#rehearse)) before the next demo.

Update this section and the table from the newest green rehearsal report before each demo.

## One-time setup

Do these once per deployment, at least a day before the first demo.

| What | How | Why |
|---|---|---|
| Work IQ tenant policy | Microsoft 365 admin center > **Agents** > **Tools** > **Work IQ MCP** > **Policies** > **Tenant Default**: Directory lookups off; Allow write actions and Allow create on; partial update, replace and delete off | 6.1 tours it; 6.2 and 6.3 depend on it. Changes can take hours to apply |
| The advisor's consent | Window A, the app: **New session**, send the 1.1 prompt. Click each **Authorize** link in the banner once, finish sign-in, then **Retry**. Then ask the 2.2 Fabric IQ question and the 5.2 delegation once each | Work IQ's consent shows as the banner at the start of a turn. Fabric IQ and Work IQ Chat (A2A) are called inside a specialist agent, so their consent link can come back inside the answer instead of the banner (to verify after consent): open it once, accept, ask again. The review packs run on the same agent, so these consents cover them |
| The analyst account's consent | Window B, signed in as `DEMO_ANALYST_UPN`: the same steps | Step 6.4 runs as this user |
| The story content | Your own tenant only: the Content step, the recorded review, the Monday stage and the saved baseline ([DEPLOYMENT.md](DEPLOYMENT.md#step-3-the-steps-that-need-a-person)) | Segments 1, 3, 4 and 5 read this story |
| The quality items | The rubric, the golden-question run, the hourly evaluation, an Insights scan and an optimizer job ([QUALITY-LOOP.md](QUALITY-LOOP.md)) | Segment 8 shows them |
| Defender's inventory | Deploy the agents at least a day before | `AgentsInfo` can take a day to list a new agent |
| Microsoft Purview for the agent | Purview pay-as-you-go billing for AI apps on the subscription; `enable_purview = true` in `terraform/terraform.tfvars`, then the Agent step (Terraform grants the agent identity three Microsoft Graph application roles); then `.\scripts\Set-PurviewDlpForAgent.ps1 -UserPrincipalName <a compliance admin>` (Exchange Online Management module; it signs you in to Security & Compliance PowerShell) | 2.6 shows the DLP block and 6.5 its record. A new DLP policy can take up to an hour to apply |
| Defender's logs | The Microsoft 365 connector in Defender for Cloud Apps (the Agent 365 and Purview audit records in `CloudAppEvents`), and an Entra diagnostic setting that sends the Microsoft Graph activity logs to the Microsoft Sentinel workspace onboarded to the Defender portal | 6.5's query and `Get-WorkIqAuditTrail.ps1` read both |

**The story clock.** The story is dated relative to `STORY_MEETING_DATE`: the analyst's alert is on the story's
Monday at 4:45 PM ET, and the demo is on the Tuesday or Wednesday. Outside that window, hold the agents' clock inside
it: set `agent_extra_environment` in `terraform/terraform.tfvars` and rerun the Agent step.

- **Before the story's Monday alert** (rehearsing early), a floor: `agent_extra_environment = { ADVISOR_CLOCK_NOT_BEFORE
  = "<the story's Tuesday, 09:00 ET, as ISO 8601>" }`. Before it, the agent rightly treats Monday's items as the future
  and leaves them out. Once real time passes the floor, real time applies again.
- **After the story's Thursday** (presenting a week that has passed, without moving it), a ceiling:
  `agent_extra_environment = { ADVISOR_CLOCK_NOT_AFTER = "<the story's Wednesday, 14:00 ET, as ISO 8601>" }`. The
  agents stay on that Wednesday afternoon, so Thursday's meeting is still ahead: the clock in every role's
  instructions and the meeting pack's calendar window use it. It holds until you remove it. Public sources keep their
  real dates: Web IQ news and prices can be dated after the story's Wednesday. Moving the story to a new week instead
  means new content and a newly recorded review ([DEPLOYMENT.md](DEPLOYMENT.md#the-story-week)).

## Before the demo

| When | Do | Why |
|---|---|---|
| The evening before (at least three hours before) | In the app as the advisor, a **New session**: send 1.1, then 2.6 | 6.5's query reads one day back, and the Agent 365 records took more than two hours to reach Defender in testing: this run gives it its Agent 365 row |
| T-60 | The story clock: compare today with the story week. After the story's Thursday, `terraform/terraform.tfvars` must hold `agent_extra_environment = { ADVISOR_CLOCK_NOT_AFTER = "<the story's Wednesday, 14:00 ET>" }`; before its Monday alert, `ADVISOR_CLOCK_NOT_BEFORE`; inside the week, neither. If you change it, rerun the Agent step ([the story clock](#one-time-setup)), then check that the T-10 brief calls Thursday's meeting upcoming | Outside the week, the agents treat Thursday's meeting as past, or Monday's items as the future |
| T-60 | **Window A**, Edge InPrivate: the advisor. **Window B**, another browser profile: the app as the analyst account. Zoom 125%, notifications off, other tenants out of both windows | A stale or shared sign-in fails mid-demo |
| T-45 | Open the tabs listed in the [walkthrough](DEMO-WALKTHROUGH.md#windows-and-tabs) in window A | No hunting for tabs live |
| T-45 | In the app, a **New session**: send 1.1, then 2.5 and 2.6 in the same session (2.6 must come back as Purview's block). Then run 6.5's query in the Defender tab and check its four rows | 6.5 shows these: Graph's records reach Defender within minutes and 2.6's DLP match within about fifteen; Purview Audit's can take up to an hour, and the Agent 365 row comes from the evening run |
| T-30 | `.\scripts\Start-DemoPrewarm.ps1 -IncludeFabric` (2-5 minutes). Read its output for "Fabric answered in …s", any "CONSENT LINK" and the app's address | Resumes the Fabric capacity and warms its data agent; asks the agent to clear the Fabric outage, the crash switch and saved preferences; starts a warm container |
| T-25 | [Reset the presenter controls](#reset-the-presenter-controls) | The prewarm doesn't clear the reviewer test, the review-packs crash or a specialist agent outage |
| T-20 | [Check Web IQ](#check-web-iq) | Web IQ is in preview; check it shortly before, not once |
| T-15 | Optional: `& $py scripts\rehearse.py --segments eachiq firm web --have workiq-consent fabric webiq purview` (side-effect-free segments only) | A last green run; the report lands in the work folder |
| T-10 | In the app: **New session**, send the 1.1 prompt, keep that session as the backup answer. **Delete** the other sessions in the sidebar. Delete any leftover drafts in Outlook Drafts | A finished brief to fall back on; a clean sidebar |
| T-5 | Open the backup session's X-ray and check the lanes and the verdict card; open the **Review packs** panel once and close it | The lanes and the panel need the current UI |
| T-2 | Your own tenant only: `.\data\hydration\Publish-DemoContent.ps1 -Stage Live -ReplaceMail 'One more thing before Thursday: 529 plan'` | Michael's 529 email arrives "now". Never run it in a tenant another demo uses: there, the email shows when it was last sent |

**Don't start an evaluation, an Insights scan or the optimizer while anyone uses the app.** The golden questions and
the optimizer call the agent itself and use its model quota; judges run on `team-judge`, but the agent's answers
during those runs don't.

### Reset the presenter controls

The presenter controls live per advisor in the state store. The prewarm clears `fabric`, `process_crash` and saved
preferences; this clears the rest, without showing anything on screen:

```powershell
.\scripts\Invoke-Agent.ps1 -Prompt "Presenter setup: clear these demo faults by calling demo_fault_switch with enabled false for each: reviewer_test, packs_crash, specialist_client_context, specialist_portfolio, specialist_firm_policy and specialist_market. Then clear my saved advisor preferences (advisor_preferences with action clear) and reply with one line listing what you cleared."
```

About 40 seconds (verified: 37 s); the answer lists what was cleared. If it says a control failed (the model clears
them in parallel, and two writes to the same item can collide), send the prompt again; a control that says
`"simulated_outage": false` is clear. Run it before the demo and after 7.5 (the saved
preference). Every other control clears itself: `reviewer_test`
after one brief, `packs_crash` after it fires, and a specialist agent outage after that specialist agent's next use, so the 9.3
outage never reaches the review packs.

| Control | Set by | Effect | Clears itself |
|---|---|---|---|
| `fabric` | `DEMO_FAULTS` or the switch | A simulated Fabric IQ outage | No (the prewarm clears it) |
| `process_crash` | 7.1 | The next durable meeting pack stops the process between two writes | Yes, when it fires |
| `reviewer_test` | 9.2 | The next brief's first draft carries a seeded advice sentence | Yes, after one brief |
| `packs_crash` | the switch | The next review-packs run stops once before the last pack (the app's checkbox does the same per run) | Yes, when it fires |
| `specialist_client_context`, `specialist_portfolio`, `specialist_firm_policy`, `specialist_market` | 9.3 | That specialist agent reports "not checked" without calling its IQ | Yes, after its next use (the next question that needs it) |

### Check Web IQ

```powershell
.\scripts\Test-ToolboxTool.ps1 -Toolbox team-webiq
.\scripts\Test-ToolboxTool.ps1 -Toolbox team-webiq -Tool webiq___news -Arguments @{ query = 'NVIDIA' }
```

- **Results come back:** nothing to do.
- **`Failed Dependency` or status code 500:** Web IQ's service is failing (the agent retries transient errors twice
  on its own). Rotate the key if you suspect throttling (`.\scripts\Set-WebIqConnection.ps1 -Recreate`, which asks
  for the key), then check again. If it still fails, follow the Web IQ row in [Fallbacks](#fallbacks).

## Timing table

Budgets come from `demo/expectations.yaml` (`budget_s`) for every prompt the rehearsal runner sends; a step fails the
rehearsal when it runs over. "O1 setup" is the morning-note script's own run, with a target instead. "Needs" lists
what the step depends on. "Last verified" is the 2026-10-23 rehearsal on the hosted agent: every segment on
`advisor-team` version 30, and the steps the day's last fix touched again on version 31 (a range covers both runs).

| Step | Prompt (short) | Session | Needs | Budget | Last verified |
|---|---|---|---|---|---|
| 1.1 | Prepare me for my Thursday meeting | New | Work IQ consent, Fabric, Web IQ | 180 s | Pass, 107-132 s |
| 1.5 | What changed since my last discussion | New | Work IQ consent | 180 s | Pass, 118-166 s |
| 2.1 | $400,000 for his home closing | New | Work IQ consent, Fabric, Web IQ | 215 s | Pass, 113-163 s |
| 2.2 Work IQ | Bring me up to speed | New | Work IQ consent | 190 s | Pass, 121-150 s |
| 2.2 Fabric IQ | How has his NVIDIA position moved | New | Work IQ consent, Fabric | 160 s | Pass, 93-122 s (the movement ends on the table's 26.1%) |
| 2.2 Foundry IQ | What does our firm require | New | | 55 s | Pass, 31-39 s |
| 2.2 Web IQ | What's moving NVIDIA this week | New | Web IQ | 60 s | Pass, 32 s on version 31 (on version 30: no links, 90 s; fixed) |
| 2.3 | Wire $400,000 to his title company | New | | 70 s | Pass, 30 s |
| 2.4 | Public news, three bullets | New | Web IQ | 45 s | Pass, 29-37 s |
| 2.5 | Search the web for his position | Same as the step before (2.4) | | 70 s | Pass, 8-9 s |
| 2.6 | A Social Security number in a prompt | Same as 2.5 | Purview (`enable_purview`, the DLP policy) | 40 s | Blocked in 11 s, by hand on version 35 (2026-10-24); rehearse with `purview` in `--have` |
| 3.1 | Friday's Q4 review, quote the transcript | New | Work IQ consent | 140 s | Pass, 118 s |
| 3.2 | Find the chart | New | Work IQ consent | 110 s | Pass, 82-107 s |
| 3.4 | Since that chart, check the email against Fabric | Same | Work IQ consent, Fabric | 180 s | Pass, 95 s on version 31 (version 30's 102 s answer was right; the check wanted the figure and its label on one line) |
| 4.1 | Work IQ versus the Graph API | New | Work IQ consent | 90 s | Pass, 65 s on version 31 (on version 30, 64 s, Work IQ returned no page for the chart, and the answer said so) |
| 5.1 | Compare the signed IPS with v2 | New | Work IQ consent | 120 s | Pass, 84 s |
| 5.2 | Delegate to Work IQ | Same | Work IQ consent | 180 s | Pass, 121 s |
| 5.3 | Which Copilot agents | Same | Work IQ consent | 60 s | Pass, 33 s |
| 6.2 | What is this agent allowed to do | New | Work IQ consent | 90 s | Pass, 33 s |
| 6.3 | Draft a short follow-up email | Same | Work IQ consent | 110 s to the card | Pass, 65 s |
| 6.4 | The 1.1 prompt as the analyst account | New, window B | Work IQ consent, second user | 180 s | Pass by hand, 2026-10-21 |
| 7.1 | Arm the crash test | New | | 75 s | Pass, 16 s |
| 7.2 | Run the durable meeting pack | Same | Work IQ consent | 65 s (stops) | Pass, 51 s |
| 7.3 | What is stored durably for Michael | New | | 50 s | Pass, 23 s |
| 7.4 | Resume the durable meeting pack | Same | Work IQ consent | 105 s | Pass, 102 s |
| 7.5a | Remember my preference | New | | 30 s | Pass, 15 s |
| 7.5b | What is stored durably for me | Same | | 35 s | Pass, 14 s |
| 7.5c | Which process is serving you now (after the restart) | Same | | 35 s | Pass, 24 s |
| 8.7 | Where do his NVIDIA weight, exposure and cash stand | New | Work IQ consent, Fabric | 105 s | Pass, 66 s |
| 9.2 | Arm the reviewer test | New | | 40 s | Pass, 16 s |
| 9.2b | The 1.1 prompt, revised once | Same | Work IQ consent, Fabric, Web IQ | 240 s | Pass, 155 s |
| 9.3a | Take the portfolio specialist offline | New | | 40 s | Pass, 13-14 s |
| 9.3b | The 2.1 prompt, portfolio offline | Same | Work IQ consent, Web IQ | 215 s | 189-192 s: over the old 170 s budget, inside 215 s (2.1's, for the same prompt) |
| 9.4 | Review packs, crash test on | Review packs panel | Work IQ consent, Fabric, Web IQ (as for a brief; without them each pack is labelled degraded) | 600 s | Pass, 308 s with the crash test, from the command line on version 36 (2026-10-25): process 1 wrote packs 1-2 and stopped; process 2, in a new session, restored them and wrote pack 3 |
| O1 setup | Run the morning note and approve it (add-on) | PowerShell: `Invoke-MorningNote.ps1 -Tickers NVDA` | `enable_morning_note` | Target 150 s | 335 s for NVDA (105 s for NVDA, MSFT and AAPL on 2026-10-22) |
| O1 | What is the house view in this morning's note on NVIDIA | New | O1 setup done today | 90 s | Pass, 36 s (IC-MN-20261023) |
| O3 | What if NVIDIA falls 20% (add-on) | New | | 150 s | Pass, 73 s |
| O4 | Convene the investment committee (add-on, experimental) | New | `enable_committee` | 300 s | Pass, 77 s (2026-10-22) |

## Rehearse

```powershell
. .\scripts\Use-IsolatedAzure.ps1 -RequireLogin
$py = Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe'
# Every segment whose prerequisites you have; the others are skipped, not failed (drop purview if Purview is off).
& $py scripts\rehearse.py --have workiq-consent fabric webiq purview
# Then check that every source the answers cite opens, and is the item it names (the newest run; reads only).
& $py scripts\check_sources.py
# A few segments.
& $py scripts\rehearse.py --segments prep fouriqs eachiq firm web --have workiq-consent fabric webiq purview
# Review packs alone, from the command line: crash test on, every card approved.
& $py scripts\review_packs.py --crash-test --approve
# The morning note (add-on): run and approve today's note, then rehearse the team's question about it.
.\scripts\Invoke-MorningNote.ps1 -Tickers NVDA -Approve
& $py scripts\rehearse.py --segments addons --have morning-note
```

The runner reads `demo/expectations.yaml`, sends each prompt to `advisor-team` as the signed-in user, checks the calls,
the lanes, the facts and the time, and writes `work/rehearsal/<time>/report.md` and `results.json` in the profile's work
folder (`%LOCALAPPDATA%\advisor-team\<TEAM_PROFILE>\work`). Segment names are the keys of the expectations file:
`prep`, `fouriqs`, `eachiq`, `firm`, `web`, `meeting`, `chart`, `graph`, `workiq`, `governance`, `draft`, `analyst`,
`durability`, `restart`, `changed`, `fabric`, `team`, `outage`, `packs` and `addons`. Prerequisites for `--have`:
`workiq-consent`, `fabric`, `webiq`, `second-user`, `committee` for the experimental committee add-on, `morning-note`
once today's morning note is published, and `purview` once the agent runs with Purview and the DLP policy (2.6).

Some segments leave state behind: `durability` arms and fires the crash switch, `restart` saves a preference and
restarts the session, `team` arms the reviewer test, `outage` takes the portfolio agent offline, and `draft`
answers an approval card (rejected in the expectations). Run those well before the demo, then
[reset the presenter controls](#reset-the-presenter-controls). Step 6.4 runs as the analyst account, so rehearse it in
window B. `review_packs.py` writes its transcript to `work/review-packs/<time>/transcript.json`.

## Fallbacks

Retry once in a new session first. Then use the row that matches.

| What happens | Why | What to do and say |
|---|---|---|
| The orange consent banner appears | First use of a connection by this user and agent, or an expired consent | Click each **Authorize** link once, finish sign-in in the new tab, close it, then **Retry: send my last message again**. Links expire after about 10 minutes; Retry gets fresh ones. A consent link inside an answer works the same way |
| A lane or an answer says the portfolio was "not checked", or Fabric IQ is unavailable | The Fabric capacity is paused (some subscriptions pause capacities overnight), or the data agent ran past the 100-second limit on a tool call ("timed out"; seen when two runs used Fabric at the same moment) | Say: "The portfolio system is offline, and the agents say so instead of guessing." A timeout: ask again in a new session, and keep rehearsals and evaluations off while presenting. A paused capacity: before the next Fabric step, resume it: `.\scripts\Set-FabricCapacityState.ps1 -State Active` (about a minute; up to 15 minutes if a pause is still in progress), then warm it with `.\scripts\Start-DemoPrewarm.ps1 -IncludeFabric`. If the capacity is shared, ask its owner first |
| "Web IQ is unavailable right now", or Web IQ lines are empty | Web IQ (preview) outage | Say: "Web IQ is in preview and having an outage; notice the agents carry on with the other three IQs." Skip the Web IQ question in 2.2 and step 2.4. Step 2.5 still works: it declines before anything reaches Web IQ |
| An answer runs well past its budget | A slow Work IQ call or a stalled model call | Keep narrating the plan. A model call that hasn't answered in 120 s is sent once more automatically (`TEAM_MODEL_STALL_SECONDS`). If it's a brief, open the T-10 backup session |
| A specialist agent is "unavailable" with "exceeded token rate limit" (429) | The four specialist agents share one deployment's 100,000 tokens a minute; back-to-back Work IQ-heavy questions can use it up (the client retries five times first) | Say which IQ wasn't checked, wait a minute, and ask again. Leave a minute between Work IQ-heavy steps (2.2 Work IQ, 3.1, 5.x, 6.3) |
| A one-IQ question in 2.2 shows a second lane | The model strayed: about one in six of the rehearsal runs of the Work IQ and Fabric IQ questions did (the answer was still right) | **New session** and ask again; the answer should come from one lane. Don't present the second lane as a planned double-check. Out of time: say the lead agent asked a second specialist agent this time, that routing is the model's call, and the X-ray shows exactly what it did |
| The review-packs status says "The agent process stopped. Sending the step again in a new session" | The crash test working: the process stopped before the last pack, and the interrupted conversation stays locked, so the panel sends the step again in a new session; a new process restores the finished packs from the state store | Keep the panel open: it sends the step again 5 seconds after each stop, up to 36 times, and a closed panel stops driving the run. The new process usually answers within a minute; talk through what's happening meanwhile. If the panel says "The agent didn't come back", close it and start another run without the crash test, or show the transcript of the last `review_packs.py --crash-test --approve` rehearsal |
| The review-packs run ends with an error | A failed step or a model outage | Start another run without the crash test, or show the transcript of the last `review_packs.py --crash-test --approve` rehearsal |
| The app won't sign in, or says "A previous sign-in attempt is still pending" | A stale or blocked sign-in | Close any old sign-in window, then **Retry sign-in**. Sign out and in again in that window; keep other tenants out of it. As a last resort, open the app in a fresh InPrivate window |
| A script fails with a token or Continuous Access Evaluation error | Sign-ins from before a policy change are no longer accepted | `.\scripts\Connect-IsolatedAzure.ps1` (it signs out first), then rerun the script |
| "in-flight turn", or a session that won't answer after a crash test | That conversation was interrupted on purpose and stays locked | **New session** |
| No approval card for 6.3 | A reload, or the card was answered | **New session**, send 6.3 again; typing "approve" does nothing |
| 2.6 comes back as an ordinary answer, not Purview's block | Purview is off for the agent (`enable_purview`), the DLP policy is missing or hasn't synced yet (up to an hour), or Purview's pay-as-you-go billing isn't set up | Say what the block shows and move on. Afterwards: `Set-PurviewDlpForAgent.ps1` (it's idempotent) and the agent's environment (`TEAM_PURVIEW=on`) |
| 6.5's query has no Agent 365, Purview Audit or Purview DLP row | The audit records haven't reached Defender yet (DLP about fifteen minutes, Purview Audit up to an hour, Agent 365 more than two hours), or the window (`since`) is too short | Widen `since` to `ago(7d)` to show earlier runs, and say the newest records are on their way |
| The crash test doesn't stop the meeting pack | The one-shot switch was used in a rehearsal | Send 7.1 again, then 7.2 |
| The 9.2 brief comes back without a revision | The reviewer test wasn't armed in this session, or was used | Send "Arm the reviewer test." again, then the brief |
| The app itself fails the same step twice | A service problem | Show the T-10 backup answer, or the saved answers in the last rehearsal report, and describe the step from the walkthrough. If you deployed the single-agent build as a second agent (see below), switch to its app |

**A single-agent fallback (optional, not deployed by default).** The same image runs one agent with every tool when
`TEAM_MODE=solo`. To keep one next to the live agent, release it under its own name and its own state stores, then give it an
app (to verify: this path has not been rehearsed for this build):

```powershell
.\scripts\Publish-CandidateAgent.ps1 -AgentName advisor-team-solo -Environment @{ TEAM_MODE = 'solo'; OPTIMIZATION_LOCAL_DIR = '/app/.agent_configs/solo' }
.\scripts\Publish-UiInstance.ps1 -AgentName advisor-team-solo
```

Each user consents once for it, as for any agent. It has no lanes, verdict card or review packs.

## Recovery moves during the demo

- **Behind schedule:** skip in this order: 2.3 and 2.4, 3.1, 7.5, 6.4, 5.3, 9.3, 9.5.
- **A one-IQ question ran another lane:** new session, ask again; the answer should come from one lane.
- **A brief says Not cleared:** read one finding aloud; it's the checker working. Then show the T-10 backup brief.
- **Fabric went down mid-demo:** use 3.4 and 9.3 to make the point that an outage is labelled, never filled in.
- **The packs panel was closed during a run:** the panel drives the run one step at a time, so the run stops after the
  step in flight (its packs stay in the state store) and a reopened panel starts a new run. Start one without the crash
  test, or show the rehearsal transcript.

## After the demo

1. Delete the draft from Outlook Drafts if you approved 6.3, and the demo sessions in both windows (**Delete** under
   each session).
2. [Reset the presenter controls](#reset-the-presenter-controls).
3. Leave the Fabric capacity running if you'll use the solution again soon. Only to save cost, pause a capacity you
   own: `.\scripts\Set-FabricCapacityState.ps1 -State Paused`. A shared capacity stays as its owner wants it.
4. Keep or remove the hourly evaluation: `.\evals\Invoke-FoundryQuality.ps1 schedule -Delete` removes it.
5. Sign the analyst account out of window B.
6. If the morning note is on, its timer starts a note every weekday morning, and each run waits for an answer and
   calls Web IQ: answer a waiting run with `.\scripts\Invoke-MorningNote.ps1 -InstanceId <id> -Reject` (the instance
   is in the Durable Task Scheduler dashboard), or set `morning_note_schedule = ""` and rerun the Platform step and
   `Publish-MorningNote.ps1` to run it on demand only.
