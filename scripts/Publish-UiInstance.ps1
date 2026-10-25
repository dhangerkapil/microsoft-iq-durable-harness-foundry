<#
.SYNOPSIS
    Deploys one more chat UI for another hosted agent in the same Foundry project, next to the Terraform-managed UI,
    without changing it: for a candidate agent, or a second agent such as a demo's successor.

.DESCRIPTION
    Additive only. Reuses the Terraform-managed Container Apps environment, the UI's managed identity, the UI API app
    registration and the Cosmos DB database. Creates:
      - a Cosmos DB container for this UI's sessions, readable and writable only by the UI identity;
      - a browser app registration (SPA) pre-authorized for the UI API scope, with tenant admin consent;
      - a Container App running the UI image with UI_AGENT_NAME set to -AgentName.
    The Terraform-managed UI, its app registrations and its sessions are not modified. Remove this instance with
    -Remove (deletes the Container App, the browser app registration and the Cosmos container).

.EXAMPLE
    .\scripts\Publish-UiInstance.ps1 -AgentName advisor-team-solo
    .\scripts\Publish-UiInstance.ps1 -AgentName advisor-team-solo -Remove
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-z0-9][a-z0-9-]{1,62}$')][string]$AgentName,
    # Container App name; default ui-<agent name>, cut to 32 characters.
    [ValidatePattern('^(|[a-z][a-z0-9-]{1,30}[a-z0-9])$')][string]$Name = '',
    [string]$ImageUri,
    [string]$Tag = (Get-Date -Format 'yyyyMMdd-HHmm'),
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
. "$PSScriptRoot\AdvisorCommon.ps1"
$repoRoot = Split-Path $PSScriptRoot -Parent
$tf = Join-Path $repoRoot 'terraform'
function Get-TfOutput([string]$Output) { (terraform "-chdir=$tf" output -raw -no-color $Output).Trim() }
if (-not $Name) { $Name = ("ui-$AgentName" -replace '[^a-z0-9-]', '').Substring(0, [Math]::Min(32, "ui-$AgentName".Length)).TrimEnd('-') }
$graph = 'https://graph.microsoft.com/v1.0'

$rg = Get-TfOutput 'resource_group_name'
$liveUi = Get-TfOutput 'ui_app_name'
$apiClientId = Get-TfOutput 'ui_api_client_id'
$project = Get-TfOutput 'foundry_project_endpoint'
$registry = Get-TfOutput 'registry_name'
$tenantId = Get-TfOutput 'tenant_id'
$live = az containerapp show -n $liveUi -g $rg -o json | ConvertFrom-Json
$liveEnv = @{}; foreach ($e in $live.properties.template.containers[0].env) { $liveEnv[$e.name] = $e.value }
$identityId = @($live.identity.userAssignedIdentities.PSObject.Properties.Name)[0]
$identity = az identity show --ids $identityId -o json | ConvertFrom-Json
$cosmosAccount = ([uri]$liveEnv['COSMOS_ENDPOINT']).Host.Split('.')[0]
$database = $liveEnv['COSMOS_DATABASE']
$container = 'uiSessions' + (Get-Culture).TextInfo.ToTitleCase(($AgentName -replace '-', ' ')).Replace(' ', '')
$browserName = "$Name-browser"

if ($Remove) {
    az containerapp delete -n $Name -g $rg --yes -o none 2>$null
    foreach ($appId in @(az ad app list --display-name $browserName --query '[].appId' -o tsv)) { if ($appId) { az ad app delete --id $appId } }
    az cosmosdb sql container delete -a $cosmosAccount -g $rg -d $database -n $container --yes -o none 2>$null
    Write-Output "Removed UI instance $Name (Container App, browser app registration $browserName, Cosmos container $container)."
    return
}

# 1. Image.
if (-not $ImageUri) {
    $log = Join-Path $env:TEAM_WORK_DIR 'acr-ui-instance-build.log'
    Push-Location $repoRoot
    try { az acr build --registry $registry --image "advisor-ui:$Tag" --platform linux/amd64 --file ui/Dockerfile . *> $log } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { Get-Content $log | Select-Object -Last 30; throw "UI image build failed (full log: $log)." }
    $digest = (az acr repository show --name $registry --image "advisor-ui:$Tag" --query digest -o tsv).Trim()
    $ImageUri = "$registry.azurecr.io/advisor-ui@$digest"
    Write-Output "Built advisor-ui:$Tag ($digest)"
}

# 2. This UI's sessions: its own Cosmos container, readable and writable only by the UI identity.
if (-not (az cosmosdb sql container show -a $cosmosAccount -g $rg -d $database -n $container --query name -o tsv 2>$null)) {
    az cosmosdb sql container create -a $cosmosAccount -g $rg -d $database -n $container --partition-key-path /owner --ttl 2592000 -o none
    Write-Output "Created Cosmos container $container."
}
$scope = "/dbs/$database/colls/$container"
$assigned = az cosmosdb sql role assignment list -a $cosmosAccount -g $rg --query "[?principalId=='$($identity.principalId)' && ends_with(scope, '$scope')].id" -o tsv
if (-not $assigned) {
    az cosmosdb sql role assignment create -a $cosmosAccount -g $rg --role-definition-id 00000000-0000-0000-0000-000000000002 --scope $scope --principal-id $identity.principalId -o none
    Write-Output "Granted the UI identity data access to $container."
}

