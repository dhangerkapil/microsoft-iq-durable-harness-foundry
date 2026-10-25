<#
.SYNOPSIS
    Creates (or removes) the Microsoft Purview DLP policy that the agent's Purview middleware enforces: prompts to the
    lead agent that contain a sensitive information type are blocked, and each match raises a DLP alert.

.DESCRIPTION
    The policy is scoped to the hosted agent's Entra agent identity, an "Entra-registered AI app" in Purview terms, with
    the Application enforcement plane. Its rule blocks UploadText (the user's prompt) that contains the sensitive
    information type, and raises an alert that shows in Purview's DLP alerts and in Microsoft Defender XDR.

    Prerequisites:
    - enable_purview = true in terraform.tfvars (the agent's Purview middleware and its three Graph roles);
    - Microsoft Purview pay-as-you-go billing in the tenant (Purview portal > Settings > Account);
    - the ExchangeOnlineManagement module and a Compliance Administrator (or Global Administrator) account.

    Policies reach the service within about an hour.

.EXAMPLE
    .\scripts\Set-PurviewDlpForAgent.ps1 -UserPrincipalName admin@contoso.onmicrosoft.com
    .\scripts\Set-PurviewDlpForAgent.ps1 -UserPrincipalName admin@contoso.onmicrosoft.com -Remove
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$UserPrincipalName,
    # The hosted agent's Entra agent identity (its app ID equals its object ID); default: Terraform's agent_principal_id.
    [string]$AgentAppId,
    [string]$AgentDisplayName = 'advisor-team (Investment Advisor)',
    [string]$PolicyName = 'Investment Advisor agents: block sensitive data in prompts',
    [string]$RuleName = 'Block U.S. Social Security numbers in prompts',
    [string]$SensitiveInformationType = 'U.S. Social Security Number (SSN)',
    # A Security & Compliance PowerShell token, for automation; otherwise the sign-in is interactive.
    [string]$AccessToken,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
if (-not (Get-Module -ListAvailable ExchangeOnlineManagement)) {
    throw 'Install the ExchangeOnlineManagement module first: Install-Module ExchangeOnlineManagement -Scope CurrentUser'
}
Import-Module ExchangeOnlineManagement

if (-not $AgentAppId -and -not $Remove) {
    . "$PSScriptRoot\Use-IsolatedAzure.ps1"
    $tf = Join-Path (Split-Path $PSScriptRoot -Parent) 'terraform'
    $AgentAppId = (terraform "-chdir=$tf" output -raw -no-color agent_principal_id).Trim()
}

$connect = @{ UserPrincipalName = $UserPrincipalName; ShowBanner = $false }
if ($AccessToken) { $connect.AccessToken = $AccessToken }
Connect-IPPSSession @connect
try {
    $policy = Get-DlpCompliancePolicy -Identity $PolicyName -ErrorAction SilentlyContinue
    if ($Remove) {
        if ($policy) {
            Get-DlpComplianceRule -Policy $PolicyName | ForEach-Object { Remove-DlpComplianceRule -Identity $_.Name -Confirm:$false }
            Remove-DlpCompliancePolicy -Identity $PolicyName -Confirm:$false
            Write-Output "Removed the DLP policy '$PolicyName'."
        } else {
            Write-Output "No DLP policy named '$PolicyName'."
        }
        return
    }

    if (-not $policy) {
        $location = @(@{
                Workload            = 'Applications'
                Location            = $AgentAppId
                LocationDisplayName = $AgentDisplayName
                LocationSource      = 'Entra'
                LocationType        = 'Individual'
                Inclusions          = @(@{ Type = 'Tenant'; Identity = 'All' })
            }) | ConvertTo-Json -Depth 5 -Compress -AsArray
        $policy = New-DlpCompliancePolicy -Name $PolicyName -Mode Enable -Locations $location -EnforcementPlanes @('Application') `
            -Comment 'Blocks prompts with sensitive data before the Investment Advisor agents see them (the agent calls Purview through Agent Framework).'
        Write-Output "Created the DLP policy '$PolicyName' for the Entra app $AgentAppId."
    } else {
        Write-Output "The DLP policy '$PolicyName' exists."
    }

    if (-not (Get-DlpComplianceRule -Policy $PolicyName | Where-Object Name -EQ $RuleName)) {
        New-DlpComplianceRule -Name $RuleName -Policy $PolicyName `
            -ContentContainsSensitiveInformation @{ Name = $SensitiveInformationType } `
            -RestrictAccess @(@{ setting = 'UploadText'; value = 'Block' }) `
            -GenerateAlert $true -ReportSeverityLevel 'Medium' | Out-Null
        Write-Output "Created the rule '$RuleName': block prompts that contain '$SensitiveInformationType', with an alert."
    } else {
        Write-Output "The rule '$RuleName' exists."
    }

    Get-DlpCompliancePolicy -Identity $PolicyName | Select-Object Name, Mode, EnforcementPlanes, DistributionStatus |
        Format-List | Out-String | Write-Output
} finally {
    Disconnect-ExchangeOnline -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
}
