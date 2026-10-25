<#
.SYNOPSIS
    Lists the hosted agent's sessions and stops one, so the presenter can force a restart instead of waiting
    for idle eviction (15 minutes by default).

.DESCRIPTION
    Uses the documented session management REST API of Foundry Agent Service, api-version v1
    (https://learn.microsoft.com/azure/foundry/agents/how-to/manage-hosted-sessions):
        GET  {project endpoint}/agents/{agent}/endpoint/sessions?api-version=v1
        POST {project endpoint}/agents/{agent}/endpoint/sessions/{session id}:stop?api-version=v1
    Stopping a session terminates its running compute and keeps its persisted filesystem ($HOME and uploaded
    files). The session is retained: the next request in the same conversation provisions new compute, which
    starts a new agent process. It is the on-demand equivalent of idle eviction. Items in the Foundry state store
    never depend on compute. This script never deletes a session (DELETE .../sessions/{id} would release the
    session and its files).

    Stop a session between turns. Stopping it while a response is running ends that turn; the MAF hosting library
    then keeps that conversation locked (as after a crash), so continue in a new conversation.

    The calls run as the signed-in Azure CLI identity of the isolated profile. With the default Entra isolation,
    list and stop act only on that identity's own sessions; a caller with the Foundry User role on the project
    sees every session on the agent.

    How a client learns the session of a conversation (Responses protocol): every response body carries it in the
    agent_session_id field (Python SDK: response.model_extra["agent_session_id"]). With a conversation ID, the
    platform binds the conversation to one session and routes every turn there, so the value stays the same for
    the whole conversation. (The agent server SDK in the container also sets an x-agent-session-id response
    header; the docs don't say whether the gateway passes it to clients, so rely on the body field.)
    Invoke-Agent.ps1 prints it, and the agent's show_state_store tool reports it as
    this_session.platform_session_id.

.EXAMPLE
    .\scripts\Restart-AgentSession.ps1 -List                     # list sessions only
    .\scripts\Restart-AgentSession.ps1                           # stop the most recently used active session
    .\scripts\Restart-AgentSession.ps1 -SessionId <agent_session_id>
#>
[CmdletBinding()]
param(
    [string]$SessionId,
    [switch]$List,
    # Another hosted agent in the project (for example advisor-team-candidate); default: the Terraform-managed agent.
    [string]$AgentName
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
$repoRoot = Split-Path $PSScriptRoot -Parent
$tf = Join-Path $repoRoot 'terraform'
$project = (terraform "-chdir=$tf" output -raw -no-color foundry_project_endpoint).Trim()
$agent = if ($AgentName) { $AgentName } else { (terraform "-chdir=$tf" output -raw -no-color agent_name).Trim() }
$sessionsUrl = "$project/agents/$([uri]::EscapeDataString($agent))/endpoint/sessions"
$token = az account get-access-token --resource 'https://ai.azure.com' --query accessToken -o tsv
$headers = @{ Authorization = "Bearer $token" }

function Format-UnixTime($Seconds) {
    if ($Seconds) { [DateTimeOffset]::FromUnixTimeSeconds([long]$Seconds).LocalDateTime.ToString('yyyy-MM-dd HH:mm:ss') }
}

function Invoke-SessionApi([string]$Method, [string]$Uri) {
    try { Invoke-RestMethod -Method $Method -Uri $Uri -Headers $headers }
    catch {
        $detail = $_.ErrorDetails.Message
        throw "$Method $Uri failed: $($_.Exception.Message) $detail"
    }
}

$sessions = @()
$after = $null
do {
    $uri = "${sessionsUrl}?api-version=v1&limit=100&order=desc"
    if ($after) { $uri += "&after=$([uri]::EscapeDataString($after))" }
    $page = Invoke-SessionApi Get $uri
    $sessions += @($page.data | Where-Object { $_ })
    $after = $page.last_id
} while ($page.has_more -and $after -and $sessions.Count -lt 500)

$sessions = @($sessions | Sort-Object { [long]$_.last_accessed_at } -Descending)
Write-Output "Agent '$agent': $($sessions.Count) session(s) visible to the signed-in identity, most recently used first."
$sessions | Select-Object -First 15 | ForEach-Object {
    [pscustomobject]@{
        session   = $_.agent_session_id
        status    = $_.status
        version   = $_.version_indicator.agent_version
        created   = Format-UnixTime $_.created_at
        last_used = Format-UnixTime $_.last_accessed_at
        stopped   = Format-UnixTime $_.stopped_at
    }
} | Format-Table -AutoSize | Out-String -Width 220 | Write-Output
if ($List) { return }

if (-not $SessionId) {
    $target = $sessions | Where-Object { "$($_.status)" -eq 'active' } | Select-Object -First 1
    if (-not $target) {
        Write-Output 'No active session to stop: the next request starts new compute and a new agent process anyway.'
        return
    }
    $SessionId = $target.agent_session_id
}
$sessionUrl = "$sessionsUrl/$([uri]::EscapeDataString($SessionId))"
Write-Output "Stopping session $SessionId..."
$null = Invoke-SessionApi Post "$($sessionUrl):stop?api-version=v1"
$session = Invoke-SessionApi Get "$($sessionUrl)?api-version=v1"
Write-Output "Session $SessionId is now '$($session.status)'. Its compute is gone; its persisted state is kept."
Write-Output 'Send the next message in the same conversation: the platform provisions new compute and a new agent process,'
Write-Output 'and show_state_store then reports a new this_process with a short uptime.'
