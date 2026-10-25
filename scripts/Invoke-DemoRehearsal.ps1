<#
.SYNOPSIS
    Rehearses the demo walkthrough against the deployed agent: sends each segment's prompts as the
    signed-in demo user and prints the elapsed time, the tool calls and the start of each answer.

.DESCRIPTION
    Uses Invoke-Agent.ps1, so it runs as the same user as the demo app and needs the same one-time
    consents. Each segment starts a new conversation; prompts inside a segment continue it. Full JSON
    responses are saved in the isolated work folder as rehearsal-<segment>-<n>.json.
    The durability segment arms the crash switch, so its second prompt is expected to fail (the agent
    process stops on purpose). The interrupted conversation stays locked by design, so the next prompts
    start a new conversation (marked "NEW:") and resume the run from the state store.
    The draft segment asks for a follow-up email, then answers the approval request: it approves (a real
    draft lands in the advisor's Outlook Drafts; delete it before the session) or, with -RejectDraft, rejects.
    The firm, web, fouriqs, eachiq and workiq segments need an agent with Foundry IQ, Web IQ and Work IQ Chat (A2A)
    tools. The demo's order for such an agent: prep (the brief, all four IQs), then fouriqs (one question that needs
    all four IQs at once) and eachiq (one question per IQ, each in a new conversation, Work IQ first with every Work
    IQ feature), then web (public news and the boundary guard), meeting, chart, graph and workiq.

.EXAMPLE
    .\scripts\Invoke-DemoRehearsal.ps1
    .\scripts\Invoke-DemoRehearsal.ps1 -Segment prep, chart
    .\scripts\Invoke-DemoRehearsal.ps1 -Segment draft -RejectDraft
    .\scripts\Invoke-DemoRehearsal.ps1 -AgentName advisor-team -Segment prep, fouriqs, eachiq, web, meeting, chart, graph, workiq
