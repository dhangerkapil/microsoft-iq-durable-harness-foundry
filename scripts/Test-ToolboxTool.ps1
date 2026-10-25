<#
.SYNOPSIS
    Calls a Foundry toolbox MCP endpoint directly as the signed-in user: lists its tools and,
    optionally, calls one. Used to check a tool connection without the hosted agent.

.EXAMPLE
    .\scripts\Test-ToolboxTool.ps1 -Toolbox advisor-tools
    .\scripts\Test-ToolboxTool.ps1 -Toolbox advisor-fabric-check -Tool fabric_portfolio___<name> -Arguments @{ userQuestion = '...' }
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Toolbox,
    [string]$Tool,
    [hashtable]$Arguments = @{},
    [int]$TimeoutSec = 300
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
. "$PSScriptRoot\AdvisorCommon.ps1"
$tf = Join-Path (Split-Path $PSScriptRoot -Parent) 'terraform'
$project = (terraform "-chdir=$tf" output -raw -no-color foundry_project_endpoint).Trim()
$url = "$project/toolboxes/$Toolbox/mcp?api-version=v1"
$headers = @{ Authorization = "Bearer $(Get-AzToken 'https://ai.azure.com')"; Accept = 'application/json, text/event-stream' }
$script:id = 0

function Invoke-Mcp([string]$Method, $Params, [switch]$Notification) {
    $message = [ordered]@{ jsonrpc = '2.0'; method = $Method }
    if (-not $Notification) { $script:id++; $message.id = $script:id }
    if ($null -ne $Params) { $message.params = $Params }
    $response = Invoke-WebRequest -Method Post -Uri $url -Headers $headers -ContentType 'application/json' `
        -Body ($message | ConvertTo-Json -Depth 20 -Compress) -TimeoutSec $TimeoutSec -SkipHttpErrorCheck
    if ($response.Headers['Mcp-Session-Id']) { $headers['Mcp-Session-Id'] = [string]@($response.Headers['Mcp-Session-Id'])[0] }
    if ($Notification) { return }
    $text = if ($response.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($response.Content) } else { [string]$response.Content }
    if ($response.StatusCode -ge 400) { throw "MCP $Method failed ($($response.StatusCode)): $text" }
    $json = if ($text.TrimStart().StartsWith('{')) { $text } else {
        (($text -split "`n") | Where-Object { $_ -like 'data:*' } | ForEach-Object { $_.Substring(5).Trim() } | Select-Object -Last 1)
    }
    $result = $json | ConvertFrom-Json -Depth 50
    if ($result.error) { throw "MCP $Method error: $($result.error | ConvertTo-Json -Depth 10 -Compress)" }
    $result.result
}

$init = Invoke-Mcp 'initialize' @{ protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = @{ name = 'advisor-team-check'; version = '1.0' } }
Write-Output "server: $($init.serverInfo.name) $($init.serverInfo.version)"
Invoke-Mcp 'notifications/initialized' $null -Notification
$tools = Invoke-Mcp 'tools/list' @{}
foreach ($t in $tools.tools) {
    Write-Output "tool: $($t.name)"
    Write-Output "  input: $(($t.inputSchema | ConvertTo-Json -Depth 8 -Compress))"
}
if ($Tool) {
    $started = Get-Date
    $call = Invoke-Mcp 'tools/call' @{ name = $Tool; arguments = $Arguments }
    Write-Output "call: $Tool isError=$($call.isError) elapsed=$([int]((Get-Date) - $started).TotalSeconds)s"
    foreach ($block in $call.content) { if ($block.type -eq 'text') { Write-Output $block.text } else { Write-Output "[$($block.type)]" } }
}
