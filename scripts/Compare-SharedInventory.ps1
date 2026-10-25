<#
.SYNOPSIS
    Compares two shared-resource snapshots and fails on anything but approved additions.

.DESCRIPTION
    -Before defaults to the oldest "baseline" snapshot; -After defaults to a fresh snapshot.
      Violation: an existing item was removed or its configuration changed, or an addition doesn't match
                 scripts\approved-additions.json (or a principal in <work>\principals.json).
      Review:    an addition that nothing here created but that the platform may add by itself, such as a
                 first-party service principal; it's listed, not failed.
    Exit code 1 if there's any violation.
#>
[CmdletBinding()]
param(
    [string]$Before,
    [string]$After
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Use-IsolatedAzure.ps1')

$dir = Join-Path $env:TEAM_WORK_DIR 'inventory'
if (-not $Before) {
    $Before = (Get-ChildItem $dir -Filter 'baseline-*.json' | Sort-Object Name | Select-Object -First 1).FullName
    if (-not $Before) { throw "No baseline snapshot in $dir. Run scripts\Get-SharedInventory.ps1 -Label baseline first." }
}
if (-not $After) { $After = (& (Join-Path $PSScriptRoot 'Get-SharedInventory.ps1') -Label 'check' | Select-Object -Last 1) }

$old = Get-Content -LiteralPath $Before -Raw | ConvertFrom-Json -AsHashtable
$new = Get-Content -LiteralPath $After -Raw | ConvertFrom-Json -AsHashtable
$approved = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'approved-additions.json') -Raw | ConvertFrom-Json
# This deployment's own resource group, from Terraform: never hard-coded, since every deployment names its own.
$ownGroup = terraform "-chdir=$(Join-Path (Split-Path $PSScriptRoot -Parent) 'terraform')" output -raw -no-color resource_group_name 2>$null
$ownGroupPattern = if ($LASTEXITCODE -eq 0 -and "$ownGroup".Trim()) { "/resourcegroups/$([regex]::Escape("$ownGroup".Trim().ToLowerInvariant()))/" } else { $null }
$principalsFile = Join-Path $env:TEAM_WORK_DIR 'principals.json'
$principals = @()
if (Test-Path -LiteralPath $principalsFile) {
    $principals = @((Get-Content -LiteralPath $principalsFile -Raw | ConvertFrom-Json).PSObject.Properties.Value | Where-Object { $_ })
}

$violations = [System.Collections.Generic.List[string]]::new()
$reviews = [System.Collections.Generic.List[string]]::new()
$additions = [System.Collections.Generic.List[string]]::new()

function Test-ApprovedName([string]$Name) {
    foreach ($p in $approved.namePatterns) { if ($Name -match $p) { return $true } }
    $false
}
function Test-ApprovedId([string]$Id) {
    foreach ($p in $approved.resourceIdPatterns) { if ($Id -match $p) { return $true } }
    if ($ownGroupPattern -and $Id.ToLowerInvariant() -match $ownGroupPattern) { return $true }
    $false
}
function Same($a, $b) { ($a | ConvertTo-Json -Depth 30 -Compress) -eq ($b | ConvertTo-Json -Depth 30 -Compress) }

# Compares two keyed lists. $IsApproved decides whether an addition is fine; $ReviewOnly marks additions that
# the platform may make by itself.
function Compare-Keyed([string]$What, $OldItems, $NewItems, [string]$Key, [scriptblock]$IsApproved, [switch]$ReviewOnly) {
    $o = @{}; foreach ($i in @($OldItems)) { if ($i) { $o[[string]$i[$Key]] = $i } }
    $n = @{}; foreach ($i in @($NewItems)) { if ($i) { $n[[string]$i[$Key]] = $i } }
    foreach ($k in $o.Keys) {
        if (-not $n.ContainsKey($k)) { $violations.Add("$What removed: $k"); continue }
        if (-not (Same $o[$k] $n[$k])) { $violations.Add("$What changed: $k") }
    }
    foreach ($k in $n.Keys) {
        if ($o.ContainsKey($k)) { continue }
        if (& $IsApproved $n[$k]) { $additions.Add("$What added: $k") }
        elseif ($ReviewOnly) { $reviews.Add("$What added (not by this repo?): $k") }
        else { $violations.Add("$What added without approval: $k") }
    }
}

$a = $old.arm; $b = $new.arm
Compare-Keyed 'resource' $a.resources $b.resources 'id' { param($x) Test-ApprovedId $x.id }
# A role assignment is approved for this solution's principals, or when its scope is inside an item this solution
# owns (for example its own project), whoever it's for.
Compare-Keyed 'role assignment' $a.roleAssignments $b.roleAssignments 'id' {
    param($x) ($principals -contains $x.principalId) -or (@($x.scope -split '/' | Where-Object { $_ -and (Test-ApprovedName $_) }).Count -gt 0) }
