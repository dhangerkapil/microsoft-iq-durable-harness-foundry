<#
.SYNOPSIS
    Every Work IQ call the agents made, end to end: the agents' own record (Foundry traces) next to the logs a security
    team already queries in Microsoft Defender (Microsoft Graph activity logs and the Microsoft Purview audit), and what
    the Purview DLP policy blocked.

.DESCRIPTION
    For a time window (default: the last 60 minutes), optionally for one user:

    1. The agents in Defender's security inventory (AgentsInfo).
    2. Who called the agents (InvokeAgent: the Agent 365 audit record of every call to a hosted agent).
    3. The agents' Work IQ calls from their Foundry traces (what the app's X-ray shows), grouped into bursts, each with
       the Microsoft Graph calls Work IQ made for that user meanwhile and, for Work IQ's ask tool and Work IQ Chat (A2A),
       the files, emails and meetings that the Purview audit says Work IQ read.
    4. Work IQ's Microsoft Graph calls on the user's behalf (MicrosoftGraphActivityLogs).
    5. Work IQ in the Purview audit: Microsoft 365 Copilot interactions (CopilotInteraction) with the resources read and
       whether a jailbreak or a cross-prompt injection attack (XPIA) was detected, and the mail and files accessed under
       the user's name.
    6. The Purview DLP policy on the agents' prompts: rule matches (DLPRuleMatch) and the alerts they raised.

    Everything is read through Microsoft Defender advanced hunting (Microsoft Graph runHuntingQuery,
    ThreatHunting.Read.All), except the traces, which come from the Application Insights that Foundry traces to (read
    with the isolated Azure CLI profile). The Purview audit records reach Defender's CloudAppEvents within minutes and
    stay in Purview Audit (activities InvokeAgent, CopilotInteraction, DLPRuleMatch) for its longer retention.
    MicrosoftGraphActivityLogs needs an Entra diagnostic setting that sends Graph activity logs to the Microsoft Sentinel
    workspace onboarded to the Defender portal; CloudAppEvents needs the Microsoft 365 connector in Defender for Cloud
    Apps.

.EXAMPLE
    .\scripts\Get-WorkIqAuditTrail.ps1 -Minutes 30 -UserPrincipalName advisor@contoso.onmicrosoft.com
    .\scripts\Get-WorkIqAuditTrail.ps1 -Minutes 120 -OutFile workiq-audit.md
#>
[CmdletBinding()]
param(
    [ValidateRange(5, 10080)][int]$Minutes = 60,
    [ValidatePattern('^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$')][string]$UserPrincipalName,
    # A Microsoft Graph token with ThreatHunting.Read.All, for automation; otherwise Connect-MgGraph signs you in.
    [string]$AccessToken,
    [string]$OutFile
)

$ErrorActionPreference = 'Stop'
$workIq = 'fdcc1f02-fc51-4226-8753-f668596af7f7'  # Work IQ's application ID, the same in every tenant
$report = [System.Collections.Generic.List[string]]::new()

# ---- The deployment: the agents, their Entra agent IDs, and the workspace behind the traces
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
$tf = Join-Path (Split-Path $PSScriptRoot -Parent) 'terraform'
function Get-TfOutput([string]$Name) { (terraform "-chdir=$tf" output -raw -no-color $Name | Out-String).Trim() }
$principals = terraform "-chdir=$tf" output -json -no-color principals | Out-String | ConvertFrom-Json
$agentNames = @{}
if ($principals.agent) { $agentNames[$principals.agent] = Get-TfOutput agent_name }
if (-not $agentNames.Count) { throw 'The Terraform outputs list no deployed agent.' }
$kqlIds = ($agentNames.Keys | ForEach-Object { "'$_'" }) -join ', '
$kqlNames = ($agentNames.Values | ForEach-Object { "'$_'" }) -join ', '
$appInsights = Get-TfOutput trace_application_insights_id
$workspace = (az resource show --ids $appInsights --query properties.WorkspaceResourceId -o tsv).Trim()
$workspaceId = (az resource show --ids $workspace --query properties.customerId -o tsv).Trim()

