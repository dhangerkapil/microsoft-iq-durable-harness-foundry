<#
.SYNOPSIS
    Sends a prompt to the deployed hosted agent as the signed-in user (isolated profile)
    and prints the answer, tool activity and any consent links.

.EXAMPLE
    .\scripts\Invoke-Agent.ps1 -Prompt "I'm meeting Michael Scott on Thursday. What should I remember?"
    .\scripts\Invoke-Agent.ps1 -Prompt "What changed since then?" -Continue
    .\scripts\Invoke-Agent.ps1 -Approve <approval request id>   # or -Reject; continues the last conversation
    .\scripts\Invoke-Agent.ps1 -AgentName advisor-team-candidate -Prompt "..."   # a candidate agent
#>
[CmdletBinding()]
param(
    [string]$Prompt,
    [switch]$Continue,
    [string]$Approve,
    [string]$Reject,
    [int]$TimeoutSec = 600,
    [string]$SaveAs,
    # Another hosted agent in the same project (such as a candidate); default: the Terraform-managed agent.
    [string]$AgentName
)

$ErrorActionPreference = 'Stop'
if (-not $Prompt -and -not $Approve -and -not $Reject) { throw 'Give -Prompt, or -Approve/-Reject with an approval request ID.' }
if ($Approve -or $Reject) { $Continue = $true }
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
$repoRoot = Split-Path $PSScriptRoot -Parent
$tf = Join-Path $repoRoot 'terraform'
$project = (terraform "-chdir=$tf" output -raw -no-color foundry_project_endpoint).Trim()
$agent = if ($AgentName) { $AgentName } else { (terraform "-chdir=$tf" output -raw -no-color agent_name).Trim() }
$agentUrl = "$project/agents/$([uri]::EscapeDataString($agent))/endpoint/protocols/openai"
$token = az account get-access-token --resource 'https://ai.azure.com' --query accessToken -o tsv
$headers = @{ Authorization = "Bearer $token" }
$stateFile = Join-Path $env:TEAM_WORK_DIR $(if ($AgentName) { "last-conversation-$AgentName.txt" } else { 'last-conversation.txt' })

$conversation = $null
if ($Continue -and (Test-Path $stateFile)) { $conversation = (Get-Content $stateFile -Raw).Trim() }
if (-not $conversation) {
    $conversation = (Invoke-RestMethod -Method Post -Uri "$agentUrl/conversations?api-version=v1" -Headers $headers -ContentType 'application/json' -Body '{}').id
    Set-Content -Path $stateFile -Value $conversation -Encoding ascii
}
Write-Output "conversation: $conversation"

if ($Approve -or $Reject) {
    # The hosted agent resumes the approval-gated tool call only after this explicit decision.
    $requestInput = @(@{ type = 'mcp_approval_response'; approval_request_id = $(if ($Approve) { $Approve } else { $Reject }); approve = [bool]$Approve })
} else { $requestInput = $Prompt }
$body = @{ input = $requestInput; conversation = $conversation; store = $true } | ConvertTo-Json -Depth 5
$started = Get-Date
try {
    $response = Invoke-RestMethod -Method Post -Uri "$agentUrl/responses?api-version=v1" -Headers $headers `
        -ContentType 'application/json' -Body $body -TimeoutSec $TimeoutSec
} catch {
    $detail = $_.ErrorDetails.Message
    Write-Output "request failed after $([int]((Get-Date) - $started).TotalSeconds)s: $($_.Exception.Message)"
    if ($detail) { Write-Output $detail.Substring(0, [Math]::Min(3000, $detail.Length)) }
    exit 1
}
$elapsed = [int]((Get-Date) - $started).TotalSeconds
Write-Output "response: $($response.id) status=$($response.status) elapsed=${elapsed}s session=$($response.agent_session_id)"
if ($SaveAs) {
    # The prompt travels with the answer so evals\foundry_quality.py can score saved answers as a dataset.
    if ($Prompt) { $response | Add-Member -NotePropertyName advisor_prompt -NotePropertyValue $Prompt -Force }
    $response | ConvertTo-Json -Depth 30 | Set-Content -Path (Join-Path $env:TEAM_WORK_DIR "$SaveAs.json") -Encoding utf8
}
foreach ($item in $response.output) {
    switch -Regex ($item.type) {
        '^message$' {
            $text = ($item.content | Where-Object { $_.text } | ForEach-Object text) -join "`n"
            Write-Output "--- answer ---"
            Write-Output $text
        }
        default {
            $line = "[$($item.type)]"
            if ($item.name) { $line += " $($item.name)" }
            if ($item.server_label) { $line += " server=$($item.server_label)" }
            if ($item.status) { $line += " status=$($item.status)" }
            if ($item.arguments) { $a = [string]$item.arguments; $line += " args=$($a.Substring(0, [Math]::Min(300, $a.Length)))" }
            if ($item.error) { $line += " error=$(($item.error | ConvertTo-Json -Depth 5 -Compress))" }
            Write-Output $line
            if ($item.type -eq 'mcp_approval_request') {
                Write-Output "    APPROVAL NEEDED: rerun with -Approve $($item.id) (or -Reject $($item.id))"
            }
            if ($item.consent_link) {
                Write-Output "    CONSENT LINK ($($item.server_label)): open in the InPrivate window, sign in, accept, then rerun:"
                Write-Output "    $($item.consent_link)"
            }
            if ($item.type -eq 'function_call_output') {
                # Fabric IQ and Work IQ Chat are called inside a tool, so their consent links arrive in the tool's
                # result (wrapped in the runtime's timing envelope), never as a consent request.
                $result = $null
                try {
                    $envelope = [string]$item.output | ConvertFrom-Json -ErrorAction Stop
                    $result = if ($envelope.PSObject.Properties.Name -contains 'output') { $envelope.output } else { $envelope }
                    if ($result -is [string]) { $result = $result | ConvertFrom-Json -ErrorAction Stop }
                } catch { $result = $null }
                if ($result -and $result.PSObject.Properties.Name -contains 'status' -and
                    $result.status -in 'FABRIC_CONSENT_REQUIRED', 'WORKIQ_CHAT_CONSENT_REQUIRED' -and $result.consent_link) {
                    $source = if ($result.status -like 'FABRIC*') { 'fabric' } else { 'workiq chat a2a' }
                    Write-Output "    CONSENT LINK ($source): open in the InPrivate window, sign in, accept, then rerun:"
                    Write-Output "    $($result.consent_link)"
                }
            }
        }
    }
}
if ($response.error) { Write-Output "error: $($response.error | ConvertTo-Json -Depth 6 -Compress)" }
