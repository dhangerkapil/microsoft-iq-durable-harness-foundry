<#
.SYNOPSIS
    Read-only snapshot of every shared resource this solution must not modify.

.DESCRIPTION
    Captures configuration, not runtime state:
      - ARM: every resource in the shared resource groups; the shared Foundry account, Search service and
        Fabric capacity; the account's projects, model deployments and project connections (no secrets);
      - role assignments at and below the shared resource groups;
      - the Foundry data plane of every existing project: agents (all versions) and toolboxes;
      - Search: indexes, knowledge sources and knowledge bases;
      - Fabric: workspaces on the shared capacity and their items;
      - Entra: applications, service principals and delegated permission grants in the tenant.

    Writes JSON to -OutFile (default: <work>\inventory\<label>-<time>.json). Compare two snapshots with
    scripts\Compare-SharedInventory.ps1. Only GET requests are made.
#>
[CmdletBinding()]
param(
    [string]$Label = 'snapshot',
    [string]$OutFile
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Use-IsolatedAzure.ps1') -RequireLogin

$accountId = $env:EXISTING_FOUNDRY_ACCOUNT_ID
$searchId = $env:EXISTING_SEARCH_SERVICE_ID
$capacityId = $env:EXISTING_FABRIC_CAPACITY_ID
if (-not ($accountId -or $searchId -or $capacityId)) {
    Write-Host 'No shared resources configured (EXISTING_* are empty): nothing to snapshot.'
    return
}

$tokens = @{}
function Get-Token([string]$Resource) {
    if (-not $tokens[$Resource]) {
        $tokens[$Resource] = az account get-access-token --resource $Resource --query accessToken -o tsv
        if ($LASTEXITCODE -ne 0 -or -not $tokens[$Resource]) { throw "No token for $Resource." }
    }
    $tokens[$Resource]
}

function Invoke-Get([string]$Uri, [string]$Resource) {
    $headers = @{ Authorization = "Bearer $(Get-Token $Resource)" }
    for ($attempt = 1; ; $attempt++) {
        try { return Invoke-RestMethod -Method Get -Uri $Uri -Headers $headers -TimeoutSec 60 }
        catch {
            $status = 0
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            if ($status -eq 404) { return $null }
            if ($attempt -ge 3 -or $status -in 400, 401, 403) { throw "GET $Uri failed ($status): $($_.Exception.Message)" }
            Start-Sleep -Seconds (2 * $attempt)
        }
    }
}

function Get-All([string]$Uri, [string]$Resource) {
    $items = [System.Collections.Generic.List[object]]::new()
    $next = $Uri
    while ($next) {
        $page = Invoke-Get $next $Resource
        if (-not $page) { break }
        $values = if ($null -ne $page.value) { $page.value } elseif ($null -ne $page.data) { $page.data } else { @() }
        foreach ($v in @($values)) { $items.Add($v) }
        if ($page.nextLink) { $next = $page.nextLink }
        elseif ($page.'@odata.nextLink') { $next = $page.'@odata.nextLink' }
        elseif ($page.has_more -and $page.last_id) {
            $sep = if ($Uri.Contains('?')) { '&' } else { '?' }
            $next = "$Uri${sep}after=$($page.last_id)"
        }
        else { $next = $null }
    }
    , $items.ToArray()
}

function Get-Hash($Value) {
    $json = $Value | ConvertTo-Json -Depth 30 -Compress
    if ($null -eq $json) { $json = 'null' }
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    ([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes))).Substring(0, 16).ToLowerInvariant()
}

function Select-Props($Object, [string[]]$Names) {
    $out = [ordered]@{}
    if ($null -eq $Object) { return $out }
    foreach ($n in $Names) { if ($Object.PSObject.Properties[$n]) { $out[$n] = $Object.$n } }
    $out
}

function Sort-ById($Items) { , @($Items | Sort-Object -Property { $_.id }) }

$arm = 'https://management.azure.com'
$snapshot = [ordered]@{
    label   = $Label
    takenAt = (Get-Date).ToUniversalTime().ToString('o')
    user    = (az account show --query user.name -o tsv)
    arm     = [ordered]@{}
    foundry = [ordered]@{}
    search  = [ordered]@{}
    fabric  = [ordered]@{}
    entra   = [ordered]@{}
}

# --- ARM: shared resource groups and resources ---------------------------------------------------------------
$sharedGroups = @(@($accountId, $searchId, $capacityId) | Where-Object { $_ } | ForEach-Object {
        ($_ -split '/providers/')[0] } | Sort-Object -Unique)
$resources = [System.Collections.Generic.List[object]]::new()
foreach ($rg in $sharedGroups) {
    foreach ($r in (Get-All "$arm$rg/resources?api-version=2021-04-01" $arm)) {
        $resources.Add([ordered]@{ id = $r.id.ToLowerInvariant(); type = $r.type; location = $r.location; sku = $r.sku.name
                tags = (Get-Hash $r.tags) })
    }
}
$snapshot.arm.resourceGroups = $sharedGroups
$snapshot.arm.resources = Sort-ById $resources