$upnById = @{}
function Get-Upn([string]$Id) {
    if (-not $Id) { return '' }
    if (-not $upnById.ContainsKey($Id)) {
        $upn = az ad user show --id $Id --query userPrincipalName -o tsv 2>$null
        $upnById[$Id] = if ($upn) { "$upn".Trim() } else { $Id }
    }
    $upnById[$Id]
}
$userId = $null
if ($UserPrincipalName) {
    $userId = "$(az ad user show --id $UserPrincipalName --query id -o tsv 2>$null)".Trim()
    if (-not $userId) { throw "Microsoft Entra ID has no user $UserPrincipalName." }
    $upnById[$userId] = $UserPrincipalName
}

# ---- Microsoft Defender advanced hunting, through Microsoft Graph
if ($AccessToken) {
    $graphHeaders = @{ Authorization = "Bearer $AccessToken" }
    function Invoke-Hunting([string]$Query) {
        $body = @{ Query = $Query } | ConvertTo-Json -Compress
        @((Invoke-RestMethod -Method Post -Uri 'https://graph.microsoft.com/v1.0/security/runHuntingQuery' `
                    -Headers $graphHeaders -ContentType 'application/json' -Body $body).results)
    }
} else {
    if (-not (Get-Module -ListAvailable Microsoft.Graph.Authentication)) {
        throw 'Install the Microsoft Graph module first: Install-Module Microsoft.Graph.Authentication -Scope CurrentUser'
    }
    Connect-MgGraph -Scopes ThreatHunting.Read.All -NoWelcome
    function Invoke-Hunting([string]$Query) {
        $body = @{ Query = $Query } | ConvertTo-Json -Compress
        @((Invoke-MgGraphRequest -Method POST -Uri 'https://graph.microsoft.com/v1.0/security/runHuntingQuery' `
                    -Body $body -ContentType 'application/json' -OutputType PSObject).results)
    }
}