# 3. The Container App (created first: the browser app needs its URL).
$envVars = @(
    "ENABLE_SENSITIVE_DATA=false", "AZURE_CLIENT_ID=$($identity.clientId)", "UI_TENANT_ID=$tenantId",
    "UI_API_CLIENT_ID=$apiClientId", "UI_BROWSER_CLIENT_ID=$($liveEnv['UI_BROWSER_CLIENT_ID'])", "UI_ORIGIN=https://placeholder.invalid",
    "COSMOS_ENDPOINT=$($liveEnv['COSMOS_ENDPOINT'])", "COSMOS_DATABASE=$database", "UI_COSMOS_CONTAINER=$container",
    "AZURE_AI_PROJECT_ENDPOINT=$project", "UI_AGENT_NAME=$AgentName")
$existing = az containerapp show -n $Name -g $rg -o json 2>$null | ConvertFrom-Json
if (-not $existing) {
    az containerapp create -n $Name -g $rg --environment $live.properties.environmentId --workload-profile-name Consumption `
        --image $ImageUri --registry-server "$registry.azurecr.io" --registry-identity $identityId --user-assigned $identityId `
        --ingress external --target-port 8080 --transport http --cpu 0.5 --memory 1Gi --min-replicas 1 --max-replicas 2 `
        --env-vars @envVars --tags "workload=advisor-team" "agent=$AgentName" -o none
    if ($LASTEXITCODE -ne 0) { throw "Creating Container App $Name failed." }
    $existing = az containerapp show -n $Name -g $rg -o json | ConvertFrom-Json
} elseif ($existing.properties.template.containers[0].image -ne $ImageUri) {
    # A rerun rolls the instance to the image just built; its settings stay as they are.
    az containerapp update -n $Name -g $rg --image $ImageUri -o none
    if ($LASTEXITCODE -ne 0) { throw "Updating Container App $Name to $ImageUri failed." }
    Write-Output "Updated $Name to $ImageUri."
}
$url = "https://$($existing.properties.configuration.ingress.fqdn)"

# 4. Browser app registration (SPA), pre-authorized for the UI API scope, with admin consent.
$api = (az rest --method get --url "$graph/applications?`$filter=appId eq '$apiClientId'&`$select=id,api" -o json | ConvertFrom-Json).value[0]
$scopeId = @($api.api.oauth2PermissionScopes | Where-Object value -eq 'access_as_user')[0].id
$browser = (az rest --method get --url "$graph/applications?`$filter=displayName eq '$browserName'&`$select=id,appId" -o json | ConvertFrom-Json).value | Select-Object -First 1
$appBody = @{ displayName = $browserName; signInAudience = 'AzureADMyOrg'; spa = @{ redirectUris = @("$url/", "$url/auth.html") }
              requiredResourceAccess = @(@{ resourceAppId = $apiClientId; resourceAccess = @(@{ id = $scopeId; type = 'Scope' }) }) }
$bodyFile = Join-Path $env:TEAM_WORK_DIR 'ui-instance-app.json'
$appBody | ConvertTo-Json -Depth 8 | Set-Content $bodyFile -Encoding utf8
if (-not $browser) {
    $browser = az rest --method post --url "$graph/applications" --body "@$bodyFile" --headers 'Content-Type=application/json' -o json | ConvertFrom-Json
    az ad sp create --id $browser.appId -o none
    Write-Output "Created browser app registration $browserName ($($browser.appId))."
} else {
    az rest --method patch --url "$graph/applications/$($browser.id)" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
}
# Pre-authorize it on the API app, keeping every existing entry.
$pre = @($api.api.preAuthorizedApplications | Where-Object { $_.appId -ne $browser.appId }) + @(@{ appId = $browser.appId; delegatedPermissionIds = @($scopeId) })
@{ api = @{ preAuthorizedApplications = $pre } } | ConvertTo-Json -Depth 8 | Set-Content $bodyFile -Encoding utf8
az rest --method patch --url "$graph/applications/$($api.id)" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
Grant-DelegatedAdminConsent -ClientId $browser.appId

# 5. Point the app at its own origin and browser app, then wait until it is ready.
az containerapp update -n $Name -g $rg --set-env-vars "UI_ORIGIN=$url" "UI_BROWSER_CLIENT_ID=$($browser.appId)" -o none
for ($i = 1; $i -le 60; $i++) {
    try { Invoke-WebRequest -Uri "$url/readyz" -TimeoutSec 20 | Out-Null; Write-Output "UI for $AgentName ready: $url"; return } catch { Start-Sleep -Seconds 10 }
}
throw "UI readiness did not succeed at $url/readyz; check the Container App's logs."