$assignments = @{}
foreach ($rg in $sharedGroups) {
    foreach ($ra in (Get-All "$arm$rg/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01" $arm)) {
        $assignments[$ra.id.ToLowerInvariant()] = [ordered]@{ id = $ra.id.ToLowerInvariant()
            scope = $ra.properties.scope.ToLowerInvariant(); principalId = $ra.properties.principalId
            role = ($ra.properties.roleDefinitionId -split '/')[-1] }
    }
}
$snapshot.arm.roleAssignments = Sort-ById $assignments.Values

if ($accountId) {
    $account = Invoke-Get "$arm${accountId}?api-version=2025-06-01" $arm
    $p = $account.properties
    $snapshot.arm.foundryAccount = [ordered]@{
        id       = $accountId.ToLowerInvariant(); sku = $account.sku.name; location = $account.location
        config   = Get-Hash (Select-Props $p 'publicNetworkAccess', 'networkAcls', 'disableLocalAuth', 'allowProjectManagement',
            'customSubDomainName', 'encryption', 'restrictOutboundNetworkAccess', 'networkInjections', 'userOwnedStorage')
        identity = Get-Hash $account.identity
    }
    $projects = Get-All "$arm$accountId/projects?api-version=2025-06-01" $arm
    $snapshot.arm.projects = @(foreach ($proj in $projects) {
            [ordered]@{ name = ($proj.name -split '/')[-1]; config = Get-Hash (Select-Props $proj.properties 'displayName', 'description', 'isDefault')
                identity = Get-Hash $proj.identity; endpoint = $proj.properties.endpoints.'AI Foundry API' }
        })
    $snapshot.arm.deployments = @(foreach ($d in (Get-All "$arm$accountId/deployments?api-version=2025-06-01" $arm)) {
            [ordered]@{ name = $d.name; model = $d.properties.model.name; version = $d.properties.model.version; sku = $d.sku.name
                capacity = $d.sku.capacity; config = Get-Hash (Select-Props $d.properties 'raiPolicyName', 'versionUpgradeOption') }
        })
    $connections = [ordered]@{}
    foreach ($proj in $snapshot.arm.projects) {
        $connections[$proj.name] = @(foreach ($c in (Get-All "$arm$accountId/projects/$($proj.name)/connections?api-version=2025-06-01" $arm)) {
                $cp = $c.properties
                [ordered]@{ name = $c.name; category = $cp.category; authType = $cp.authType; target = $cp.target
                    config = Get-Hash (Select-Props $cp 'category', 'authType', 'target', 'isSharedToAll', 'metadata', 'group') }
            })
    }
    $snapshot.arm.connections = $connections

    # Foundry data plane: agents (every version) and toolboxes, per existing project.
    foreach ($proj in $snapshot.arm.projects) {
        $endpoint = $proj.endpoint
        if (-not $endpoint) { $endpoint = "https://$($p.customSubDomainName).services.ai.azure.com/api/projects/$($proj.name)" }
        $endpoint = $endpoint.TrimEnd('/')
        $agents = @(foreach ($a in (Get-All "$endpoint/agents?api-version=v1" 'https://ai.azure.com')) {
                $versions = Get-All "$endpoint/agents/$([uri]::EscapeDataString($a.name))/versions?api-version=v1" 'https://ai.azure.com'
                [ordered]@{ name = $a.name; latest = $a.versions.latest.version
                    versions = @($versions | Sort-Object { [int]$_.version } | ForEach-Object {
                            [ordered]@{ version = $_.version; definition = Get-Hash $_.definition } })
                }
            })
        $toolboxes = @(foreach ($t in (Get-All "$endpoint/toolboxes?api-version=v1" 'https://ai.azure.com')) {
                [ordered]@{ name = $t.name; defaultVersion = $t.default_version }
            })
        $snapshot.foundry[$proj.name] = [ordered]@{ agents = $agents; toolboxes = $toolboxes }
    }
}

# --- Search ------------------------------------------------------------------------------------------------
if ($searchId) {
    $svc = Invoke-Get "$arm${searchId}?api-version=2025-05-01" $arm
    $sp = $svc.properties
    $snapshot.arm.searchService = [ordered]@{ id = $searchId.ToLowerInvariant(); sku = $svc.sku.name
        config = Get-Hash (Select-Props $sp 'replicaCount', 'partitionCount', 'publicNetworkAccess', 'networkRuleSet',
            'authOptions', 'disableLocalAuth', 'semanticSearch', 'hostingMode')
    }
    $searchHost = "https://$(($searchId -split '/')[-1]).search.windows.net"
    $api = 'api-version=2026-08-01-preview'
    $sr = 'https://search.azure.com'
    $snapshot.search.indexes = @(foreach ($i in (Get-All "$searchHost/indexes?$api&`$select=name" $sr)) {
            $full = Invoke-Get "$searchHost/indexes('$($i.name)')?$api" $sr
            [ordered]@{ name = $i.name; definition = Get-Hash (Select-Props $full 'fields', 'vectorSearch', 'semantic', 'scoringProfiles') }
        })
    $snapshot.search.knowledgeSources = @(foreach ($k in (Get-All "$searchHost/knowledgesources?$api" $sr)) {
            [ordered]@{ name = $k.name; etag = $k.'@odata.etag' } })
    $snapshot.search.knowledgeBases = @(foreach ($k in (Get-All "$searchHost/knowledgebases?$api" $sr)) {
            [ordered]@{ name = $k.name; etag = $k.'@odata.etag' } })
}