function Invoke-LogAnalytics([string]$Query) {
    $body = New-TemporaryFile
    try {
        @{ query = $Query } | ConvertTo-Json -Compress | Set-Content -Path $body -Encoding utf8
        $result = az rest --method post --url "https://api.loganalytics.io/v1/workspaces/$workspaceId/query" `
            --resource 'https://api.loganalytics.io' --body "@$body" -o json | Out-String | ConvertFrom-Json
    } finally { Remove-Item $body -ErrorAction SilentlyContinue }
    $table = $result.tables[0]
    foreach ($row in $table.rows) {
        $item = [ordered]@{}
        for ($i = 0; $i -lt $table.columns.Count; $i++) { $item[$table.columns[$i].name] = $row[$i] }
        [pscustomobject]$item
    }
}

function ConvertTo-Utc($Value) {
    if ($Value -is [datetime]) {
        if ($Value.Kind -eq [DateTimeKind]::Unspecified) { return [datetime]::SpecifyKind($Value, [DateTimeKind]::Utc) }
        return $Value.ToUniversalTime()
    }
    [datetimeoffset]::Parse([string]$Value, [cultureinfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal).UtcDateTime
}
# A Graph path without its host, query and opaque IDs: /v1.0/drives/{id}/items/{id}/children.
function Format-GraphPath([string]$Uri) {
    $path = [uri]::UnescapeDataString(([uri]$Uri).AbsolutePath)
    ($path -split '/' | ForEach-Object {
            if ($_ -match '^b!' -or $_ -match '^[0-9a-fA-F-]{36}$' -or ($_.Length -gt 24 -and $_ -match '\d' -and $_ -notmatch '\s')) { '{id}' } else { $_ }
        }) -join '/'
}
# A readable name for a resource in a Copilot audit record: a file name, an email, a meeting, a Teams message or a
# search; opaque IDs are skipped.
function Format-Resource([string]$Type, [string]$Url) {
    if ($Url -notmatch '^https?://') { return $null }
    $u = [uri]$Url
    $query = [uri]::UnescapeDataString($u.Query.TrimStart('?').Replace('+', ' '))
    if ($u.Host -like 'outlook.*') { return 'an email' }
    if ($u.Host -eq 'teams.microsoft.com') { return $(if ($u.AbsolutePath -like '/l/meeting/*') { 'a meeting' } else { 'a Teams message' }) }
    if ($query -match '(?:^|&)file=([^&]+)') { return $Matches[1] }
    if ($u.AbsolutePath -like '*/search*' -and $query -match '(?:^|&)q=([^&]+)') { return "a search for $($Matches[1])" }
    $name = [uri]::UnescapeDataString($u.Segments[-1])
    if ($name -like '*.aspx') { return $(if ($Type -match '^\w{2,5}$') { "a .$Type file" } else { 'a list item' }) }
    if ($name -and $name -ne '/') { $name } else { $u.Host }
}
function Format-Time($Value) { (ConvertTo-Utc $Value).ToString('HH:mm:ss') }
# Prints a section as a table (or with -Render, row by row) and adds it to the Markdown report as a table.
function Add-Section([string]$Title, $Rows, [string[]]$Columns, [string]$Empty, [scriptblock]$Render) {
    Write-Host "`n== $Title" -ForegroundColor Cyan
    $report.Add("`n## $Title`n")
    if (-not $Rows -or @($Rows).Count -eq 0) {
        Write-Host "   $Empty"; $report.Add($Empty); return
    }
    if ($Render) { foreach ($r in $Rows) { & $Render $r } }
    else { $Rows | Select-Object $Columns | Format-Table -AutoSize -Wrap | Out-String -Width 240 | Write-Host }
    $report.Add('| ' + ($Columns -join ' | ') + ' |'); $report.Add('|' + ('---|' * $Columns.Count))
    foreach ($r in $Rows) { $report.Add('| ' + (($Columns | ForEach-Object { "$($r.$_)" -replace '\|', '/' }) -join ' | ') + ' |') }
}

$window = "ago($($Minutes)m)"
$purviewUser = if ($userId) { "| where tostring(RawEventData.UserKey) == '$userId'" } else { '' }
$heading = "Work IQ audit trail: the last $Minutes minutes, UTC$(if ($UserPrincipalName) { ", $UserPrincipalName" })"
Write-Host $heading -ForegroundColor Green
$report.Add("# $heading")

# 1. The agents in Defender's inventory
$inventory = Invoke-Hunting @"
AgentsInfo
| where Name in ($kqlNames) or EntraAgentID in ($kqlIds)
| summarize arg_max(Timestamp, *) by AgentId
| project Name, Platform, EntraAgentID, PublishedStatus
"@
Add-Section 'The agents in Microsoft Defender''s inventory (AgentsInfo)' $inventory @('Name', 'Platform', 'EntraAgentID', 'PublishedStatus') `
    'Not listed yet: Defender can take a day to list a new agent.'

# 2. Who called the agents
$invocations = Invoke-Hunting @"
CloudAppEvents
| where Timestamp > $window
| where ActionType == 'InvokeAgent'
| where tostring(RawEventData.TargetAgentId) in ($kqlIds)
$purviewUser
| summarize Calls = count(), FirstSeen = min(Timestamp), LastSeen = max(Timestamp)
    by User = tostring(RawEventData.UserId), AgentId = tostring(RawEventData.TargetAgentId), Source = tostring(RawEventData.InvokeSource)
"@
foreach ($i in $invocations) {
    $i | Add-Member -NotePropertyName Agent -NotePropertyValue $agentNames[$i.AgentId] -Force
    $i | Add-Member -NotePropertyName From -NotePropertyValue (Format-Time $i.FirstSeen) -Force
    $i | Add-Member -NotePropertyName To -NotePropertyValue (Format-Time $i.LastSeen) -Force
}
Add-Section 'Who called the agents (InvokeAgent, the Agent 365 audit)' $invocations @('User', 'Agent', 'Source', 'Calls', 'From', 'To') `
    'No calls in the window yet: audit records reach Defender within minutes.'

# Work IQ's Graph calls and its audited Copilot interactions, read before the agents' calls are matched to them
$graphUser = if ($userId) { "| where UserId == '$userId'" } else { '' }
$graph = Invoke-Hunting @"
MicrosoftGraphActivityLogs
| where TimeGenerated > $window
| where AppId == '$workIq'
$graphUser
| project Time = TimeGenerated, UserId, Method = RequestMethod, Uri = RequestUri, Status = ResponseStatusCode
| order by Time asc
| take 5000
"@
foreach ($g in $graph) {
    $g | Add-Member -NotePropertyName At -NotePropertyValue (ConvertTo-Utc $g.Time) -Force
    $g | Add-Member -NotePropertyName Path -NotePropertyValue (Format-GraphPath $g.Uri) -Force
}
$copilot = Invoke-Hunting @"
CloudAppEvents
| where Timestamp > $window
| where ActionType == 'CopilotInteraction'
| where tostring(RawEventData.AppIdentity) == 'Copilot.M365Copilot.Apps' and tostring(RawEventData.CopilotEventData.AppHost) == 'ThirdPartyCopilot'
$purviewUser
| extend Accessed = RawEventData.CopilotEventData.AccessedResources, Messages = RawEventData.CopilotEventData.Messages
| mv-apply r = Accessed to typeof(string) on (extend j = parse_json(r) | summarize Resources = make_set(strcat(tostring(j.Type), ' ', tostring(j.SiteUrl)), 40), Xpia = countif(tobool(j.XPIADetected)))
| mv-apply m = Messages to typeof(string) on (extend j = parse_json(m) | summarize Jailbreak = countif(tobool(j.JailbreakDetected)))
| project Time = Timestamp, UserKey = tostring(RawEventData.UserKey), User = tostring(RawEventData.UserId), Resources, Xpia, Jailbreak
| order by Time asc
| take 1000
"@
foreach ($p in $copilot) {
    $upnById[$p.UserKey] = $p.User
    $p | Add-Member -NotePropertyName At -NotePropertyValue (ConvertTo-Utc $p.Time) -Force
    $read = @($p.Resources | ForEach-Object { $type, $url = "$_" -split ' ', 2; Format-Resource $type $url } | Where-Object { $_ } | Select-Object -Unique)
    $p | Add-Member -NotePropertyName Read -NotePropertyValue $read -Force
}

# 3. The agents' Work IQ calls (Foundry traces): parallel calls form a burst, matched to what the logs recorded meanwhile
$traceUser = if ($userId) { "| where User == '$userId'" } else { '' }
$calls = @(Invoke-LogAnalytics @"
AppDependencies
| where TimeGenerated > $window
| where AppRoleName in ($kqlNames) and Name startswith 'execute_tool' and (Name contains 'workiq' or Name contains 'work_iq')
| extend User = tostring(Properties['user.id']), Tool = replace_string(Name, 'execute_tool ', '')
$traceUser
| project Start = TimeGenerated, DurationMs, Agent = AppRoleName, Tool, User
| order by Start asc
| take 2000
"@)
$bursts = [System.Collections.Generic.List[object]]::new()
foreach ($c in $calls) {
    $start = ConvertTo-Utc $c.Start
    $end = $start.AddMilliseconds([double]$c.DurationMs)
    $last = if ($bursts.Count) { $bursts[$bursts.Count - 1] } else { $null }
    if ($last -and $last.Agent -eq $c.Agent -and $last.User -eq $c.User -and $start -le $last.End.AddSeconds(2)) {
        $last.Tools.Add($c.Tool); if ($end -gt $last.End) { $last.End = $end }
    } else {
        $bursts.Add([pscustomobject]@{ Agent = $c.Agent; User = $c.User; Start = $start; End = $end
                Tools = [System.Collections.Generic.List[string]]::new([string[]]@($c.Tool)) })
    }
}
$rows = foreach ($b in $bursts) {
    $during = @($graph | Where-Object { $_.UserId -eq $b.User -and $_.At -ge $b.Start.AddSeconds(-2) -and $_.At -le $b.End.AddSeconds(5) })
    $audited = @($copilot | Where-Object { $_.UserKey -eq $b.User -and $_.At -ge $b.Start -and $_.At -le $b.End.AddSeconds(15) })
    $paths = @($during | ForEach-Object { "$($_.Method) $($_.Path)" } | Select-Object -Unique)
    $read = @($audited | ForEach-Object { $_.Read } | Select-Object -Unique)
    [pscustomobject]@{
        Time         = $b.Start.ToString('HH:mm:ss')
        Agent        = $b.Agent
        User         = Get-Upn $b.User
        WorkIqCalls  = ($b.Tools | Group-Object | Sort-Object Count -Descending | ForEach-Object { "$($_.Name) x$($_.Count)" }) -join ', '
        Seconds      = [math]::Round(($b.End - $b.Start).TotalSeconds, 1)
        GraphCalls   = "$($during.Count)$(if ($paths) { ': ' + (($paths | Select-Object -First 6) -join '; ') + $(if ($paths.Count -gt 6) { '; ...' }) })"
        PurviewAudit = if ($audited) { "$($audited.Count) Copilot interaction(s): $(($read | Select-Object -First 6) -join '; ')" } else { '' }
    }
}
Add-Section 'The agents'' Work IQ calls (Foundry traces), with what Work IQ did for the user meanwhile' $rows `
    @('Time', 'Agent', 'User', 'WorkIqCalls', 'Seconds', 'GraphCalls', 'PurviewAudit') 'No Work IQ calls in the window.' {
    param($r)
    Write-Host ('{0}  {1} for {2}: {3} ({4} s)' -f $r.Time, $r.Agent, $r.User, $r.WorkIqCalls, $r.Seconds)
    Write-Host "          Graph calls by Work IQ: $($r.GraphCalls)" -ForegroundColor DarkGray
    if ($r.PurviewAudit) { Write-Host "          Purview audit: $($r.PurviewAudit)" -ForegroundColor DarkGray }
}

# 4. Work IQ's Graph calls
$graphSummary = $graph | Group-Object UserId, Method, Path | ForEach-Object {
    [pscustomobject]@{ User = Get-Upn $_.Group[0].UserId; Method = $_.Group[0].Method; Path = $_.Group[0].Path; Calls = $_.Count
        LastSeen = ($_.Group | Measure-Object At -Maximum).Maximum.ToString('HH:mm:ss') }
} | Sort-Object Calls -Descending | Select-Object -First 25
Add-Section 'Work IQ''s Microsoft Graph calls on the user''s behalf (MicrosoftGraphActivityLogs)' $graphSummary `
    @('User', 'Method', 'Path', 'Calls', 'LastSeen') 'None yet: Graph activity logs arrive within minutes.'

# 5. Work IQ in the Purview audit
$copilotRows = foreach ($p in $copilot) {
    [pscustomobject]@{ Time = $p.At.ToString('HH:mm:ss'); User = $p.User; Read = ($p.Read -join '; ')
        Jailbreak = if ([int]$p.Jailbreak) { 'DETECTED' } else { 'no' }; XPIA = if ([int]$p.Xpia) { 'DETECTED' } else { 'no' } }
}
Add-Section 'Work IQ''s Microsoft 365 Copilot interactions in the Purview audit (CopilotInteraction)' $copilotRows `
    @('Time', 'User', 'Jailbreak', 'XPIA', 'Read') 'None in the window: Work IQ''s ask and Work IQ Chat (A2A) create these, and they arrive within minutes.'
$m365User = if ($userId) { "| where AccountObjectId == '$userId'" } else { '' }
$m365 = Invoke-Hunting @"
CloudAppEvents
| where Timestamp > $window
| where ActionType != 'CopilotInteraction'
| where tostring(RawEventData) has '$workIq'
$m365User
| summarize Events = count(), LastSeen = max(Timestamp) by User = AccountDisplayName, Application, ActionType
| order by Events desc
"@
foreach ($m in $m365) { $m.LastSeen = Format-Time $m.LastSeen }
Add-Section 'What Work IQ read and wrote in Microsoft 365, under the user''s name (CloudAppEvents)' $m365 `
    @('User', 'Application', 'ActionType', 'Events', 'LastSeen') 'None yet: Microsoft 365 events arrive within minutes.'

# 6. The Purview DLP policy on the agents' prompts
$dlpMatches = Invoke-Hunting @"
CloudAppEvents
| where Timestamp > $window
| where ActionType == 'DLPRuleMatch'
| where tostring(RawEventData.ApplicationName) in ($kqlIds)
$purviewUser
| mv-expand pd = RawEventData.PolicyDetails to typeof(string)
| extend p = parse_json(pd)
| mv-expand rule = p.Rules
| project Time = Timestamp, User = tostring(RawEventData.UserId), AgentId = tostring(RawEventData.ApplicationName),
    Policy = tostring(p.PolicyName), Rule = tostring(rule.RuleName), Actions = strcat_array(rule.Actions, ', '),
    Detected = tostring(rule.ConditionsMatched.SensitiveInformation[0].SensitiveInformationTypeName)
| order by Time asc
"@
foreach ($d in $dlpMatches) {
    $d.Time = Format-Time $d.Time
    $d | Add-Member -NotePropertyName Agent -NotePropertyValue $agentNames[$d.AgentId] -Force
}
Add-Section 'Microsoft Purview DLP: rule matches on the agents'' prompts (DLPRuleMatch)' $dlpMatches `
    @('Time', 'User', 'Agent', 'Policy', 'Rule', 'Detected', 'Actions') 'No DLP rule matched in the window.'
$alertUser = if ($UserPrincipalName) { "| where tostring(a.f3u) =~ '$UserPrincipalName'" } else { '' }
$alerts = Invoke-Hunting @"
CloudAppEvents
| where Timestamp > $window
| where ActionType == 'AlertTriggered' and tostring(RawEventData.Category) == 'DataLossPrevention'
| extend a = parse_json(tostring(RawEventData.Data))
| where tostring(a.wl) == 'ExtendedApplications'
$alertUser
| project Time = Timestamp, Alert = tostring(RawEventData.Name), Severity = tostring(RawEventData.Severity),
    User = tostring(a.f3u), Policy = tostring(a.dpn), Status = tostring(RawEventData.Status)
| order by Time asc
"@
foreach ($a in $alerts) { $a.Time = Format-Time $a.Time }
Add-Section 'Microsoft Purview DLP alerts for the agents' $alerts @('Time', 'Alert', 'Severity', 'User', 'Policy', 'Status') `
    'No DLP alerts in the window.'

$flags = @($copilot | Where-Object { [int]$_.Jailbreak -or [int]$_.Xpia }).Count
$summary = "$((@($invocations) | Measure-Object Calls -Sum).Sum + 0) call(s) to the agents; $($calls.Count) Work IQ call(s) by the agents in " +
    "$($bursts.Count) burst(s); $(@($graph).Count) Graph call(s) by Work IQ; $(@($copilot).Count) audited Copilot interaction(s), " +
    "$flags with a jailbreak or XPIA detection; $((@($m365) | Measure-Object Events -Sum).Sum + 0) Microsoft 365 event(s); " +
    "$(@($dlpMatches).Count) DLP match(es); $(@($alerts).Count) DLP alert(s)."
Write-Host "`n$summary" -ForegroundColor Green
$report.Add("`n$summary")
if ($OutFile) { $report | Set-Content -Path $OutFile -Encoding utf8; Write-Host "Report: $OutFile" }
