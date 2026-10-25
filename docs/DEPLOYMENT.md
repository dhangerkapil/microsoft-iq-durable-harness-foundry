# Deploy Investment Advisor

This guide takes a Microsoft 365 tenant and an Azure subscription to a working deployment: the hosted agent, the app,
the four Microsoft IQ sources (Work IQ, Fabric IQ, Foundry IQ and, with a key, Web IQ), the quality tooling and,
in your own tenant, the fictional Microsoft 365 story it researches. One script does the automated part; a few steps
need a person and are printed at the end of the run. To deploy next to existing shared resources without touching
them, read [ISOLATION.md](ISOLATION.md) first.

> **Terms.** Investment Advisor is a multi-agent solution: a **lead agent** plans and delegates to four **specialist agents**, one per Microsoft IQ (the client context, portfolio, firm policy and market agents); the **writer agent** drafts the brief, and the **compliance reviewer agent** and a **number check** in code check it. *The advisor* is the person using the app.

All names and data are fictional.

## What gets deployed

| Where | What |
|---|---|
| A new resource group | Container Registry, Cosmos DB (the app's sessions, private endpoint), Container Apps with a virtual network (the app), Application Insights and Log Analytics; unless you reuse existing ones: the Foundry account, Azure AI Search (Basic, semantic ranker) and the Fabric capacity (F8 by default). With `-SpecialistLocation`, a second AI Services account in that region holds only `team-specialist` |
| The Foundry account | A project (`proj-advteam-<suffix>` in an existing account); the model deployments `team-lead`, `team-specialist` and `team-judge`, and `text-embedding-3-large` for Foundry IQ (in an existing account it must already exist); a guardrail policy |
| The Foundry project | The hosted agent `advisor-team` (the lead agent, its specialist agents and the workflows); connections for Work IQ (MCP and A2A), Fabric IQ, Foundry IQ (managed identity), Web IQ (API key), the registry and Application Insights; the toolboxes `team-workiq`, `team-workiq-chat`, `team-governance`, `team-drafts`, `team-fabric`, `team-foundryiq`, `team-webiq`; the rubric evaluator, an Insights monitor and the brief-writer prompt agent for the optimizer |
| Azure AI Search | The fictional firm library (six policy documents) in `team-firm-library-index`, the knowledge source `team-firm-library-ks` and the knowledge base `team-firm-knowledge`; the documents are pushed into the index, so no storage account is needed |
| Microsoft Fabric | The workspace "Advisor Team" with the lakehouse `team_portfolio` (fictional clients, prices, positions, transactions) and the data agent "Team Portfolio Analyst" |
| Microsoft Entra ID | App registrations for the toolbox OAuth client, the app (API and browser) and the Fabric IQ connection; the tenant's Work IQ service principal only if the tenant has none |
| Microsoft 365 (opt-in Content step) | The story for the advisor: emails, a calendar event, Word documents in SharePoint, PDFs in OneDrive, and a Teams meeting to record, organized by the analyst |
| The resource group, with `enable_morning_note` (off by default) | The morning note add-on: a user-assigned identity, a Durable Task Scheduler (Consumption) with the task hub `advisor-team`, a storage account without public access or shared keys and with private endpoints for blob, queue and table, a virtual network, and the Flex Consumption Function app `func-mn-<suffix>` |

## Before you start

**Tenant and people**

- A Microsoft 365 tenant with Exchange Online, SharePoint, OneDrive and Teams. A demo or developer tenant is best.
- **The advisor:** you, signed in as a Global Administrator (app registrations, admin consent, the Work IQ service
  principal). The story's emails, meeting and files are addressed to you.
- **The analyst:** a second licensed user with a mailbox and Teams (`-AnalystUpn`). The analyst organizes the recorded
  meeting and is the second user in the permission step (6.4 in the demo).
- **Work IQ API access:** usage-based billing with Copilot Credits, and both users in a spending policy that includes
  the Work IQ API (Microsoft 365 admin center; see the prerequisites in the
  [Work IQ overview](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/)).

**Azure**

- A subscription where you are Owner, in a region with Foundry hosted agents (default `eastus2`).
- Model quota: `gpt-5.6-luna` for `team-lead` and `team-specialist` (100K tokens a minute each, DataZone Standard by
  default), `gpt-5.5` for `team-judge` (150K, Global Standard) and `text-embedding-3-large` (50K). DataZone quota is
  shared across the zone's regions: if it runs short, put `team-specialist` in another zone with `-SpecialistLocation`.
- Azure AI Search capacity for a Basic service with the semantic ranker; `-SearchLocation` puts it in another region.
- Optional: a Fabric capacity (`-FabricSku F2` or larger; default `F8`; `''` skips Fabric, and the portfolio agent
  then reports Fabric IQ as unavailable).
- Optional: a **Web IQ API key** (`-WebIqApiKey`, or the `WEBIQ_API_KEY` environment variable). It goes only to Azure
  Resource Manager and lives in the Foundry connection. Without it, the agents have no web tools. Web IQ queries leave
  the Microsoft 365 and Azure compliance boundary, which is why the boundary guard sends only public topics.

**Your machine (Windows)**

- PowerShell 7.4+, Azure CLI, Python 3.12+ (3.14 recommended), Node.js 20+ and Git. No Docker: images build in Azure
  Container Registry. Terraform is installed into the deployment's profile if it's missing.
- The scripts use an isolated profile per deployment, `%LOCALAPPDATA%\advisor-team\<TEAM_PROFILE>`: its own Azure CLI
  sign-in, Terraform state, Python environment and work files. Your normal `az` sign-in is never read or changed. Use
  a different `-ProfileName` for each deployment on the same machine.
- For the Python scripts and the tests, create the profile's Python environment once:

  ```powershell
  . .\scripts\Use-IsolatedAzure.ps1
  python -m venv $env:UV_PROJECT_ENVIRONMENT
  & "$env:UV_PROJECT_ENVIRONMENT\Scripts\python.exe" -m pip install --pre -e ".[dev,ui]"
  ```

## Step 1: preview

```powershell
git clone https://github.com/dhangerkapil/microsoft-iq-durable-harness-foundry.git
cd microsoft-iq-durable-harness-foundry
$deploy = @{
    TenantId       = '<tenant guid>'
    SubscriptionId = '<subscription guid>'
    ResourceGroup  = 'rg-advisor-team'
    AnalystUpn     = '<the analyst user principal name>'
}
.\scripts\Deploy-AdvisorTeam.ps1 @deploy -Step Config, Platform -WhatIf
```

The Config step writes two git-ignored files, `.env.local` and `terraform/terraform.tfvars`, signs the isolated
profile in by device code (sign in as the advisor), looks up the advisor and the analyst, and prints the **story
week**. It also writes `web_boundary_terms`: the names of the fictional clients, the advisor and the analyst, which the
agents never send to Web IQ. `-WhatIf` then runs a Terraform plan without changing anything. `.env.example` explains
what `.env.local` holds: the settings the Config step writes, the optional ones, and what later steps record.

## Step 2: deploy

```powershell
.\scripts\Deploy-AdvisorTeam.ps1 @deploy                                                        # without Web IQ
.\scripts\Deploy-AdvisorTeam.ps1 @deploy -WebIqApiKey (Read-Host 'Web IQ API key' -AsSecureString)   # with Web IQ
```

The steps run in order, and each is safe to rerun alone with `-Step`:

| Step | What it does |
|---|---|
| Config | `.env.local` and `terraform.tfvars`; the advisor and the analyst; the story week; the Web IQ boundary terms |
| Guard | The preflight (`Test-Preflight.ps1`): every name this deployment will create in a shared resource is free or already its own. With shared resources, a baseline inventory (`Get-SharedInventory.ps1`) the first time |
| Platform | Terraform, through the plan gate (`Invoke-TerraformApply.ps1` and `check_plan.py`): everything in the Azure rows above |
| WorkIq | The Work IQ MCP connection and four toolboxes: `team-workiq` (read tools and `list_agents`), `team-workiq-chat` (Work IQ Chat over A2A), `team-governance` (the policy probe) and `team-drafts` (`create_entity`, behind the approval) |
| Fabric | With a capacity: the workspace, lakehouse, data and the data agent, the Fabric IQ connection and `team-fabric`. Skipped without one |
| FoundryIq | Builds the six firm-library PDFs, embeds and pushes them into the index, creates the knowledge source, the knowledge base, its project connection and `team-foundryiq` |
| WebIq | With a key: the Web IQ connection and `team-webiq`. Without one: skipped |
| Agent | Builds the image in the registry (`Publish-AgentImage.ps1`) and releases the hosted agent from it |
| Ui | Builds and deploys the app (`Publish-Ui.ps1`), grants its app registrations' admin consent and waits for it to answer |
| Quality | The rubric evaluator, an Insights monitor with daily scans, one optimizer run on the brief-writer prompt agent |
| Content | **Opt-in, not a default step.** The Microsoft 365 story: Friday's files and emails, the OneDrive PDFs, the Teams meeting to record. Never run it in a tenant another demo uses |
| Prewarm | `Start-DemoPrewarm.ps1`: warms the agent and clears the demo switches |
| Verify | With shared resources, the inventory diff (`Compare-SharedInventory.ps1`): nothing existing changed. It records this deployment's principals first |

Useful parameters:

| Parameter | Default | Notes |
|---|---|---|
| `-StoryMeetingDate` | the next Thursday | The client meeting that anchors the story week (a Thursday, `yyyy-MM-dd`) |
| `-AdvisorName` | your display name | The name the client and the analyst use in the story |
| `-Location` | `eastus2` | Region for the resource group and a new Foundry account |
| `-SpecialistLocation` | the main account | A separate account in this region holds only `team-specialist`, for quota |
| `-SearchLocation` | the region | Region for a new Azure AI Search service |
| `-FabricSku`, `-FabricLocation` | `F8`, the region | `''` skips Fabric |
| `-ExistingFoundryAccountId`, `-ExistingSearchServiceId`, `-ExistingFabricCapacityId` | `EXISTING_*` in `.env.local`, else none (create) | Reuse shared resources under new names ([ISOLATION.md](ISOLATION.md)). Recorded in `.env.local`, where the safety checks read them; a value that differs from `.env.local` stops the run, unless `-Force` |
| `-NameSuffix` | the profile name | Suffix for this deployment's tenant-wide names |
| `-ProfileName` | `demo` | The isolated profile's folder |
| `-AgentName` | `advisor-team` | The main hosted agent's name |
| `-TeamMode` | `team` | `solo` releases the single agent under the main name instead |
| `-ClientEmail` | `michael.scott@scott-family.example` | The fictional client's address in the story |
| `-WebIqApiKey` | none | Adds Web IQ; to add it later, rerun `-Step Config, WebIq, Agent` with the key |
| `-ExtraUserUpns` | none | More users who may call the agents through the app |
| `-Step` | all but Content | Run only the named steps |
| `-Force` | off | Rewrite `.env.local` and `terraform.tfvars` from the parameters |

## Step 3: the steps that need a person

The script prints these with your story week's dates.

1. **Work IQ for the tenant.** Usage-based billing for the Work IQ API (a Copilot Credits spending policy that
   includes it). Then Microsoft 365 admin center > **Agents** > **Tools** > **Work IQ MCP** > **Policies** > **Tenant
   Default**: Directory lookups off; Allow write actions and Allow create on; partial update, replace and delete off.
   Changes can take a while to apply. This repository never changes the policy itself.
2. **Consent, once per user and agent.** Open the app (`terraform -chdir=terraform output -raw ui_url`) as the
   advisor, send a prompt, click each **Authorize** link in the banner once, then **Retry**. Work IQ's request shows as
   the banner at the start of a turn; Fabric IQ and Work IQ Chat (A2A) are called inside a specialist agent, and their
   consent link can come back inside the answer instead (open it once, accept, ask again). Repeat as the analyst in a
   separate browser profile. Foundry IQ and Web IQ need no user consent. Consent is stored per agent: a candidate agent
   asks again; the review packs run on the same agent and need nothing more
   ([runbook](DEMO-RUNBOOK.md#one-time-setup)).
3. **Record the review** (your own tenant, after the Content step; about four minutes): on the story's Friday, the
   analyst opens the Teams meeting the Content step created, you join, record and read the script. The script with
   your dates is written to the profile's work folder; the template is
   [data/hydration/meeting-script.md](../data/hydration/meeting-script.md).
4. **The story's Monday.** Publish the IPS draft, then save the review as the lead agent's "last discussion":

   ```powershell
   .\data\hydration\Publish-DemoContent.ps1 -Stage Monday -ReplaceFiles 'IPS v2 DRAFT.docx'
   .\scripts\Invoke-Agent.ps1 -Prompt "Save Friday's Q4 portfolio review with Michael Scott as our last discussion."
   ```

5. **The quality loop** (optional, after consent, about an hour): [QUALITY-LOOP.md](QUALITY-LOOP.md).
6. **Demo day** (the story's Tuesday or Wednesday): minutes before, `.\data\hydration\Publish-DemoContent.ps1 -Stage
   Live` and `.\scripts\Start-DemoPrewarm.ps1 -IncludeFabric`; then follow [DEMO-RUNBOOK.md](DEMO-RUNBOOK.md).

## The story week

Everything in the story is dated relative to the client meeting on Thursday (`STORY_MEETING_DATE`): weekday names stay
right, and the months and the year follow the date. The facts in `demo/expectations.yaml` use the reference week (the
meeting on Thursday, October 22, 2026).

| Day | What exists | Created by |
|---|---|---|
| Thursday, a week before | Michael's agenda email for the review | Content (Friday stage) |
| Friday | The quarterly review, recorded at 10:30 PM ET; meeting notes; the signed IPS; the NVDA research note | You (recording); Content |
| Saturday | Michael's email: the closing moved, $400,000 due before it | Content |
| Monday 4:45 PM ET | The analyst's alert: NVDA up 11.2%, Michael over his 25% limit | Content |
| Monday | The IPS v2 draft | Monday stage |
| Tuesday or Wednesday | **The demo.** Michael's 529-plan email arrives during it | Live stage |
| Thursday 10:00 AM ET | The client meeting the agents prepare for | Content (calendar) |

To preview a week without publishing anything:

```powershell
.\data\hydration\Publish-DemoContent.ps1 -Stage Friday, Meeting, OneDrive, Monday, Live -Preview -StoryMeetingDate 2027-03-04
```

To move a deployment to a new week, rerun with the new date, then republish the data and the content:
`.\scripts\Deploy-AdvisorTeam.ps1 @deploy -StoryMeetingDate <thursday> -Step Config, Fabric, Content`. To rehearse
before the story's Monday alert, set a clock floor (`ADVISOR_CLOCK_NOT_BEFORE`); to present the story after its
Thursday without moving it, a clock ceiling (`ADVISOR_CLOCK_NOT_AFTER`) keeps the agents on the story's Wednesday
([runbook](DEMO-RUNBOOK.md#one-time-setup)).

## Verify

```powershell
. .\scripts\Use-IsolatedAzure.ps1 -RequireLogin
$py = Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe'
& $py scripts\rehearse.py --segments firm web --have webiq       # no consent needed: Foundry IQ, Web IQ, the guard
& $py scripts\rehearse.py --have workiq-consent fabric webiq      # every segment, after consent
& $py scripts\review_packs.py --crash-test --approve              # W3 review packs on advisor-team, with the crash test
.\scripts\Invoke-Agent.ps1 -Prompt "What does our firm require when a client's single stock goes above its limit, and before we release a large wire?"
& $py -m advisor_team.hosts.console "What does our firm require before we release a large wire?"   # the team locally
```

The rehearsal runner checks each step's calls, lanes, facts and time budget against `demo/expectations.yaml` and
writes a report to the profile's work folder. `Invoke-Agent.ps1` prints the answer, the tool activity and any consent
link. The console host runs the agents on your machine against the deployed resources, without the consent check.

## Test

```powershell
& $py -m pytest tests                       # more than 400 tests; the bash-based deployment tests need Linux
.\scripts\Test-AgentInAcr.ps1 -Tests tests  # the whole suite on Linux, in Azure Container Registry (nothing is pushed)
.\scripts\Test-AgentInAcr.ps1 -Ui           # the app: type check and its tests
pwsh -NoProfile -File scripts\Test-DemoDocs.ps1   # demo prompts in the script match the expectations; no forbidden terms
terraform fmt -check -recursive terraform
```

`Test-AgentInAcr.ps1` without `-Tests` runs `tests/test_advisor.py` only.

## Operate

- **The Fabric capacity** stays running while you use the solution; Fabric IQ needs it. Some subscriptions pause
  Fabric capacities on a schedule, for example overnight: `.\scripts\Start-DemoPrewarm.ps1 -IncludeFabric` (or
  `.\scripts\Set-FabricCapacityState.ps1 -State Active`) resumes it in about a minute. To save cost, you can pause a
  capacity you own with `-State Paused`; resume it an hour before a rehearsal or demo. A shared capacity is paused or
  resumed by its owner, not by you.
- **Release agent changes:** `.\scripts\Publish-AgentImage.ps1` builds the image in the registry, pins its digest and
  applies it: the hosted agent gets a new version.
- **Release app changes:** `.\scripts\Publish-Ui.ps1`.
- **Try changes safely:** `.\scripts\Publish-CandidateAgent.ps1` releases `advisor-team-candidate` next to the live
  agent, with its own state stores ([QUALITY-LOOP.md](QUALITY-LOOP.md)).
- **An extra app for another agent** (a candidate, or the single-agent build): `.\scripts\Publish-UiInstance.ps1
  -AgentName <agent>`; `-Remove` deletes it.
- **Restart a session** as idle eviction would: `.\scripts\Restart-AgentSession.ps1` (`-List` to see the sessions).
- **Rerun any step:** `.\scripts\Deploy-AdvisorTeam.ps1 @deploy -Step <name>`.

## The morning note (add-on)

Off by default ([ARCHITECTURE.md](ARCHITECTURE.md#the-morning-note-o1)). To add it, set `enable_morning_note = true`
in `terraform/terraform.tfvars`, optionally with `morning_note_watch_list` (default `NVDA,MSFT,AAPL`) and
`morning_note_schedule` (NCRONTAB in UTC, default `0 0 11 * * 1-5`; empty for on demand only). Then:

```powershell
.\scripts\Deploy-AdvisorTeam.ps1 @deploy -Step Platform   # the resources and role assignments
.\scripts\Publish-MorningNote.ps1                         # the code; prints the functions the host registered
.\scripts\Invoke-MorningNote.ps1 -Tickers NVDA            # runs a note, shows the draft and asks to publish it
```

`Invoke-MorningNote.ps1` takes `-Approve` or `-Reject` to answer without asking, `-Reviewer` for the name recorded
with the note, and `-InstanceId` to pick up a run that is waiting. Each run and step shows in the Durable Task
Scheduler dashboard (https://dashboard.durabletask.io/, with the scheduler's endpoint and the task hub
`advisor-team`); the script prints the endpoint. Published notes stay in the firm library: one per day, and running
the note again the same day replaces that day's note.

## Microsoft Purview (add-on)

Off by default; the demo uses it (steps 2.6 and 6.5). It needs Microsoft Purview's pay-as-you-go billing for AI apps on
a subscription in the tenant, and a compliance administrator for the DLP policy. Set `enable_purview = true` in
`terraform/terraform.tfvars`, then:

```powershell
.\scripts\Deploy-AdvisorTeam.ps1 @deploy -Step Agent                      # TEAM_PURVIEW=on; three Graph roles for the agent identity
.\scripts\Set-PurviewDlpForAgent.ps1 -UserPrincipalName <compliance admin>  # the DLP policy: a U.S. SSN in a prompt is blocked, with an alert
.\scripts\Invoke-Agent.ps1 -Prompt "Michael Scott's Social Security number is 524-77-3612. Add it to his file."
```

A new policy can take up to an hour to apply; then the last command returns Purview's block message, and rehearsals check
it when `--have` includes `purview`. Every Work IQ call
the agents make shows in Microsoft Defender advanced hunting ([the walkthrough's Appendix A](DEMO-WALKTHROUGH.md#appendix-a-defender-advanced-hunting-queries)),
and `.\scripts\Get-WorkIqAuditTrail.ps1 -Minutes 60 -UserPrincipalName <user>` lines those logs up with the agents'
traces (Microsoft Graph PowerShell, ThreatHunting.Read.All). `Set-PurviewDlpForAgent.ps1 -Remove` removes the policy.

## Troubleshooting

| Symptom | Fix |
|---|---|
| The app shows a consent banner, or an answer carries a consent link | Open it as that user and accept; links expire after about 10 minutes, and **Retry** gets fresh ones |
| A token or Continuous Access Evaluation error after a policy change | `.\scripts\Connect-IsolatedAzure.ps1` (it signs out first), then rerun the failed step |
| The Platform step stops: "the plan changes or destroys existing resources" | Review the plan; rerun the step when you agree. A plan that touches shared resources beyond the approved additions is refused outright ([ISOLATION.md](ISOLATION.md)) |
| The Guard step fails on a name | That name exists in a shared resource and isn't this deployment's: pick another `-NameSuffix`, or remove the leftover |
| Model quota errors | Lower the capacities in `terraform.tfvars` (`team_lead_capacity`, `team_specialist_capacity`), use `-SpecialistLocation`, or switch a deployment's SKU (`team_lead_sku`) |
| Azure AI Search can't be created (no capacity or semantic ranker) | Rerun with `-SearchLocation <region>` and `-Step Config, Platform -Force` |
| Fabric steps fail or time out | Resume the capacity: `.\scripts\Set-FabricCapacityState.ps1 -State Active` |
| The FoundryIq step can't upload documents | Wait a few minutes after Platform for the Search role assignments to apply, then rerun `-Step FoundryIq` |
| The agents say the firm library or the web was not checked | Check the toolbox: `.\scripts\Test-ToolboxTool.ps1 -Toolbox team-foundryiq` (or `team-webiq`); for Web IQ, rotate the key with `.\scripts\Set-WebIqConnection.ps1 -Recreate` |
| A web question comes back `WEB_QUERY_BLOCKED` or `WEB_TASK_BLOCKED` | By design: the query or the task named a client or staff member, or carried an email address, an amount or an account-like number |
| A connection "already exists and can only be updated by the workspace that created it" | Connection names are unique across a Foundry account: in an existing account the scripts add this deployment's suffix; rename any you created by hand |
| New mail or files aren't found | Work IQ indexes new mail within minutes and new files can take longer; publish content a day ahead |
| The Content step can't create the Teams meeting | The organizer's calendar must allow Teams meetings: use an analyst whose mailbox can host them |
| The morning note's host logs 403 "Server failed to authenticate the request" | A deploy added key-based `AzureWebJobsStorage` or `DEPLOYMENT_STORAGE_CONNECTION_STRING` settings, which can't work without shared keys: rerun `.\scripts\Publish-MorningNote.ps1`, which removes them |
| A morning note run fails with "The draft is still too long to approve" | The writer agent couldn't fit the note in the approval request (16 KB): run it with fewer tickers (`-Tickers`, or `morning_note_watch_list`) |
| A morning note step fails with a rate limit (429) | Each model call is retried three times with growing waits; if it still fails, lower the watch list or raise `team_lead_capacity` |

## Remove everything

```powershell
.\scripts\Remove-AdvisorTeam.ps1 -WhatIf          # what would be removed
.\scripts\Remove-AdvisorTeam.ps1 -PurgeFoundry    # remove; asks before destroying
```

It deletes the Fabric workspace, the agents Terraform doesn't manage (the candidate), extra app instances, this
deployment's script-created app registrations, and everything Terraform created, including a Search service it created
with the Foundry IQ items in it. An existing Foundry account, Search service or Fabric capacity is kept: from them, only
this deployment's project (with its connections, toolboxes and agents), guardrail policy, role assignments and Fabric
workspace go. In an existing Search service, the script deletes only the index, knowledge source and knowledge base
this deployment added (`team-firm-library-index`, with any published morning notes, `team-firm-library-ks` and
`team-firm-knowledge`). The tenant's Work IQ service principal is kept. `-PurgeFoundry` purges a soft-deleted Foundry
account this deployment created, so its name is free at once. `-KeepLocalConfig` keeps `.env.local` and the `.tfvars`
files.

The Microsoft 365 story stays in the tenant. Delete it by hand if you keep using the tenant: the emails from Michael
Scott and the analyst's alert, and the "Year-end planning - Michael Scott" event, in the advisor's mailbox; the
recorded review meeting in the analyst's calendar; the `Client Files/Michael Scott` and `Research` folders on the root
SharePoint site; and `Client Files/Michael Scott` in the advisor's OneDrive.
