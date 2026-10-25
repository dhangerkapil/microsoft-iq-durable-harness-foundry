<#
.SYNOPSIS
    Pauses or resumes the Terraform-managed Fabric capacity (billing stops while it is paused).

.EXAMPLE
    .\scripts\Set-FabricCapacityState.ps1 -State Paused
    .\scripts\Set-FabricCapacityState.ps1 -State Active    # resume about an hour before rehearsals and the demo
#>
[CmdletBinding()]
param([Parameter(Mandatory)][ValidateSet('Active', 'Paused')][string]$State)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
$id = if ($env:FABRIC_CAPACITY_ID) { $env:FABRIC_CAPACITY_ID } else {
    (terraform "-chdir=$(Join-Path (Split-Path $PSScriptRoot -Parent) 'terraform')" output -raw -no-color fabric_capacity_id).Trim()
}
$api = '?api-version=2023-11-01'
$current = (az rest --method get --url "https://management.azure.com$id$api" --query 'properties.state' -o tsv).Trim()
Write-Output "Capacity state: $current"
$action = if ($State -eq 'Active') { 'resume' } else { 'suspend' }
if (($State -eq 'Active' -and $current -eq 'Active') -or ($State -eq 'Paused' -and $current -in 'Paused', 'Suspended')) { return }
az rest --method post --url "https://management.azure.com$id/$action$api" -o none
# A capacity that is still pausing (or resuming) ignores the opposite request: wait for it to settle, then ask again.
for ($i = 0; $i -lt 100; $i++) {
    Start-Sleep -Seconds 15
    $current = (az rest --method get --url "https://management.azure.com$id$api" --query 'properties.state' -o tsv).Trim()
    if (($State -eq 'Active' -and $current -eq 'Active') -or ($State -eq 'Paused' -and $current -in 'Paused', 'Suspended')) { break }
    if (($State -eq 'Active' -and $current -in 'Paused', 'Suspended') -or ($State -eq 'Paused' -and $current -eq 'Active')) {
        az rest --method post --url "https://management.azure.com$id/$action$api" -o none
    }
}
Write-Output "Capacity state: $current"