# --- Fabric ------------------------------------------------------------------------------------------------
if ($capacityId) {
    $cap = Invoke-Get "$arm${capacityId}?api-version=2023-11-01" $arm
    $snapshot.arm.fabricCapacity = [ordered]@{ id = $capacityId.ToLowerInvariant(); sku = $cap.sku.name
        admins = Get-Hash $cap.properties.administration; stateAtSnapshot = $cap.properties.state
    }
    $fab = 'https://api.fabric.microsoft.com'
    $capacityName = ($capacityId -split '/')[-1]
    $fabricCapacity = (Get-All "$fab/v1/capacities" $fab) | Where-Object { $_.displayName -eq $capacityName } | Select-Object -First 1
    $workspaces = Get-All "$fab/v1/workspaces" $fab
    $onCapacity = @($workspaces | Where-Object { $fabricCapacity -and $_.capacityId -eq $fabricCapacity.id })
    $snapshot.fabric.workspaces = @(foreach ($w in $onCapacity) {
            $items = Get-All "$fab/v1/workspaces/$($w.id)/items" $fab
            [ordered]@{ id = $w.id; name = $w.displayName
                items = @($items | Sort-Object id | ForEach-Object { [ordered]@{ id = $_.id; name = $_.displayName; type = $_.type } })
            }
        })
}

# --- Entra (tenant-wide) -----------------------------------------------------------------------------------
$graph = 'https://graph.microsoft.com'
$appSelect = 'id,appId,displayName,identifierUris,requiredResourceAccess,web,spa,publicClient,api,passwordCredentials,keyCredentials'
$apps = Get-All "$graph/v1.0/applications?`$select=$appSelect&`$top=999" $graph
$appRows = foreach ($a in $apps) {
    [ordered]@{ id = $a.id; displayName = $a.displayName
        config = Get-Hash ([ordered]@{ uris = $a.identifierUris; rra = $a.requiredResourceAccess; web = $a.web.redirectUris
                spa = $a.spa.redirectUris; api = $a.api; pwd = @($a.passwordCredentials.keyId); cert = @($a.keyCredentials.keyId) })
    }
}
$snapshot.entra.applications = Sort-ById $appRows

$spSelect = 'id,appId,displayName,servicePrincipalType,accountEnabled,appRoleAssignmentRequired,tags,replyUrls'
$sps = Get-All "$graph/v1.0/servicePrincipals?`$select=$spSelect&`$top=999" $graph
$spRows = foreach ($s in $sps) {
    [ordered]@{ id = $s.id; displayName = $s.displayName
        config = Get-Hash ([ordered]@{ enabled = $s.accountEnabled; required = $s.appRoleAssignmentRequired; tags = $s.tags; reply = $s.replyUrls })
    }
}
$snapshot.entra.servicePrincipals = Sort-ById $spRows

$grants = Get-All "$graph/v1.0/oauth2PermissionGrants?`$top=999" $graph
$grantRows = foreach ($g in $grants) {
    [ordered]@{ id = $g.id; clientId = $g.clientId; resourceId = $g.resourceId; consentType = $g.consentType; principalId = $g.principalId
        scope = (($g.scope -split ' ') | Where-Object { $_ } | Sort-Object) -join ' '
    }
}
$snapshot.entra.permissionGrants = Sort-ById $grantRows

if (-not $OutFile) {
    $dir = Join-Path $env:TEAM_WORK_DIR 'inventory'
    [void][IO.Directory]::CreateDirectory($dir)
    $OutFile = Join-Path $dir ("{0}-{1}.json" -f $Label, (Get-Date).ToString('yyyyMMdd-HHmmss'))
}
$snapshot | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $OutFile -Encoding utf8

$agentCount = 0; $toolboxCount = 0
foreach ($proj in $snapshot.foundry.Values) { $agentCount += @($proj.agents).Count; $toolboxCount += @($proj.toolboxes).Count }
Write-Host ("Inventory '{0}' written to {1}" -f $Label, $OutFile)
Write-Host ("  resources {0}, role assignments {1}, projects {2}, deployments {3}, agents {4}, toolboxes {5}" -f `
        $snapshot.arm.resources.Count, $snapshot.arm.roleAssignments.Count, @($snapshot.arm.projects).Count,
    @($snapshot.arm.deployments).Count, $agentCount, $toolboxCount)
Write-Host ("  indexes {0}, workspaces {1}, applications {2}, service principals {3}, grants {4}" -f `
        @($snapshot.search.indexes).Count, @($snapshot.fabric.workspaces).Count, $snapshot.entra.applications.Count,
    $snapshot.entra.servicePrincipals.Count, $snapshot.entra.permissionGrants.Count)
$OutFile
