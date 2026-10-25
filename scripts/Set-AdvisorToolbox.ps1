<#
.SYNOPSIS
    Publishes an advisor toolbox: Work IQ (optionally with the Fabric IQ portfolio data agent), or with
    -FabricOnly the Fabric IQ portfolio data agent alone.

.DESCRIPTION
    PowerShell port of scripts/setup_toolbox.py that needs only the Azure CLI:
      1. Grants the toolbox app's delegated Work IQ admin consent (tenant admin).
      2. Validates the Work IQ connection and reads its OAuth redirect URL.
      3. Adds that redirect URL to the toolbox app and records it in toolbox.auto.tfvars.
      4. Creates a toolbox version when the desired tools differ from the default version,
         and promotes it.
    -WorkIqMode Mcp (default) exposes Work IQ's granular MCP tools through a custom OAuth MCP connection
    (needs a public-network Foundry account). -AllowedTools is the toolbox allowlist: the default is
    read-only. -WorkIqMode A2A uses the work_iq_preview tool over a RemoteA2A connection instead.
    Set-WorkIqConnection.ps1 creates either connection.
    -IncludeFabric adds the Fabric IQ portfolio data agent when FABRIC_DATA_AGENT_MCP_URL and
    FABRIC_IQ_CONNECTION_ID are set in .env.local (scripts/Set-FabricIqConnection.ps1 sets them).
    -FabricOnly skips steps 1-3 and publishes a toolbox whose only tool is the Fabric IQ portfolio data
    agent (default name advisor-fabric-check); it fails when either of those values is missing.
    -WebIqOnly publishes a toolbox whose only tool is Microsoft Web IQ (web_iq_preview) over the project's
    Web IQ connection (WEBIQ_CONNECTION_NAME, default WebIQ; scripts/Set-WebIqConnection.ps1 creates it).
    Default name team-webiq.
    -FoundryIqOnly publishes a toolbox whose only tool is the Foundry IQ knowledge base's MCP endpoint
    (knowledge_base_retrieve) over its project connection (FOUNDRY_IQ_MCP_URL and FOUNDRY_IQ_CONNECTION_ID;
    scripts/Set-FoundryIqKnowledge.ps1 sets them). Default name advisor-foundryiq.
    Examples:
      .\Set-AdvisorToolbox.ps1 -ToolboxName advisor-workiq
      .\Set-AdvisorToolbox.ps1 -ToolboxName advisor-governance -AllowedTools fetch,create_entity `
          -Description 'Demo-only Work IQ governance probe: shows tenant policy denying writes.'
      .\Set-AdvisorToolbox.ps1 -FabricOnly -ToolboxName advisor-fabric-check
      .\Set-AdvisorToolbox.ps1 -WebIqOnly
      .\Set-AdvisorToolbox.ps1 -FoundryIqOnly
#>
[CmdletBinding()]
param(
    [ValidateSet('A2A', 'Mcp')][string]$WorkIqMode = 'Mcp',
    [string]$WorkIqConnectionName = '',
    [string]$ToolboxName = '',
    [string[]]$AllowedTools = @('retrieve', 'fetch', 'fetch_blob', 'call_function', 'get_schema', 'search_paths', 'ask'),
    [switch]$IncludeFabric,
    [switch]$FabricOnly,
    [switch]$WebIqOnly,
    [switch]$FoundryIqOnly,
    [string]$Description = 'Advisor team tools: Work IQ MCP with a read-only allowlist.'
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
. "$PSScriptRoot\AdvisorCommon.ps1"
$repoRoot = Split-Path $PSScriptRoot -Parent
$tf = Join-Path $repoRoot 'terraform'
function Get-TfOutput([string]$Name) { (terraform "-chdir=$tf" output -raw -no-color $Name).Trim() }
function Get-Token([string]$Resource) { az account get-access-token --resource $Resource --query accessToken -o tsv }
if (@($FabricOnly, $WebIqOnly, $FoundryIqOnly | Where-Object { $_ }).Count -gt 1) { throw 'Use only one of -FabricOnly, -WebIqOnly and -FoundryIqOnly.' }
$iqOnly = $FabricOnly -or $WebIqOnly -or $FoundryIqOnly
if ($FoundryIqOnly) {
    $missingSettings = @('FOUNDRY_IQ_MCP_URL', 'FOUNDRY_IQ_CONNECTION_ID' | Where-Object { -not [Environment]::GetEnvironmentVariable($_) })
    if ($missingSettings) { throw "-FoundryIqOnly needs $($missingSettings -join ' and ') in .env.local. Run scripts\Set-FoundryIqKnowledge.ps1 first." }
    if (-not $PSBoundParameters.ContainsKey('Description')) { $Description = 'Foundry IQ: the firm knowledge base (knowledge_base_retrieve).' }
}
if ($WebIqOnly -and -not $PSBoundParameters.ContainsKey('Description')) {
    $Description = 'Web IQ: public web and news grounding. Public topics only; never client data.'
}

if ($FabricOnly) {
    $missingSettings = @('FABRIC_DATA_AGENT_MCP_URL', 'FABRIC_IQ_CONNECTION_ID' | Where-Object { -not [Environment]::GetEnvironmentVariable($_) })
    if ($missingSettings) {
        throw "-FabricOnly needs $($missingSettings -join ' and ') in .env.local. Run fabric\Deploy-FabricPortfolio.ps1 (data agent MCP URL), then scripts\Set-FabricIqConnection.ps1 (Fabric IQ connection)."
    }
    if (-not $PSBoundParameters.ContainsKey('Description')) {
        $Description = 'Advisor team Fabric IQ: only the Fabric IQ portfolio data agent.'
    }
}

$workIqEndpoint = 'https://workiq.svc.cloud.microsoft/mcp'
$workIqScope = 'api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask'
$project = Get-TfOutput 'foundry_project_endpoint'

$tenantId = Get-TfOutput 'tenant_id'
$toolboxName = if ($ToolboxName) { $ToolboxName } elseif ($FabricOnly) { 'advisor-fabric-check' }
    elseif ($WebIqOnly) { 'team-webiq' } elseif ($FoundryIqOnly) { 'team-foundryiq' } else { Get-TfOutput 'workiq_toolbox_name' }
# The harness runtime, not this flag, enforces approvals; Work IQ tenant policy still applies to every call.
$approval = 'never'
$allowedTools = @($AllowedTools | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

if (-not $iqOnly) {
    $clientId = Get-TfOutput 'workiq_toolbox_client_id'
    $appObjectId = Get-TfOutput 'workiq_toolbox_application_object_id'

    # 1. Delegated admin consent for the toolbox app's requested permissions.
    Write-Output 'Granting the toolbox app delegated Work IQ admin consent.'
    $graph = 'https://graph.microsoft.com/v1.0'
    $app = (az rest --method get --url "$graph/applications?`$filter=appId eq '$clientId'&`$select=requiredResourceAccess" -o json | ConvertFrom-Json).value[0]
    $caller = (az rest --method get --url "$graph/servicePrincipals?`$filter=appId eq '$clientId'&`$select=id" -o json | ConvertFrom-Json).value[0]
    foreach ($access in $app.requiredResourceAccess) {
        $resource = (az rest --method get --url "$graph/servicePrincipals?`$filter=appId eq '$($access.resourceAppId)'&`$select=id,oauth2PermissionScopes" -o json | ConvertFrom-Json).value[0]
        if (-not $resource) { throw "Service principal for $($access.resourceAppId) is missing." }
        $requested = @($access.resourceAccess | Where-Object type -eq 'Scope' | ForEach-Object {
            $id = $_.id; ($resource.oauth2PermissionScopes | Where-Object id -eq $id).value
        })
        $grants = @((az rest --method get --url "$graph/oauth2PermissionGrants?`$filter=clientId eq '$($caller.id)' and resourceId eq '$($resource.id)'" -o json | ConvertFrom-Json).value |
            Where-Object consentType -eq 'AllPrincipals')
        $bodyFile = Join-Path $env:TEAM_WORK_DIR 'grant.json'
        if ($grants.Count -eq 0) {
            @{ clientId = $caller.id; resourceId = $resource.id; consentType = 'AllPrincipals'; scope = ($requested -join ' ') } |
                ConvertTo-Json | Set-Content -Path $bodyFile -Encoding ascii
            az rest --method post --url "$graph/oauth2PermissionGrants" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
        } else {
            $current = @($grants[0].scope -split ' ' | Where-Object { $_ })
            $missing = @($requested | Where-Object { $current -notcontains $_ })
            if ($missing.Count -gt 0) {
                @{ scope = (($current + $missing) | Sort-Object -Unique) -join ' ' } | ConvertTo-Json | Set-Content -Path $bodyFile -Encoding ascii
                az rest --method patch --url "$graph/oauth2PermissionGrants/$($grants[0].id)" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
            }
        }
        Write-Output "  consent: $($requested -join ', ')"
    }

    # 2. Validate the Work IQ connection and read its OAuth redirect URL.
    if (-not $WorkIqConnectionName) { $WorkIqConnectionName = Get-ProjectConnectionName $(if ($WorkIqMode -eq 'Mcp') { 'work-iq-mcp' } else { 'work-iq' }) }
    & "$PSScriptRoot\Set-WorkIqConnection.ps1" -Kind $WorkIqMode -ConnectionName $WorkIqConnectionName | Write-Output
    $connectionId = (Get-TfOutput 'foundry_project_id') + "/connections/$WorkIqConnectionName"
    $conn = az rest --method get --url "https://management.azure.com${connectionId}?api-version=2025-04-01-preview" -o json | ConvertFrom-Json
    $p = $conn.properties
    if ($p.error) { throw "Foundry reports a Work IQ connection error: $($p.error)" }
    $expectedCategory = if ($WorkIqMode -eq 'A2A') { 'RemoteA2A' } else { 'RemoteTool' }
    if ($p.category -ne $expectedCategory -or $p.authType -ne 'OAuth2' -or (@($p.scopes) -notcontains $workIqScope)) {
        throw "The Work IQ connection does not match the expected $WorkIqMode OAuth configuration."
    }
    $redirect = @($p.oauthRedirectUrl, $p.redirectUrl) | Where-Object { $_ } | Select-Object -First 1
    if (-not ($redirect -match '^https://[^/?#]+/')) { throw 'Foundry did not return a valid OAuth redirect URL.' }

    # 3. Register the redirect on the toolbox app and persist it for Terraform.
    $web = (az rest --method get --url "$graph/applications/${appObjectId}?`$select=web" -o json | ConvertFrom-Json).web
    $redirects = @($web.redirectUris)
    if ($redirects -notcontains $redirect) {
        $bodyFile = Join-Path $env:TEAM_WORK_DIR 'redirect.json'
        @{ web = @{ redirectUris = @($redirects + $redirect) } } | ConvertTo-Json -Depth 4 | Set-Content -Path $bodyFile -Encoding ascii
        az rest --method patch --url "$graph/applications/$appObjectId" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
        Write-Output '  redirect URI added to the toolbox app'
    }
    "workiq_toolbox_redirect_uri = $($redirect | ConvertTo-Json)" | Set-Content -Path (Join-Path $tf 'toolbox.auto.tfvars') -Encoding ascii
}