foreach ($single in 'foundryAccount', 'searchService') {
    if ($a[$single] -and -not (Same $a[$single] $b[$single])) { $violations.Add("$single configuration changed") }
}
if ($a.fabricCapacity) {
    $fa = [ordered]@{} + $a.fabricCapacity; $fb = [ordered]@{} + $b.fabricCapacity
    $fa.Remove('stateAtSnapshot'); $fb.Remove('stateAtSnapshot')
    if (-not (Same $fa $fb)) { $violations.Add('fabricCapacity configuration changed') }
    if ($a.fabricCapacity.stateAtSnapshot -ne $b.fabricCapacity.stateAtSnapshot) {
        $reviews.Add("Fabric capacity state: $($a.fabricCapacity.stateAtSnapshot) -> $($b.fabricCapacity.stateAtSnapshot) (this repo never changes it)")
    }
}
Compare-Keyed 'project' $a.projects $b.projects 'name' { param($x) Test-ApprovedName $x.name }
Compare-Keyed 'model deployment' $a.deployments $b.deployments 'name' { param($x) Test-ApprovedName $x.name }
foreach ($proj in @($b.connections.Keys)) {
    # Plain script blocks, called within this iteration: GetNewClosure() would hide this script's functions from them.
    $approvedHere = (-not $a.connections.ContainsKey($proj)) -and (Test-ApprovedName $proj)
    Compare-Keyed "connection in $proj" $a.connections[$proj] $b.connections[$proj] 'name' { param($x) $approvedHere }
}
foreach ($proj in @($a.connections.Keys)) {
    if (-not $b.connections.ContainsKey($proj)) { $violations.Add("connections of project $proj missing") }
}
foreach ($proj in @($new.foundry.Keys)) {
    $approvedHere = (-not $old.foundry.ContainsKey($proj)) -and (Test-ApprovedName $proj)
    $ok = { param($x) $approvedHere }
    Compare-Keyed "agent in $proj" $old.foundry[$proj].agents $new.foundry[$proj].agents 'name' $ok
    Compare-Keyed "toolbox in $proj" $old.foundry[$proj].toolboxes $new.foundry[$proj].toolboxes 'name' $ok
}
foreach ($proj in @($old.foundry.Keys)) {
    if (-not $new.foundry.ContainsKey($proj)) { $violations.Add("Foundry project $proj missing from data plane") }
}
Compare-Keyed 'search index' $old.search.indexes $new.search.indexes 'name' { param($x) Test-ApprovedName $x.name }
Compare-Keyed 'knowledge source' $old.search.knowledgeSources $new.search.knowledgeSources 'name' { param($x) Test-ApprovedName $x.name }
Compare-Keyed 'knowledge base' $old.search.knowledgeBases $new.search.knowledgeBases 'name' { param($x) Test-ApprovedName $x.name }
Compare-Keyed 'Fabric workspace' $old.fabric.workspaces $new.fabric.workspaces 'id' { param($x) Test-ApprovedName $x.name }
Compare-Keyed 'Entra application' $old.entra.applications $new.entra.applications 'id' { param($x) Test-ApprovedName $x.displayName }
Compare-Keyed 'service principal' $old.entra.servicePrincipals $new.entra.servicePrincipals 'id' {
    param($x) (Test-ApprovedName $x.displayName) -or ($principals -contains $x.id) } -ReviewOnly
$spNames = @{}
foreach ($s in @($new.entra.servicePrincipals)) { if ($s) { $spNames[$s.id] = $s.displayName } }
Compare-Keyed 'permission grant' $old.entra.permissionGrants $new.entra.permissionGrants 'id' {
    param($x) ($principals -contains $x.clientId) -or ($spNames[$x.clientId] -and (Test-ApprovedName $spNames[$x.clientId])) } -ReviewOnly

Write-Host "Before: $Before"
Write-Host "After:  $After"
Write-Host ("Approved additions ({0}):" -f $additions.Count)
$additions | Sort-Object | ForEach-Object { Write-Host "  + $_" }
if ($reviews.Count) {
    Write-Host ("For review ({0}):" -f $reviews.Count) -ForegroundColor Yellow
    $reviews | Sort-Object | ForEach-Object { Write-Host "  ? $_" -ForegroundColor Yellow }
}
if ($violations.Count) {
    Write-Host ("VIOLATIONS ({0}):" -f $violations.Count) -ForegroundColor Red
    $violations | Sort-Object | ForEach-Object { Write-Host "  ! $_" -ForegroundColor Red }
    exit 1
}
Write-Host 'No existing item was modified or removed.' -ForegroundColor Green
