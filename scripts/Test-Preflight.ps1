<#
.SYNOPSIS
    Preflight: every name this solution plans to create in a shared resource must be
    free, unless this solution's own Terraform state already holds it (a re-run).

.DESCRIPTION
    Read-only. Checks the shared Foundry account (project, deployments, guardrail policy), the shared Search service
    (index, knowledge source, knowledge base), the shared Fabric capacity's workspaces and the tenant's app
    registrations. Fails with the exact conflicting item.
#>
[CmdletBinding()]
param(
    [string]$Suffix
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Use-IsolatedAzure.ps1') -RequireLogin
if (-not $Suffix) { $Suffix = $env:TEAM_NAME_SUFFIX }
if (-not $Suffix) { throw 'Set TEAM_NAME_SUFFIX in .env.local (or pass -Suffix).' }

$tfDir = Join-Path (Split-Path $PSScriptRoot -Parent) 'terraform'
$owned = @()
if (Test-Path -LiteralPath $env:TEAM_TF_STATE) {
    $state = Get-Content -LiteralPath $env:TEAM_TF_STATE -Raw | ConvertFrom-Json
    $owned = @($state.resources | Where-Object { $_.mode -eq 'managed' } | ForEach-Object { $_.instances.attributes.id } | Where-Object { $_ } |
        ForEach-Object { "$_".ToLowerInvariant() })
}

$conflicts = [System.Collections.Generic.List[string]]::new()
function Test-Free([string]$What, [string]$Id, [bool]$Exists) {
    if ($Exists -and ($owned -notcontains $Id.ToLowerInvariant())) { $conflicts.Add("$What already exists and isn't this solution's: $Id") }
}
function Test-ArmExists([string]$Id, [string]$ApiVersion) {
    az rest --method get --url "https://management.azure.com${Id}?api-version=$ApiVersion" -o none 2>$null
    $LASTEXITCODE -eq 0
}

$checked = 0
if ($env:EXISTING_FOUNDRY_ACCOUNT_ID) {
    $acct = $env:EXISTING_FOUNDRY_ACCOUNT_ID
    $items = [ordered]@{
        "project proj-advteam-$Suffix"     = @("$acct/projects/proj-advteam-$Suffix", '2025-06-01')
        "guardrail team-guardrail-$Suffix" = @("$acct/raiPolicies/team-guardrail-$Suffix", '2025-06-01')
        'deployment team-lead'             = @("$acct/deployments/team-lead", '2024-10-01')
        'deployment team-judge'            = @("$acct/deployments/team-judge", '2024-10-01')
        'deployment team-specialist'       = @("$acct/deployments/team-specialist", '2024-10-01')
    }
    foreach ($k in $items.Keys) { Test-Free $k $items[$k][0] (Test-ArmExists $items[$k][0] $items[$k][1]); $checked++ }
}

if ($env:EXISTING_SEARCH_SERVICE_ID) {
    $search = "https://$(($env:EXISTING_SEARCH_SERVICE_ID -split '/')[-1]).search.windows.net"
    $token = az account get-access-token --resource https://search.azure.com --query accessToken -o tsv
    $headers = @{ Authorization = "Bearer $token" }
    foreach ($kind in 'indexes', 'knowledgesources', 'knowledgebases') {
        $names = @((Invoke-RestMethod -Uri "$search/${kind}?api-version=2026-08-01-preview" -Headers $headers).value.name)
        foreach ($n in $names | Where-Object { $_ -like 'team-firm-*' }) { $conflicts.Add("Search $kind already has $n (not created by this repo's scripts?)") }
        $checked++
    }
}

if ($env:EXISTING_FABRIC_CAPACITY_ID) {
    $token = az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv
    $ws = (Invoke-RestMethod -Uri 'https://api.fabric.microsoft.com/v1/workspaces' -Headers @{ Authorization = "Bearer $token" }).value
    # FABRIC_WORKSPACE_ID in .env.local is this solution's own workspace (written by the Fabric step).
    $ourId = $env:FABRIC_WORKSPACE_ID
    foreach ($w in @($ws | Where-Object { $_.displayName -eq 'Advisor Team' -and $_.id -ne $ourId })) {
        $conflicts.Add("Fabric workspace 'Advisor Team' already exists ($($w.id)) and isn't recorded as this solution's")
    }
    $checked++
}

$apps = az ad app list --filter "startswith(displayName,'advisor-team-$Suffix')" --query "[].{id:id, name:displayName}" -o json | ConvertFrom-Json
foreach ($a in @($apps)) { Test-Free "app registration $($a.name)" $a.id $true }
$checked++

if ($conflicts.Count) {
    $conflicts | ForEach-Object { Write-Host "  ! $_" -ForegroundColor Red }
    throw "Preflight failed: $($conflicts.Count) planned name(s) are taken. Choose another TEAM_NAME_SUFFIX or remove the conflicting items."
}
Write-Output "Preflight passed: $checked checks, every planned name is free or already this solution's."