# 4. Desired toolbox tools.
if ($WebIqOnly) {
    $webIqConnection = if ($env:WEBIQ_CONNECTION_NAME) { $env:WEBIQ_CONNECTION_NAME } else { Get-ProjectConnectionName 'WebIQ' }
    $webIqId = (Get-TfOutput 'foundry_project_id') + "/connections/$webIqConnection"
    $null = az rest --method get --url "https://management.azure.com${webIqId}?api-version=2025-06-01" -o none 2>$null
    if ($LASTEXITCODE -ne 0) { throw "The project has no Web IQ connection named $webIqConnection; run scripts\Set-WebIqConnection.ps1 first." }
    $tools = @([ordered]@{ type = 'web_iq_preview'; name = 'webiq'; server_label = 'webiq'; project_connection_id = $webIqId; require_approval = $approval })
} elseif ($FoundryIqOnly) {
    $tools = @([ordered]@{
        type = 'mcp'; server_label = 'firm_knowledge'; server_url = $env:FOUNDRY_IQ_MCP_URL
        project_connection_id = $env:FOUNDRY_IQ_CONNECTION_ID; allowed_tools = @('knowledge_base_retrieve'); require_approval = $approval
    })
} elseif ($FabricOnly) {
    $tools = @()
} elseif ($WorkIqMode -eq 'A2A') {
    $tools = @([ordered]@{ type = 'work_iq_preview'; name = 'workiq'; project_connection_id = $connectionId })
} else {
    $tools = @(
        [ordered]@{
            type                  = 'mcp'
            server_label          = 'workiq'
            server_url            = $workIqEndpoint
            project_connection_id = $connectionId
            allowed_tools         = $allowedTools
            require_approval      = $approval
        }
    )
}
if ($FabricOnly -or ($IncludeFabric -and $env:FABRIC_DATA_AGENT_MCP_URL -and $env:FABRIC_IQ_CONNECTION_ID)) {
    $tools += [ordered]@{
        type                  = 'fabric_iq_preview'
        name                  = 'fabric_portfolio'
        server_label          = 'fabric_portfolio'
        server_url            = $env:FABRIC_DATA_AGENT_MCP_URL
        project_connection_id = $env:FABRIC_IQ_CONNECTION_ID
    }
}
function Get-ToolSignature($items) {
    (@($items) | ForEach-Object {
        "$($_.type)|$($_.name)|$($_.project_connection_id)|$($_.server_url)|$(@($_.allowed_tools) -join ',')|$($_.require_approval)"
    } | Sort-Object) -join ';'
}

