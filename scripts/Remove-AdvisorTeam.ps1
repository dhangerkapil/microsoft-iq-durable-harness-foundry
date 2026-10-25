<#
.SYNOPSIS
    Removes an Investment Advisor Team deployment: the Fabric workspace, the agents, UI instances
    and Entra apps that scripts created, then everything Terraform created.

.DESCRIPTION
    Runs in the deployment's isolated profile (.env.local of this clone). In order:
      1. Fabric     Deletes the workspace in FABRIC_WORKSPACE_ID (lakehouse, notebooks, Portfolio Analyst data agent).
      2. Agents     Deletes the hosted agents in the Foundry project that Terraform does not manage (the candidate).
      3. Entra      Deletes extra UI instances (Publish-UiInstance.ps1) with their browser app registrations, and this
                    deployment's hydration and Fabric IQ app registrations (names carry TEAM_NAME_SUFFIX, so other
                    deployments in the tenant are not touched).
      4. Terraform  Keeps the tenant's shared Work IQ service principal (removed from state, not deleted) and
                    destroys the rest: resource group, Foundry account and project with its models, connections,
                    toolboxes, evaluations and Insights monitors, the registry, Cosmos DB, Application Insights,
                    Azure AI Search with the Foundry IQ index and knowledge base, the UI Container App, the toolbox
                    and UI app registrations and the Fabric capacity. An existing Foundry account or Fabric capacity
                    (-ExistingFoundryAccountId, -ExistingFabricCapacityId) is kept: only this deployment's project,
                    guardrail policy, role assignments and workspace are removed from it.
      5. Purge      With -PurgeFoundry, purges the soft-deleted Foundry account so its name is free at once.
      6. Local      Removes .env.local, terraform/terraform.tfvars and terraform/*.auto.tfvars unless -KeepLocalConfig.

    The Microsoft 365 demo content (mail, calendar events, SharePoint and OneDrive files, the recorded meeting) stays
    in the tenant; docs/DEPLOYMENT.md lists it for manual cleanup. Use -WhatIf to see the plan first.

.EXAMPLE
    .\scripts\Remove-AdvisorTeam.ps1 -WhatIf
    .\scripts\Remove-AdvisorTeam.ps1 -PurgeFoundry
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [switch]$PurgeFoundry,
    [switch]$KeepLocalConfig
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$tf = Join-Path $repoRoot 'terraform'
$whatIf = $WhatIfPreference; $WhatIfPreference = $false
. (Join-Path $PSScriptRoot 'Use-IsolatedAzure.ps1') -RequireLogin
. (Join-Path $PSScriptRoot 'AdvisorCommon.ps1')
$WhatIfPreference = $whatIf
if (-not (Test-Path (Join-Path $env:TF_DATA_DIR 'terraform.tfstate'))) {
    terraform "-chdir=$tf" init -input=false -no-color "-backend-config=path=$env:TEAM_TF_STATE" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'terraform init failed.' }
}

function Get-TfOutput([string]$Name) {
    $value = terraform "-chdir=$tf" output -raw -no-color $Name 2>$null
    if ($LASTEXITCODE -eq 0 -and $value) { "$value".Trim() } else { '' }
}

$resourceGroup = Get-TfOutput 'resource_group_name'
$project = Get-TfOutput 'foundry_project_endpoint'
$liveAgent = Get-TfOutput 'agent_name'
$accountName = if ($project -match '^https://([^.]+)\.services\.ai\.azure\.com/') { $Matches[1] } else { '' }
# An existing Foundry account or Fabric capacity (another resource group) is never deleted: only this deployment's
# project, workspace and resources go. Deployments from before these outputs existed created both.
$accountManaged = (Get-TfOutput 'foundry_account_managed') -ne 'false'
$capacityManaged = (Get-TfOutput 'fabric_capacity_managed') -ne 'false'
if (-not $resourceGroup) { throw 'No Terraform state with a resource group in this profile: nothing to remove (or run from the deployment''s clone).' }
$location = az group show --name $resourceGroup --query location -o tsv 2>$null
Write-Output "Deployment: resource group $resourceGroup, Foundry account $accountName$(if (-not $accountManaged) { ' (existing: kept; only this deployment''s project is removed)' }), agent $liveAgent, tenant $env:AZURE_TENANT_ID."
if (-not $capacityManaged -and (Get-TfOutput 'fabric_capacity_id')) { Write-Output 'Fabric capacity: existing, kept (only this deployment''s workspace is removed).' }

# 1. Fabric workspace (created by fabric/Deploy-FabricPortfolio.ps1, not by Terraform).
if ($env:FABRIC_WORKSPACE_ID) {
    if ($PSCmdlet.ShouldProcess("Fabric workspace $env:FABRIC_WORKSPACE_ID", 'Delete with its lakehouse, notebooks and data agent')) {
        $headers = @{ Authorization = "Bearer $(Get-AzToken 'https://api.fabric.microsoft.com')" }
        $uri = "https://api.fabric.microsoft.com/v1/workspaces/$env:FABRIC_WORKSPACE_ID"
        try { Invoke-RestMethod -Method Delete -Uri $uri -Headers $headers | Out-Null; Write-Output 'Deleted the Fabric workspace.' }
        catch {
            $code = $_.Exception.Response.StatusCode.value__
            if ($code -eq 404) { Write-Output 'The Fabric workspace was already gone.' }
            else {
                # A paused capacity can refuse item deletes: resume it, then try once more.
                Write-Output "Workspace delete answered $code; resuming the Fabric capacity and retrying."
                & (Join-Path $PSScriptRoot 'Set-FabricCapacityState.ps1') -State Active | Out-Null
                Invoke-RestMethod -Method Delete -Uri $uri -Headers $headers | Out-Null
                Write-Output 'Deleted the Fabric workspace.'
            }
        }
    }
} else { Write-Output 'No Fabric workspace recorded (FABRIC_WORKSPACE_ID).' }

# 2. Hosted agents that Terraform does not manage (candidates from Publish-CandidateAgent.ps1).
if ($project) {
    $agents = @()
    try { $agents = @((az rest --method get --url "$project/agents?api-version=v1" --resource 'https://ai.azure.com' -o json | ConvertFrom-Json).data | ForEach-Object name) } catch { }
    foreach ($name in $agents | Where-Object { $_ -and $_ -ne $liveAgent }) {
        if ($PSCmdlet.ShouldProcess("Foundry agent $name", 'Delete')) {
            az rest --method delete --url "$project/agents/$([uri]::EscapeDataString($name))?api-version=v1" --resource 'https://ai.azure.com' -o none
            Write-Output "Deleted agent $name."
        }
    }
}

# 3. App registrations created by scripts (Terraform owns the toolbox and UI apps), and extra UI instances
#    (Publish-UiInstance.ps1): their browser app registrations live outside the resource group.
foreach ($instance in @(az containerapp list -g $resourceGroup --query "[?tags.agent && name!='$(Get-TfOutput 'ui_app_name')'].{name:name, agent:tags.agent}" -o json 2>$null | ConvertFrom-Json)) {
    if ($PSCmdlet.ShouldProcess("UI instance $($instance.name) for agent $($instance.agent)", 'Delete with its browser app registration')) {
        & (Join-Path $PSScriptRoot 'Publish-UiInstance.ps1') -AgentName $instance.agent -Name $instance.name -Remove
    }
}
foreach ($base in 'advisor-team-hydration', 'advisor-team-fabric-iq') {
    $name = Get-TenantScopedName $base
    foreach ($appId in @(az ad app list --display-name $name --query '[].appId' -o tsv)) {
        if ($appId -and $PSCmdlet.ShouldProcess("App registration $name ($appId)", 'Delete')) {
            az ad app delete --id $appId
            Write-Output "Deleted app registration $name."
        }
    }
}

# 3b. Foundry IQ in an existing (shared) Search service: Terraform doesn't own these items, so remove exactly this
#     deployment's team-firm-* knowledge base, knowledge source and index by name. A Search service this deployment
#     created goes with the resource group.
$searchEndpoint = Get-TfOutput 'foundry_iq_search_endpoint'
if ($searchEndpoint -and (Get-TfOutput 'search_service_managed') -eq 'false') {
    $searchHeaders = @{ Authorization = "Bearer $(az account get-access-token --resource 'https://search.azure.com' --query accessToken -o tsv)" }
    foreach ($item in 'knowledgebases/team-firm-knowledge', 'knowledgesources/team-firm-library-ks', 'indexes/team-firm-library-index') {
        if ($PSCmdlet.ShouldProcess("$searchEndpoint/$item", 'Delete from the existing Search service')) {
            try {
                Invoke-RestMethod -Method Delete -Uri "$searchEndpoint/${item}?api-version=2026-08-01-preview" -Headers $searchHeaders | Out-Null
                Write-Output "Deleted $item."
            } catch {
                if ($_.Exception.Response.StatusCode.value__ -eq 404) { Write-Output "$item was already gone." } else { throw }
            }
        }
    }
}

# 4. Terraform: keep the shared Work IQ service principal and the protected MCP connection record, destroy the rest.
$state = @(terraform "-chdir=$tf" state list 2>$null)
foreach ($address in 'azuread_service_principal.workiq', 'azapi_resource.workiq_mcp[0]') {
    if ($state -contains $address -and $PSCmdlet.ShouldProcess($address, 'Remove from Terraform state (kept in Azure/Entra)')) {
        terraform "-chdir=$tf" state rm $address | Out-Null
        Write-Output "Removed $address from state; it stays (the Work IQ service principal is shared by the tenant)."
    }
}
if ($PSCmdlet.ShouldProcess("resource group $resourceGroup and every resource Terraform created", 'terraform destroy')) {
    terraform "-chdir=$tf" destroy -auto-approve -input=false -no-color
    if ($LASTEXITCODE -ne 0) { throw 'terraform destroy failed; fix the error and rerun this script (it is idempotent).' }
}

# 5. The Foundry account stays soft-deleted for 48 hours unless purged (only an account this deployment created).
if ($PurgeFoundry -and $accountManaged -and $accountName -and $location -and $PSCmdlet.ShouldProcess("Foundry account $accountName", 'Purge the soft-deleted account')) {
    az cognitiveservices account purge --name $accountName --resource-group $resourceGroup --location $location -o none
    Write-Output "Purged the soft-deleted Foundry account $accountName."
}

# 6. Local, git-ignored configuration of this deployment.
if (-not $KeepLocalConfig) {
    $files = @((Join-Path $repoRoot '.env.local'), (Join-Path $tf 'terraform.tfvars')) + @(Get-ChildItem -Path $tf -Filter '*.auto.tfvars' | ForEach-Object FullName)
    foreach ($file in $files) {
        if ((Test-Path -LiteralPath $file) -and $PSCmdlet.ShouldProcess($file, 'Delete')) { Remove-Item -LiteralPath $file -Force; Write-Output "Deleted $file." }
    }
}
Write-Output "Done. The isolated profile folder (sign-in cache, logs, generated documents) is $env:TEAM_PROFILE_DIR; delete it when you no longer need it."
