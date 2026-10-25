<#
.SYNOPSIS
    Runs the morning note (add-on O1): starts the durable workflow, waits for its approval request, shows the draft,
    and approves or rejects it through the respond endpoint; then waits for the published result.

.DESCRIPTION
    The workflow runs on Azure Functions and the Durable Task Scheduler. While it waits for the approval it uses no
    compute; the scheduler's dashboard shows one durable activity per step. Without -Approve or -Reject the script
    asks. -InstanceId continues a run that is already waiting (for example one the weekday timer started).

.EXAMPLE
    .\scripts\Invoke-MorningNote.ps1 -Approve
    .\scripts\Invoke-MorningNote.ps1 -Tickers NVDA,MSFT -Reject
    .\scripts\Invoke-MorningNote.ps1 -InstanceId <id> -Approve
#>
[CmdletBinding()]
param(
    [string[]]$Tickers = @(),
    [string]$InstanceId,
    [switch]$Approve,
    [switch]$Reject,
    [string]$Reviewer = 'Head of Research',
    [int]$TimeoutSec = 900
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
$tf = Join-Path (Split-Path $PSScriptRoot -Parent) 'terraform'
$url = (terraform "-chdir=$tf" output -raw -no-color morning_note_url 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $url -or $url -eq 'null') { throw 'The morning note is not provisioned (enable_morning_note).' }
$app = (terraform "-chdir=$tf" output -raw -no-color morning_note_function_name).Trim()
$resourceGroup = (terraform "-chdir=$tf" output -raw -no-color resource_group_name).Trim()
$key = (az functionapp keys list --resource-group $resourceGroup --name $app --query 'functionKeys.default' -o tsv).Trim()
if (-not $key) { throw "Could not read the function key of $app." }
$headers = @{ 'x-functions-key' = $key }
$base = "$url/api/workflow/morning-note"

function Get-Status([string]$Id) { Invoke-RestMethod -Uri "$base/status/$Id" -Headers $headers -TimeoutSec 60 }
function Wait-For([string]$Id, [scriptblock]$Done) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec); $started = Get-Date
    do {
        $status = Get-Status $Id
        if (& $Done $status) { return $status }
        Write-Host ("  ... {0} {1:n0} s" -f $status.runtimeStatus, ((Get-Date) - $started).TotalSeconds)
        Start-Sleep -Seconds 10
    } while ((Get-Date) -lt $deadline)
    throw "Timed out after $TimeoutSec s; the run continues: .\scripts\Invoke-MorningNote.ps1 -InstanceId $Id"
}

if (-not $InstanceId) {
    $body = @{ tickers = @($Tickers | ForEach-Object { $_ -split ',' } | Where-Object { $_ }) } | ConvertTo-Json -Compress
    $started = Invoke-RestMethod -Method Post -Uri "$base/run" -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 60
    $InstanceId = $started.instanceId
    Write-Output "Started the morning note: instance $InstanceId"
}

$terminal = 'Completed', 'Failed', 'Terminated', 'Canceled'
$status = Wait-For $InstanceId { param($s) $s.pendingHumanInputRequests -or $s.runtimeStatus -in $terminal }
if ($status.runtimeStatus -notin $terminal) {
    $request = @($status.pendingHumanInputRequests)[0]
    $data = $request.requestData
    Write-Output "`n[Approval] $($data.question)`n"
    Write-Output $data.note
    Write-Output ''
    $decision = if ($Approve) { $true } elseif ($Reject) { $false } else { (Read-Host 'Publish it? [y/N]') -match '^(y|yes)$' }
    $reply = @{ approve = $decision; reviewer = $Reviewer; comment = '' } | ConvertTo-Json -Compress
    Invoke-RestMethod -Method Post -Uri $request.respondUrl -Headers $headers -ContentType 'application/json' -Body $reply -TimeoutSec 60 | Out-Null
    Write-Output "$(if ($decision) { 'Approved' } else { 'Rejected' }) by $Reviewer."
    $status = Wait-For $InstanceId { param($s) $s.runtimeStatus -in $terminal }
}
Write-Output "`nRun $InstanceId $($status.runtimeStatus): $($status.output | ConvertTo-Json -Depth 6 -Compress)"
Write-Output "Durable Task Scheduler dashboard: https://dashboard.durabletask.io/ (endpoint $(terraform "-chdir=$tf" output -raw -no-color durable_task_scheduler_endpoint), task hub advisor-team)"