#>
[CmdletBinding()]
param(
    [ValidateSet('prep', 'fouriqs', 'eachiq', 'meeting', 'chart', 'graph', 'governance', 'draft', 'durability', 'changed', 'fabric', 'firm', 'web', 'workiq')]
    [string[]]$Segment = @('prep', 'meeting', 'chart', 'graph', 'governance', 'draft', 'durability'),
    [int]$TimeoutSec = 600,
    [switch]$RejectDraft,
    # Another hosted agent in the project (such as a candidate); default: the Terraform-managed agent.
    [string]$AgentName,
    # Saved turns are named <prefix>-<segment>-<n> in the work folder, for evals\Invoke-FoundryQuality.ps1 dataset.
    [ValidatePattern('^[a-z0-9-]+$')][string]$SavePrefix = 'rehearsal'
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
. "$PSScriptRoot\StoryDates.ps1"
$prompts = [ordered]@{
    prep       = @("Prepare me for my Thursday meeting with Michael Scott.")
    # The demo's second question: one business question that needs all four Microsoft IQs (the app's second suggestion).
    fouriqs    = @("Michael Scott needs `$400,000 for his home closing. How could we fund it, and what has to happen before the money moves?")
    # One IQ at a time (the app's "One IQ at a time" row): each question is in the advisor's words and never names a
    # source; the agent picks the IQ from what it's about. Each starts a new conversation.
    eachiq     = @(
        "NEW:Bring me up to speed on Michael Scott: when we next meet, what he has asked us for, what we agreed at the last review, what the team is saying, how the new policy draft differs from the signed one, what the NVIDIA chart in his portfolio overview shows, and what is still open.",
        "NEW:How has Michael Scott's NVIDIA position moved over the last month, and where do NVIDIA, his technology exposure and his cash stand against his limits today?",
        "NEW:What does our firm require when a client's single stock goes above its limit, and before we release a large wire?",
        "NEW:What's moving NVIDIA this week, and where is the stock trading now?")
    meeting    = @("In Friday's Q4 portfolio review, what exactly did Michael say about his tax budget and his house? Quote the meeting transcript, and list the action items with their owners and whether each has been done since.")
    chart      = @(
        "Find the chart of Michael Scott's NVDA weight against its limit. What were the high and the low since September 1, how close did it come to the limit, and where did it end?",
        "Since that chart, where does Michael's NVDA position stand now? Check the analyst's latest email against the portfolio data in Fabric, tell me whether they agree, what Michael and the team said about trimming it, and what a trim to the limit would mean.")
    graph      = @("How is Work IQ different from just calling the Graph API? Show me side by side on this question: where is the chart of Michael Scott's NVDA position against its limit, and what did Michael ask about it by email?")
    governance = @('What is this agent allowed to do with Work IQ? Test it.')
    draft      = @(
        'Draft a short follow-up email to Michael Scott confirming our Thursday meeting and listing the three agenda topics from his latest email.',
        'DECIDE')
    durability = @(
        'Arm the crash test for the durable meeting pack.',
        'Run the durable meeting pack for Michael Scott.',
        'NEW:What is stored durably for Michael Scott right now?',
        'Resume the durable meeting pack for Michael Scott.')
    changed    = @('What changed since my last discussion with Michael Scott?')
    fabric     = @("Where do Michael Scott's NVIDIA weight, his technology exposure and his cash stand right now, and as of when?")
    firm       = @('Michael wants to wire $400,000 to his title company for the closing. What does our firm policy require before we send it?')
    web        = @(
        'What is the public news on NVIDIA this week? Three bullets with links.',
        "Search the web for Michael Scott's NVDA position and his stake.")
    workiq     = @(
        "Compare Michael Scott's signed IPS with the IPS v2 draft. Which terms changed, and which version is in force?",
        'Delegate to Work IQ: what did Michael Scott ask me for in his recent emails, and what is still open?',
        'Which Copilot agents can you work with through Work IQ?')
}

$target = if ($AgentName) { @{ AgentName = $AgentName } } else { @{} }
$summary = @()
foreach ($name in $Segment) {
    $index = 0
    $fresh = $true
    $approvalId = $null
    foreach ($prompt in @($prompts[$name] | ForEach-Object { Convert-StoryText $_ })) {
        $index++
        if ($prompt.StartsWith('NEW:')) { $prompt = $prompt.Substring(4); $fresh = $true }
        $saveAs = "$SavePrefix-$name-$index"
        if ($prompt -eq 'DECIDE') {
            if (-not $approvalId) { Write-Warning 'No approval request to answer.'; continue }
            $decision = if ($RejectDraft) { @{ Reject = $approvalId } } else { @{ Approve = $approvalId } }
            Write-Output ''
            Write-Output "=== $name $index : $(if ($RejectDraft) { 'reject' } else { 'approve' }) $approvalId"
            $output = & "$PSScriptRoot\Invoke-Agent.ps1" @decision @target -TimeoutSec $TimeoutSec -SaveAs $saveAs 2>&1 |
                ForEach-Object { "$_" }
        } else {
            Write-Output ''
            Write-Output "=== $name $index : $prompt"
            $output = & "$PSScriptRoot\Invoke-Agent.ps1" -Prompt $prompt @target -TimeoutSec $TimeoutSec -SaveAs $saveAs -Continue:(-not $fresh) 2>&1 |
                ForEach-Object { "$_" }
        }
        $fresh = $false
        $approval = $output | Select-String -Pattern 'APPROVAL NEEDED: rerun with -Approve (\S+)' | Select-Object -First 1
        $approvalId = if ($approval) { $approval.Matches[0].Groups[1].Value } else { $null }
        $status = ($output | Select-String -Pattern 'status=(\w+) elapsed=(\d+)s' | Select-Object -First 1)
        $failed = $output | Select-String -Pattern '^request failed after (\d+)s' | Select-Object -First 1
        $tools = @($output | Where-Object { $_ -match '^\[(function_call|mcp_call|custom_tool_call|mcp_approval_request)\]' } |
            ForEach-Object { ($_ -split ' ')[1] })
        $consent = [bool]($output | Select-String -Pattern 'CONSENT LINK' -Quiet)
        $answerAt = [array]::IndexOf($output, '--- answer ---')
        $answer = if ($answerAt -ge 0) { ($output[($answerAt + 1)..($output.Count - 1)] -join ' ') } else { '' }
        $summary += [pscustomobject]@{
            segment = "$name $index"
            result  = if ($failed) { "failed after $($failed.Matches[0].Groups[1].Value)s" } elseif ($status) { "$($status.Matches[0].Groups[1].Value) in $($status.Matches[0].Groups[2].Value)s" } else { 'no response' }
            tools   = ($tools | Group-Object | ForEach-Object { if ($_.Count -gt 1) { "$($_.Name) x$($_.Count)" } else { $_.Name } }) -join ', '
            consent = $consent
        }
        if ($failed) { $output | Select-Object -First 6 | Write-Output } else { Write-Output ($answer.Substring(0, [Math]::Min(700, $answer.Length))) }
        if ($consent) { Write-Warning 'A consent link is pending: complete it in the demo app, then rerun.'; break }
        if ($failed -and $name -eq 'durability' -and $index -eq 2) {
            Write-Output 'Expected: the crash switch stopped the agent process. Waiting 30 s, then resuming from a new conversation.'
            Start-Sleep -Seconds 30
        }
    }
}
Write-Output ''
Write-Output 'Summary:'
$summary | ForEach-Object { '{0,-14} {1,-22} {2}{3}' -f $_.segment, $_.result, $_.tools, $(if ($_.consent) { '  [consent needed]' } else { '' }) } | Write-Output

# The chart step: Work IQ (ask) should read the chart; read_chart is the harness's fallback reader. Both run inside the
# client-context specialist, so their names are in the team's records in the saved turn, not in the lead's own calls.
function Get-TeamCalls([string]$Path) {
    if (-not (Test-Path $Path)) { return @() }
    $saved = Get-Content $Path -Raw | ConvertFrom-Json -Depth 60
    foreach ($item in @($saved.output)) {
        if ($item.type -ne 'function_call_output' -or $item.output -isnot [string]) { continue }
        $value = $item.output
        for ($i = 0; $i -lt 3; $i++) {
            try { $decoded = $value | ConvertFrom-Json -Depth 60 -ErrorAction Stop } catch { break }
            if ($null -eq $decoded -or $decoded -isnot [pscustomobject]) { break }
            $keys = $decoded.PSObject.Properties.Name
            if ($keys -contains '__team_activity__') { $decoded.__team_activity__.calls | ForEach-Object { $_.tool }; break }
            if ($keys -notcontains '__workiq_tool_execution__') { break }
            $value = if ($decoded.output -is [string]) { $decoded.output } else { $decoded.output | ConvertTo-Json -Depth 60 -Compress }
        }
    }
}
$chart = $summary | Where-Object segment -eq 'chart 1' | Select-Object -First 1
if ($chart) {
    $chartTools = "$($chart.tools) $((Get-TeamCalls (Join-Path $env:TEAM_WORK_DIR "$SavePrefix-chart-1.json")) -join ', ')"
    Write-Output ''
    if ($chartTools -match 'read_chart') {
        Write-Output 'Chart check: the harness fallback (read_chart) read the chart: Work IQ ask did not return its values this time. Run the chart segment again; if it repeats, look at the workiq___ask output in the saved JSON.'
    } elseif ($chartTools -match 'workiq___ask') {
        Write-Output 'Chart check: Work IQ (workiq___ask) read the chart, and inspect_document verified the page. As intended.'
    } else {
        Write-Output 'Chart check: neither workiq___ask nor read_chart ran: the answer has no chart values. Run the chart segment again.'
    }
}
