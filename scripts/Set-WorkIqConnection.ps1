<#
.SYNOPSIS
    Creates (idempotently) a Work IQ project connection for the toolbox and registers its OAuth
    redirect URL on the toolbox app.

.DESCRIPTION
    -Kind Mcp (default): a custom OAuth MCP connection to https://workiq.svc.cloud.microsoft/mcp. The toolbox
    then exposes Work IQ's granular MCP tools (retrieve, fetch, fetch_blob, get_schema, search_paths, ask),
    which the X-ray shows call by call. It needs a public-network Foundry account; a managed-network
    account rejects it ("not a supported target").
    -Kind A2A: a RemoteA2A connection to https://workiq.svc.cloud.microsoft/a2a/ consumed through a toolbox
    tool of type work_iq_preview (one natural-language Work IQ call per question).
    Both use the bring-your-own Entra toolbox app (On-Behalf-Of). This script:
      1. Reuses a healthy connection, or deletes one that reports an error.
      2. Adds a new client secret to the toolbox app (kept in memory only; Foundry stores it).
      3. Creates the connection and verifies Foundry reports no connection error.
      4. Adds the connection's OAuth redirect URL to the toolbox app.
#>
[CmdletBinding()]
param(
    [ValidateSet('Mcp', 'A2A')][string]$Kind = 'Mcp',
    [string]$ConnectionName = '',
    [int]$SecretLifetimeDays = 180,
    [switch]$Recreate
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
. "$PSScriptRoot\AdvisorCommon.ps1"
if (-not $ConnectionName) { $ConnectionName = Get-ProjectConnectionName $(if ($Kind -eq 'Mcp') { 'work-iq-mcp' } else { 'work-iq' }) }
$category = if ($Kind -eq 'Mcp') { 'RemoteTool' } else { 'RemoteA2A' }
$tf = Join-Path (Split-Path $PSScriptRoot -Parent) 'terraform'
function Get-TfOutput([string]$Name) { (terraform "-chdir=$tf" output -raw -no-color $Name).Trim() }

$projectId = Get-TfOutput 'foundry_project_id'
$connectionId = "$projectId/connections/$ConnectionName"
$tenantId = Get-TfOutput 'tenant_id'
$clientId = Get-TfOutput 'workiq_toolbox_client_id'
$appObjectId = Get-TfOutput 'workiq_toolbox_application_object_id'
$url = "https://management.azure.com${connectionId}?api-version=2025-04-01-preview"
$arm = @{ Authorization = "Bearer $(az account get-access-token --resource https://management.azure.com --query accessToken -o tsv)" }

function Get-Connection {
    try { Invoke-RestMethod -Method Get -Uri $url -Headers $arm } catch {
        if ($_.Exception.Response.StatusCode.value__ -eq 404) { return $null }
        throw
    }
}

$connection = Get-Connection
if ($connection -and ($Recreate -or $connection.properties.error)) {
    Write-Output "Deleting connection $ConnectionName (error: $($connection.properties.error))."
    Invoke-RestMethod -Method Delete -Uri $url -Headers $arm | Out-Null
    $connection = $null
}

if (-not $connection) {
    $endDate = (Get-Date).ToUniversalTime().AddDays($SecretLifetimeDays).ToString('yyyy-MM-dd')
    $secret = az ad app credential reset --id $clientId --append --display-name "foundry-$ConnectionName-$($Kind.ToLower())" `
        --end-date $endDate --query password -o tsv --only-show-errors
    if (-not $secret) { throw 'Could not create a client secret on the toolbox app.' }
    $login = "https://login.microsoftonline.com/$tenantId/oauth2/v2.0"
    $properties = [ordered]@{
        authType         = 'OAuth2'
        category         = $category
        isSharedToAll    = $true
        sharedUserList   = @()
        TokenUrl         = "$login/token"
        AuthorizationUrl = "$login/authorize"
        RefreshUrl       = "$login/token"
        Scopes           = @('api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask', 'offline_access')
        Credentials      = @{ ClientId = $clientId; ClientSecret = $secret }
    }
    if ($Kind -eq 'Mcp') {
        $properties.group = 'GenericProtocol'
        $properties.target = 'https://workiq.svc.cloud.microsoft/mcp'
        $properties.metadata = @{ oAuthProvider = 'custom'; type = 'custom_MCP' }
    } else {
        $properties.group = 'ServicesAndApps'
        $properties.target = 'https://workiq.svc.cloud.microsoft/a2a/'
        $properties.metadata = @{ ApiType = 'Azure' }
    }
    $body = @{ properties = $properties } | ConvertTo-Json -Depth 6
    $secret = $null; $properties = $null
    Invoke-RestMethod -Method Put -Uri $url -Headers $arm -ContentType 'application/json' -Body $body | Out-Null
    $body = $null
    Write-Output "Created Work IQ $Kind connection $ConnectionName."
    $connection = Get-Connection
}

$p = $connection.properties
if ($p.error) { throw "Foundry reports a Work IQ connection error: $($p.error)" }
if ($p.category -ne $category) { throw "Connection $ConnectionName is $($p.category), expected $category." }
$redirect = @($p.oauthRedirectUrl, $p.redirectUrl) | Where-Object { $_ } | Select-Object -First 1
if (-not ($redirect -match '^https://[^/?#]+/')) { throw 'Foundry did not return an OAuth redirect URL for the connection.' }

$graph = 'https://graph.microsoft.com/v1.0'
$web = (az rest --method get --url "$graph/applications/${appObjectId}?`$select=web" -o json | ConvertFrom-Json).web
$redirects = @($web.redirectUris)
if ($redirects -notcontains $redirect) {
    $bodyFile = Join-Path $env:TEAM_WORK_DIR 'redirect.json'
    @{ web = @{ redirectUris = @($redirects + $redirect) } } | ConvertTo-Json -Depth 4 | Set-Content -Path $bodyFile -Encoding ascii
    az rest --method patch --url "$graph/applications/$appObjectId" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
    Write-Output 'Added the connection redirect URL to the toolbox app.'
}
Grant-OidcAdminConsent -ClientId $clientId
Write-Output "Work IQ $Kind connection ready: $connectionId"
