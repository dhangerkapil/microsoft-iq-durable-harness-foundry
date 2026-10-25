<#
.SYNOPSIS
    Prewarms everything before a rehearsal or the live demo (run 10-15 minutes before you start).

.DESCRIPTION
    1. With -IncludeFabric (only if you will show the optional Fabric IQ segment): resumes the Fabric
       capacity and asks the Fabric data agent one question directly to warm it.
    2. Asks the hosted agent to clear any demo fault left from a rehearsal (Fabric outage and the
       process-crash switch) and any advisor preferences saved during a rehearsal, and to answer one memory
       question. This starts a container, connects the Work IQ toolbox and shows any pending consent link.
    3. Prints the UI address.
    Each step only warns on failure, so one slow component doesn't stop the rest of the prewarm.
#>
[CmdletBinding()]
param(
    [switch]$IncludeFabric,
    # Another hosted agent in the project (for example advisor-team-candidate); default: the Terraform-managed agent.
    [string]$AgentName
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
. "$PSScriptRoot\AdvisorCommon.ps1"
$tf = Join-Path (Split-Path $PSScriptRoot -Parent) 'terraform'
$target = if ($AgentName) { @{ AgentName = $AgentName } } else { @{} }

if ($IncludeFabric) {
try { & "$PSScriptRoot\Set-FabricCapacityState.ps1" -State Active } catch { Write-Warning "Could not resume the Fabric capacity: $($_.Exception.Message)" }

Write-Output 'Warming the Fabric data agent...'
try {
    $url = $env:FABRIC_DATA_AGENT_MCP_URL
    $headers = @{ Authorization = "Bearer $(Get-AzToken 'https://api.fabric.microsoft.com')"; Accept = 'application/json, text/event-stream' }
    function Send-Mcp($Message, [switch]$Notification) {
        $response = Invoke-WebRequest -Method Post -Uri $url -Headers $headers -ContentType 'application/json' `
            -Body ($Message | ConvertTo-Json -Depth 10 -Compress) -SkipHttpErrorCheck -TimeoutSec 300
        if ($response.Headers['Mcp-Session-Id']) { $headers['Mcp-Session-Id'] = [string]@($response.Headers['Mcp-Session-Id'])[0] }
        if ($Notification) { return }
        $text = if ($response.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($response.Content) } else { [string]$response.Content }
        if (-not $text.TrimStart().StartsWith('{')) { $text = ($text -split "`n" | Where-Object { $_ -like 'data:*' } | ForEach-Object { $_.Substring(5).Trim() } | Select-Object -Last 1) }
        if (-not $text) { throw "Empty MCP reply ($($response.StatusCode))." }
        $reply = $text | ConvertFrom-Json -Depth 50
        if ($reply.error) { throw "MCP error: $($reply.error | ConvertTo-Json -Depth 5 -Compress)" }
        $reply.result
    }
    $started = Get-Date
    $null = Send-Mcp @{ jsonrpc = '2.0'; id = 1; method = 'initialize'; params = @{ protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = @{ name = 'prewarm'; version = '1' } } }
    Send-Mcp @{ jsonrpc = '2.0'; method = 'notifications/initialized' } -Notification
    $tool = @((Send-Mcp @{ jsonrpc = '2.0'; id = 2; method = 'tools/list'; params = @{} }).tools)[0]
    if (-not $tool) { throw 'The data agent lists no tools; is it published?' }
    $result = Send-Mcp @{ jsonrpc = '2.0'; id = 3; method = 'tools/call'; params = @{ name = $tool.name; arguments = @{ userQuestion = "What is Michael Scott's total portfolio value on the latest date?" } } }
    $text = ($result.content | Where-Object type -eq 'text' | ForEach-Object text) -join ' '
    Write-Output "  Fabric answered in $([int]((Get-Date) - $started).TotalSeconds)s: $($text.Substring(0, [Math]::Min(160, $text.Length)))"
} catch {
    Write-Warning "Fabric warm-up failed: $($_.Exception.Message). The agent falls back to its saved snapshot if this persists."
}
}

Write-Output 'Warming the hosted agent and clearing any demo fault and rehearsal preferences...'
try {
    $answer = & "$PSScriptRoot\Invoke-Agent.ps1" @target -TimeoutSec 600 -Prompt (
        'Presenter setup: clear both demo faults (call demo_fault_switch with component fabric and enabled false, and ' +
        'with component process_crash and enabled false), and clear my saved advisor preferences (call ' +
        'advisor_preferences with action clear). ' +
        'Then, in one sentence, tell me when my last saved discussion with Michael Scott was, using only client memory.')
    $answer | Select-Object -Last 4 | Write-Output
    if ($answer -match 'CONSENT LINK') { Write-Warning 'A consent link is pending: open it in the InPrivate window before the demo.' }
} catch {
    Write-Warning "Hosted agent warm-up failed: $($_.Exception.Message)"
}
$liveAgent = (terraform "-chdir=$tf" output -raw -no-color agent_name 2>$null)
$ui = if ($AgentName -and $AgentName -ne $liveAgent) {
    # A UI instance for another agent (scripts/Publish-UiInstance.ps1) carries the agent's name as a tag.
    $rg = (terraform "-chdir=$tf" output -raw -no-color resource_group_name 2>$null)
    $fqdn = az containerapp list -g $rg --query "[?tags.agent=='$AgentName'].properties.configuration.ingress.fqdn | [0]" -o tsv 2>$null
    if ($fqdn) { "https://$fqdn" }
} else { (terraform "-chdir=$tf" output -raw -no-color ui_url 2>$null) }
if ($ui) { Write-Output "UI: $ui" }
