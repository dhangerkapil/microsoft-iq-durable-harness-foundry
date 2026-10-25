<#
.SYNOPSIS
    Creates (idempotently) the Foundry project connection for the Fabric IQ portfolio data agent.

.DESCRIPTION
    -AuthType OAuth2 (default, required for hosted agents): a bring-your-own Entra app
    "advisor-team-fabric-iq[-<TEAM_NAME_SUFFIX>]" with the delegated Power BI permission DataAgent.Execute.All and tenant
    admin consent. Each user completes a one-time consent link the first time the agent calls Fabric;
    after that Fabric runs every query with that user's own workspace permissions. The app secret is
    created in memory and stored only in the Foundry connection.

    -AuthType UserEntraToken: passes the caller's Entra token through On-Behalf-Of. This only works when
    the end user calls the toolbox directly; a hosted agent calls the toolbox with its own identity.

    Records FABRIC_IQ_CONNECTION_ID and FABRIC_IQ_CONNECTION_NAME in .env.local for Set-AdvisorToolbox.ps1.
#>
[CmdletBinding()]
param(
    # Default: fabric-portfolio (plus this deployment's suffix in a shared Foundry account).
    [string]$ConnectionName = '',
    [ValidateSet('OAuth2', 'UserEntraToken')][string]$AuthType = 'OAuth2',
    # Default: advisor-team-fabric-iq, plus the deployment's TEAM_NAME_SUFFIX.
    [string]$AppName,
    [int]$SecretLifetimeDays = 180,
    [switch]$Recreate
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
. "$PSScriptRoot\AdvisorCommon.ps1"
if (-not $ConnectionName) { $ConnectionName = Get-ProjectConnectionName 'fabric-portfolio' }
if (-not $AppName) { $AppName = Get-TenantScopedName 'advisor-team-fabric-iq' }
if (-not $env:FABRIC_DATA_AGENT_MCP_URL) { throw 'FABRIC_DATA_AGENT_MCP_URL is missing; run fabric\Deploy-FabricPortfolio.ps1 first.' }
$tf = Join-Path (Split-Path $PSScriptRoot -Parent) 'terraform'
$projectId = (terraform "-chdir=$tf" output -raw -no-color foundry_project_id).Trim()
$connectionId = "$projectId/connections/$ConnectionName"
$url = "https://management.azure.com${connectionId}?api-version=2025-04-01-preview"
$arm = @{ Authorization = "Bearer $(Get-AzToken 'https://management.azure.com')" }
$graph = 'https://graph.microsoft.com/v1.0'
$powerBiAppId = '00000009-0000-0000-c000-000000000000'
$scope = 'https://analysis.windows.net/powerbi/api/DataAgent.Execute.All'

function Get-Connection {
    try { Invoke-RestMethod -Method Get -Uri $url -Headers $arm } catch {
        if ($_.Exception.Response.StatusCode.value__ -eq 404) { return $null }
        throw
    }
}

$connection = Get-Connection
$wrongKind = $connection -and ($connection.properties.authType -ne $AuthType -or
    $connection.properties.target -ne $env:FABRIC_DATA_AGENT_MCP_URL)
if ($connection -and ($Recreate -or $wrongKind -or $connection.properties.error)) {
    Write-Output "Replacing connection $ConnectionName ($($connection.properties.authType); error: $($connection.properties.error))."
    Invoke-RestMethod -Method Delete -Uri $url -Headers $arm | Out-Null
    $connection = $null
}

if ($AuthType -eq 'UserEntraToken') {
    if (-not $connection) {
        $body = @{ properties = [ordered]@{
                category = 'RemoteTool'; authType = 'UserEntraToken'; target = $env:FABRIC_DATA_AGENT_MCP_URL
                audience = 'https://analysis.windows.net/powerbi/api'; isSharedToAll = $true } } | ConvertTo-Json -Depth 5
        Invoke-RestMethod -Method Put -Uri $url -Headers $arm -ContentType 'application/json' -Body $body | Out-Null
        $connection = Get-Connection
    }
} else {
    # Entra app with the delegated DataAgent.Execute.All permission and tenant-wide admin consent.
    $app = az ad app list --display-name $AppName --query '[0]' -o json | ConvertFrom-Json
    if (-not $app) {
        $app = az ad app create --display-name $AppName --sign-in-audience AzureADMyOrg -o json | ConvertFrom-Json
        Write-Output "Created app registration $AppName ($($app.appId))."
    }
    $appSp = az ad sp list --filter "appId eq '$($app.appId)'" --query '[0]' -o json | ConvertFrom-Json
    if (-not $appSp) { $appSp = az ad sp create --id $app.appId -o json | ConvertFrom-Json }
    $powerBi = (az rest --method get --url "$graph/servicePrincipals?`$filter=appId eq '$powerBiAppId'&`$select=id,oauth2PermissionScopes" -o json | ConvertFrom-Json).value[0]
    $scopeId = ($powerBi.oauth2PermissionScopes | Where-Object value -eq 'DataAgent.Execute.All').id
    $bodyFile = Join-Path $env:TEAM_WORK_DIR 'fabric-app.json'
    @{ requiredResourceAccess = @(@{ resourceAppId = $powerBiAppId; resourceAccess = @(@{ id = $scopeId; type = 'Scope' }) }) } |
        ConvertTo-Json -Depth 6 | Set-Content $bodyFile -Encoding ascii
    az rest --method patch --url "$graph/applications/$($app.id)" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
    $grant = @((az rest --method get --url "$graph/oauth2PermissionGrants?`$filter=clientId eq '$($appSp.id)' and resourceId eq '$($powerBi.id)'" -o json | ConvertFrom-Json).value |
        Where-Object consentType -eq 'AllPrincipals')
    if (-not $grant) {
        @{ clientId = $appSp.id; resourceId = $powerBi.id; consentType = 'AllPrincipals'; scope = 'DataAgent.Execute.All' } |
            ConvertTo-Json | Set-Content $bodyFile -Encoding ascii
        az rest --method post --url "$graph/oauth2PermissionGrants" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
        Write-Output 'Granted tenant-wide consent for DataAgent.Execute.All.'
    }

    if (-not $connection) {
        $endDate = (Get-Date).ToUniversalTime().AddDays($SecretLifetimeDays).ToString('yyyy-MM-dd')
        $secret = az ad app credential reset --id $app.appId --append --display-name "foundry-$ConnectionName" `
            --end-date $endDate --query password -o tsv --only-show-errors
        $login = "https://login.microsoftonline.com/$env:AZURE_TENANT_ID/oauth2/v2.0"
        $body = @{ properties = [ordered]@{
                authType         = 'OAuth2'; category = 'RemoteTool'; group = 'GenericProtocol'
                target           = $env:FABRIC_DATA_AGENT_MCP_URL; isSharedToAll = $true
                authorizationUrl = "$login/authorize"; tokenUrl = "$login/token"; refreshUrl = "$login/token"
                scopes           = @($scope, 'offline_access')
                credentials      = @{ clientId = $app.appId; clientSecret = $secret }
                metadata         = @{ oAuthProvider = 'custom'; type = 'custom_MCP' } } } | ConvertTo-Json -Depth 6
        $secret = $null
        Invoke-RestMethod -Method Put -Uri $url -Headers $arm -ContentType 'application/json' -Body $body | Out-Null
        $body = $null
        Write-Output "Created OAuth2 connection $ConnectionName."
        $connection = Get-Connection
    }
    $redirect = @($connection.properties.oauthRedirectUrl, $connection.properties.redirectUrl) | Where-Object { $_ } | Select-Object -First 1
    if (-not ($redirect -match '^https://[^/?#]+/')) { throw 'Foundry did not return an OAuth redirect URL for the Fabric connection.' }
    $web = (az rest --method get --url "$graph/applications/$($app.id)?`$select=web" -o json | ConvertFrom-Json).web
    if (@($web.redirectUris) -notcontains $redirect) {
        @{ web = @{ redirectUris = @(@($web.redirectUris) + $redirect) } } | ConvertTo-Json -Depth 4 | Set-Content $bodyFile -Encoding ascii
        az rest --method patch --url "$graph/applications/$($app.id)" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
        Write-Output 'Added the connection redirect URL to the Fabric app.'
    }
    Grant-OidcAdminConsent -ClientId $app.appId
}

if ($connection.properties.error) { throw "Foundry reports a Fabric IQ connection error: $($connection.properties.error)" }
Set-EnvLocalValue @{ FABRIC_IQ_CONNECTION_ID = $connectionId; FABRIC_IQ_CONNECTION_NAME = $ConnectionName }
Write-Output "Fabric IQ connection ready ($AuthType): $connectionId"
