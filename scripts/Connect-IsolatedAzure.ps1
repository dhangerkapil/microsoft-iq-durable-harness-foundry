<#
.SYNOPSIS
    Signs the repository's isolated Azure CLI profile in to the tenant in .env.local (device code).
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Use-IsolatedAzure.ps1')
if (-not $env:AZURE_TENANT_ID) { throw 'Set AZURE_TENANT_ID in .env.local first.' }
# az logout first: az login alone keeps an old Microsoft Graph token that Graph may keep refusing.
az logout 2>$null
az login --tenant $env:AZURE_TENANT_ID --use-device-code --output none
if ($LASTEXITCODE -ne 0) { throw 'Sign-in failed.' }
if ($env:AZURE_SUBSCRIPTION_ID) { az account set --subscription $env:AZURE_SUBSCRIPTION_ID --output none }
az account show --query "{user:user.name, tenant:tenantId, subscription:id}" --output table
