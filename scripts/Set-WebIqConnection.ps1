<#
.SYNOPSIS
    Creates (idempotently) the Foundry project connection for Microsoft Web IQ (preview), the public-web source.

.DESCRIPTION
    Web IQ is an MCP server (https://api.microsoft.ai/v3/mcp) that authenticates with an API key in the x-apikey
    header. This script creates the same connection the Foundry portal creates from Tools > Catalog > Web IQ: a
    RemoteTool connection with CustomKeys credentials. The key is read from -ApiKey, else the WEBIQ_API_KEY
    environment variable, else a hidden prompt; it is sent only to Azure Resource Manager and is never written to disk.

    Then run .\scripts\Set-AdvisorToolbox.ps1 -WebIqOnly to publish the advisor-webiq toolbox over it.

    Web IQ is not an Azure online service: queries leave the Microsoft 365 and Azure compliance boundary, so the
    agent sends only public topics (see src/advisor_team/webiq.py, WEB_BOUNDARY_TERMS). Records
    WEBIQ_CONNECTION_NAME in .env.local.

.EXAMPLE
    .\scripts\Set-WebIqConnection.ps1                      # prompts for the key
    .\scripts\Set-WebIqConnection.ps1 -ApiKey $secureKey -Recreate
#>
[CmdletBinding()]
param(
    [securestring]$ApiKey,
    # Default: WebIQ (plus this deployment's suffix in a shared Foundry account).
    [string]$ConnectionName = '',
    [string]$Target = 'https://api.microsoft.ai/v3/mcp',
    # Replace an existing connection (for example, to rotate the key).
    [switch]$Recreate
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
. "$PSScriptRoot\AdvisorCommon.ps1"
if (-not $ConnectionName) { $ConnectionName = Get-ProjectConnectionName 'WebIQ' }
$tf = Join-Path (Split-Path $PSScriptRoot -Parent) 'terraform'
$projectId = (terraform "-chdir=$tf" output -raw -no-color foundry_project_id).Trim()
$url = "https://management.azure.com$projectId/connections/${ConnectionName}?api-version=2025-10-01-preview"
$arm = @{ Authorization = "Bearer $(Get-AzToken 'https://management.azure.com')" }

$existing = try { Invoke-RestMethod -Method Get -Uri $url -Headers $arm } catch {
    if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw }
    $null
}
if ($existing -and -not $Recreate) {
    if ($existing.properties.target -ne $Target -or $existing.properties.authType -ne 'CustomKeys') {
        throw "Connection $ConnectionName exists with another target or auth type; rerun with -Recreate."
    }
    Write-Output "Web IQ connection $ConnectionName already exists (use -Recreate to replace its key)."
    Set-EnvLocalValue @{ WEBIQ_CONNECTION_NAME = $ConnectionName }
    return
}

if (-not $ApiKey -and $env:WEBIQ_API_KEY) { $ApiKey = ConvertTo-SecureString $env:WEBIQ_API_KEY -AsPlainText -Force }
if (-not $ApiKey) { $ApiKey = Read-Host 'Web IQ API key (x-apikey)' -AsSecureString }
$key = [System.Net.NetworkCredential]::new('', $ApiKey).Password
if (-not $key) { throw 'A Web IQ API key is required.' }
$body = @{ properties = [ordered]@{
        category      = 'RemoteTool'
        authType      = 'CustomKeys'
        target        = $Target
        isSharedToAll = $false
        credentials   = @{ keys = @{ 'x-apikey' = $key } }
        # The same catalog entry the Foundry portal records, so the portal shows it as the Web IQ tool.
        metadata      = @{ type = 'catalog_MCP'; toolEntityId = 'azureml://location/eastus/apiCenter/registry-prod-bl/type/tools/objectId/web-iq-mcp/version/1' }
    } } | ConvertTo-Json -Depth 6
try {
    Invoke-RestMethod -Method Put -Uri $url -Headers $arm -ContentType 'application/json' -Body $body | Out-Null
} finally { Remove-Variable key, body -ErrorAction SilentlyContinue }
Write-Output "Web IQ connection $ConnectionName created ($Target)."
Set-EnvLocalValue @{ WEBIQ_CONNECTION_NAME = $ConnectionName }