$token = Get-Token 'https://ai.azure.com'
$headers = @{ Authorization = "Bearer $token" }
$base = "$project/toolboxes/$toolboxName"
$existing = $null
try { $existing = Invoke-RestMethod -Method Get -Uri "${base}?api-version=v1" -Headers $headers } catch {
    if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw }
}
if ($existing) {
    $current = Invoke-RestMethod -Method Get -Uri "$base/versions/$($existing.default_version)?api-version=v1" -Headers $headers
    if ((Get-ToolSignature $current.tools) -eq (Get-ToolSignature $tools)) {
        Write-Output "Reusing toolbox $toolboxName version $($existing.default_version)."
        return
    }
}
$body = @{ description = $Description; tools = $tools } |
    ConvertTo-Json -Depth 8
$version = Invoke-RestMethod -Method Post -Uri "$base/versions?api-version=v1" -Headers $headers -ContentType 'application/json' -Body $body
if ($existing) {
    Invoke-RestMethod -Method Patch -Uri "${base}?api-version=v1" -Headers $headers -ContentType 'application/json' `
        -Body (@{ default_version = $version.version } | ConvertTo-Json) | Out-Null
}
Write-Output "Published toolbox $toolboxName version $($version.version) with $($tools.Count) tool(s); default updated."
Write-Output 'Each user still completes the per-user OAuth consent link the first time a tool needs it.'
