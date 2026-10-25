<#
.SYNOPSIS
    Deploys the Investment Advisor Agent with Microsoft IQ into a tenant from one command: infrastructure, the Work IQ
    connection and toolboxes, Fabric IQ portfolio data, the Foundry IQ firm library, optional Web IQ, the hosted
    agent and UI, Foundry quality assets and the Microsoft 365 demo content, then prints the steps that need a person.

.DESCRIPTION
    Steps, in order (each is idempotent; rerun any of them with -Step):
      Config    Writes the two git-ignored inputs, .env.local and terraform/terraform.tfvars, from the parameters,
                signs the isolated Azure CLI profile in (device code) and looks up the advisor (the signed-in
                admin) and the analyst. Existing files are kept unless -Force; differences are reported. The shared
                resources (-Existing...Id) are recorded in .env.local, where the safety checks read them.
      Platform  Terraform: resource group, Foundry account, project and models, registry, Cosmos DB, Application
                Insights, role assignments, the Work IQ service principal, the toolbox app, the Fabric capacity and,
                for Foundry IQ, Azure AI Search and the embedding deployment. With -ExistingFoundryAccountId and
                -ExistingFabricCapacityId it uses that Foundry account (creating only this deployment's project in it)
                and that Fabric capacity, for example from another resource group, instead of creating them.
      WorkIq    The Work IQ MCP connection, then four toolboxes: the agent's Work IQ tools (read-only, plus
                list_agents), Work IQ Chat over A2A (delegate_to_work_iq), the demo-only governance probe (fetch,
                update_entity, delete_entity) and the drafts toolbox (create_entity, behind the agent's approval step).
      Fabric    Optional (skipped when -FabricSku is ''): Fabric workspace and lakehouse with the synthetic
                portfolio data, the Portfolio Analyst data agent, its Foundry connection and toolbox.
      FoundryIq The fictional firm library (six policy PDFs) pushed into an Azure AI Search index with embeddings,
                the knowledge source and knowledge base, its project connection (managed identity) and toolbox.
      WebIq     Optional (needs a Web IQ API key, -WebIqApiKey): the Web IQ connection and toolbox. The agent's
                boundary guard keeps client and staff names (web_boundary_terms), amounts and account numbers out
                of every public-web query.
      Agent     Builds the hosted-agent image in the registry and releases it as a new agent version.
      Ui        Builds and deploys the chat UI.
      Quality   Generates the rubric evaluator, creates the Insights monitor with daily scans, and runs the agent
                optimizer once on the brief-writer prompt agent (evals/Invoke-FoundryQuality.ps1). The full loop on
                the hosted agent (evaluate, Insights, optimize, release, compare) needs Work IQ consent first:
                evals/Invoke-QualityLoop.ps1, listed in the steps that need a person.
      Content   Microsoft 365 demo content for the advisor: mail, calendar, SharePoint and OneDrive files, and the
                Q4 review as a Teams meeting organized by the analyst.
      Prewarm   Warms the agent and clears the demo switches.

    The signed-in admin is the advisor in the story. The analyst (-AnalystUpn) must be a licensed user with a
    mailbox and Teams: they organize the recorded meeting, message the advisor, and are the second user for the
    permission-trimming step. The story is one week: the recorded review on Friday, the client's emails, the
    analyst's alert on Monday afternoon, the demo on Tuesday or Wednesday and the client meeting on Thursday
    (-StoryMeetingDate, default the next Thursday). All names and data are fictional.

.EXAMPLE
    .\scripts\Deploy-AdvisorTeam.ps1 -TenantId <tenant guid> -SubscriptionId <subscription guid> `
        -ResourceGroup contoso-advisor-rg -AnalystUpn analyst@contoso.onmicrosoft.com
.EXAMPLE
    .\scripts\Deploy-AdvisorTeam.ps1 @parameters -WebIqApiKey (Read-Host 'Web IQ key' -AsSecureString)  # with Web IQ
.EXAMPLE
    # Reuse a Foundry account and a Fabric capacity from another resource group (no new models or capacity):
    .\scripts\Deploy-AdvisorTeam.ps1 @parameters `
        -ExistingFoundryAccountId /subscriptions/<id>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<name> `
        -ExistingFabricCapacityId /subscriptions/<id>/resourceGroups/<rg>/providers/Microsoft.Fabric/capacities/<name>
.EXAMPLE
    .\scripts\Deploy-AdvisorTeam.ps1 @parameters -Step Config, Platform -WhatIf   # show config, plan only
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][guid]$TenantId,
    [Parameter(Mandatory)][guid]$SubscriptionId,
    [Parameter(Mandatory)][ValidatePattern('^[-\w._()]+$')][string]$ResourceGroup,
    [Parameter(Mandatory)][string]$AnalystUpn,
    # The advisor's name in the story ("Hi Alex"); default: the signed-in admin's display name.
    [string]$AdvisorName = '',
    [string]$Location = 'eastus2',
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$ProfileName = 'demo',
    [string]$ClientEmail = 'michael.scott@scott-family.example',
    # GitHub repository (owner/name): used in resource-group, app-registration and tag names.
    [string]$RepoName = 'dhangerkapil/investment-advisor-multi-agent',
    [ValidatePattern('^[a-z0-9][a-z0-9-]{1,62}$')][string]$AgentName = 'advisor-team',
    # The fictional client meeting (a Thursday, yyyy-MM-dd) that anchors the story's week; default: kept from
    # .env.local, else the next Thursday. Changing it later means rerunning -Step Fabric, Content.
    [ValidatePattern('^(|\d{4}-\d{2}-\d{2})$')][string]$StoryMeetingDate = '',
    # Short suffix for this deployment's names (proj-advteam-<suffix>, advisor-team-<suffix>-*).
    [ValidatePattern('^[a-z0-9]{0,8}$')][string]$NameSuffix = '',
    # team (the lead and its specialists) or solo (one agent with every tool).
    [ValidateSet('team', 'solo')][string]$TeamMode = 'team',
    # A separate account in this region hosts only team-specialist (for model quota); blank: the main account.
    [string]$SpecialistLocation = '',
    # Fabric is an optional story source; '' skips the capacity, the data agent and its toolbox.
    [ValidatePattern('^(|F(2|4|8|16|32|64|128|256|512|1024|2048))$')][string]$FabricSku = 'F8',
    [string]$FabricLocation = '',
    # The three -Existing...Id parameters default to EXISTING_* in .env.local, and the Config step records them there.
    # Use an existing Fabric capacity (resource ID, for example in another resource group) instead of creating one;
    # this deployment's workspace goes on it. -FabricSku is then ignored.
    [ValidatePattern('(?i)^(|/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Fabric/capacities/[^/]+)$')]
    [string]$ExistingFabricCapacityId = '',
    # Use an existing Foundry account (resource ID, for example in another resource group) instead of creating one.
    # The deployment creates its own project in it and uses its model deployments (-ChatModel, gpt-5.5 for the
    # optimizer and text-embedding-3-large for Foundry IQ must exist there). The account's region applies.
    [ValidatePattern('(?i)^(|/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+)$')]
    [string]$ExistingFoundryAccountId = '',
    # Azure AI Search region for Foundry IQ when the resource group's region has no capacity or no agentic retrieval.
    [string]$SearchLocation = '',
    # Use an existing Azure AI Search service for Foundry IQ (this deployment adds its own index and knowledge base).
    [ValidatePattern('(?i)^(|/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.Search/searchServices/[^/]+)$')]
    [string]$ExistingSearchServiceId = '',
    # Web IQ (preview) API key: adds the public-web source. Without it (and without an earlier Web IQ deployment) the
    # agent has no web tools. Also read from the WEBIQ_API_KEY environment variable.
    [securestring]$WebIqApiKey,
    # More users who may call the agent through the UI (Foundry User on the project); the analyst is always added.
    [string[]]$ExtraUserUpns = @(),
    [ValidateSet('Config', 'Guard', 'Platform', 'WorkIq', 'Fabric', 'FoundryIq', 'WebIq', 'Agent', 'Ui', 'Quality', 'Content', 'Prewarm', 'Verify')]
    # Content (Microsoft 365 story hydration) is not a default step: never run it in a tenant another demo uses.
    [string[]]$Step = @('Config', 'Guard', 'Platform', 'WorkIq', 'Fabric', 'FoundryIq', 'WebIq', 'Agent', 'Ui', 'Quality', 'Prewarm', 'Verify'),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'StoryDates.ps1')
$tf = Join-Path $repoRoot 'terraform'
$envFile = Join-Path $repoRoot '.env.local'
$tfvarsFile = Join-Path $tf 'terraform.tfvars'
$toolboxes = @{ Main = 'team-workiq'; Governance = 'team-governance'; Drafts = 'team-drafts'; Fabric = 'team-fabric'
                FoundryIq = 'team-foundryiq'; WebIq = 'team-webiq'; WorkIqChat = 'team-workiq-chat' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmm'

function Write-Step([string]$Name) { Write-Output ''; Write-Output "=== $Name ===" }

function Read-KeyValues([string]$Path, [string]$Pattern) {
    $values = [ordered]@{}
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path) {
            if ($line -match $Pattern) { $values[$Matches[1]] = $Matches[2].Trim() }
        }
    }
    $values
}

function Compare-Settings([string]$Label, $Desired, $Existing) {
    $differences = @(foreach ($key in $Desired.Keys) {
        if (-not $Existing.Contains($key)) { "  missing   $key = $($Desired[$key])" }
        elseif ($Existing[$key] -ne $Desired[$key]) { "  differs   $key = $($Existing[$key])  (parameters give $($Desired[$key]))" }
    })
    $extra = @($Existing.Keys | Where-Object { -not $Desired.Contains($_) })
    if ($differences) { Write-Output "$Label differs from the parameters (kept; use -Force to rewrite):"; $differences }
    else { Write-Output "$Label matches the parameters." }
    if ($extra) { Write-Output "  also set there, not managed by this script: $($extra -join ', ')" }
}

# The shared resources to reuse reach Terraform through terraform.tfvars, while the safety checks for shared resources
# (Test-Preflight.ps1, Get-SharedInventory.ps1 and the plan gate) read EXISTING_* from .env.local, so both must name
# the same ones. A parameter left out takes the value .env.local holds; the Config step records the parameters there.
$recordedEnv = Read-KeyValues $envFile '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$'
function Resolve-SharedResourceId([string]$Key, [string]$Parameter, [string]$Given) {
    $recorded = "$($recordedEnv[$Key])".Trim('"')
    if (-not $Given) { return $recorded }
    if ($recorded -and $recorded -ne $Given -and -not $Force) {
        throw (".env.local has $Key=$recorded, but -$Parameter is $Given. The safety checks read .env.local and " +
            'Terraform the parameter, so they must match: correct one of them, or rerun with -Force to rewrite .env.local.')
    }
    $Given
}
$ExistingFoundryAccountId = Resolve-SharedResourceId 'EXISTING_FOUNDRY_ACCOUNT_ID' 'ExistingFoundryAccountId' $ExistingFoundryAccountId
$ExistingSearchServiceId = Resolve-SharedResourceId 'EXISTING_SEARCH_SERVICE_ID' 'ExistingSearchServiceId' $ExistingSearchServiceId
$ExistingFabricCapacityId = Resolve-SharedResourceId 'EXISTING_FABRIC_CAPACITY_ID' 'ExistingFabricCapacityId' $ExistingFabricCapacityId
$sharedResourceIds = [ordered]@{
    EXISTING_FOUNDRY_ACCOUNT_ID = $ExistingFoundryAccountId
    EXISTING_SEARCH_SERVICE_ID  = $ExistingSearchServiceId
    EXISTING_FABRIC_CAPACITY_ID = $ExistingFabricCapacityId
}

function Invoke-Script([string]$Description, [scriptblock]$Action) {
    if ($PSCmdlet.ShouldProcess($Description)) {
        & $Action
        if (-not $?) { throw "Step failed: $Description" }
    }
}

function Test-Tools {
    $missing = @('az', 'python', 'node' | Where-Object { -not (Get-Command $_ -ErrorAction SilentlyContinue) })
    if ($missing) { throw "Install these tools first: $($missing -join ', ') (see README Prerequisites)." }
    if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Run this script in PowerShell 7 or later.' }
}

function Install-Terraform {
    # Terraform goes into the isolated profile's bin folder, which Use-IsolatedAzure.ps1 puts first on PATH.
    if (Get-Command terraform -ErrorAction SilentlyContinue) { return }
    $version = '1.16.6'
    $zip = Join-Path $env:TEMP "terraform_$version.zip"
    if ($PSCmdlet.ShouldProcess($dirs['bin'], "Install Terraform $version")) {
        Invoke-WebRequest -Uri "https://releases.hashicorp.com/terraform/$version/terraform_${version}_windows_amd64.zip" -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath $dirs['bin'] -Force
        Remove-Item $zip -Force
        Write-Output "Installed Terraform $version in $($dirs['bin'])."
    }
}

# --- Config ---------------------------------------------------------------------------------------------------------
if ($Step -contains 'Config') {
    Write-Step 'Config'
    Test-Tools
    $baseEnv = [ordered]@{
        TEAM_PROFILE       = $ProfileName
        AZURE_TENANT_ID       = "$TenantId"
        AZURE_SUBSCRIPTION_ID = "$SubscriptionId"
        DEMO_ANALYST_UPN      = $AnalystUpn
        DEMO_CLIENT_EMAIL     = $ClientEmail
        # Tenant-wide names (Entra apps, Fabric workspace) carry the profile, so deployments never collide.
        TEAM_NAME_SUFFIX      = $(if ($NameSuffix) { $NameSuffix } else { $ProfileName })
    }
    # The shared resources, for the safety checks (Terraform gets them through terraform.tfvars).
    foreach ($key in $sharedResourceIds.Keys) { if ($sharedResourceIds[$key]) { $baseEnv[$key] = $sharedResourceIds[$key] } }
    $existingEnv = Read-KeyValues $envFile '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$'
    # The story week: the parameter, else the deployment's existing week, else the next Thursday.
    $storyDate = if ($StoryMeetingDate) { $StoryMeetingDate }
        elseif ($existingEnv['STORY_MEETING_DATE']) { $existingEnv['STORY_MEETING_DATE'] }
        elseif ($existingEnv.Count) { '2026-10-22' }  # deployed before story weeks existed: the reference week
        else { Get-StoryDefaultMeetingDate }
    $null = Get-StoryMeetingDate $storyDate
    $baseEnv['STORY_MEETING_DATE'] = $storyDate
    if ($existingEnv.Contains('AZURE_TENANT_ID') -and $existingEnv['AZURE_TENANT_ID'] -ne "$TenantId" -and -not $Force) {
        throw ".env.local is for tenant $($existingEnv['AZURE_TENANT_ID']), not $TenantId. Use another clone, or -Force to switch."
    }
    if (-not (Test-Path -LiteralPath $envFile) -or $Force) {
        if ($PSCmdlet.ShouldProcess($envFile, 'Write the tenant settings')) {
            @('# Local, git-ignored settings for the isolated demo profile. Do not commit.') +
                @($baseEnv.Keys | ForEach-Object { "$_=$($baseEnv[$_])" }) | Set-Content -LiteralPath $envFile -Encoding utf8
        } else { Write-Output 'Would write .env.local:'; $baseEnv.Keys | ForEach-Object { "  $_=$($baseEnv[$_])" } }
    }

    if (Test-Path -LiteralPath $envFile) {
        # Loading the profile only sets process variables, so it runs even under -WhatIf.
        $whatIf = $WhatIfPreference; $WhatIfPreference = $false
        . (Join-Path $PSScriptRoot 'Use-IsolatedAzure.ps1')
        . (Join-Path $PSScriptRoot 'AdvisorCommon.ps1')
        $WhatIfPreference = $whatIf
        Install-Terraform
        $tenantNow = az account show --query tenantId -o tsv 2>$null
        if ($tenantNow -ne "$TenantId") {
            Write-Output "Sign in as the tenant admin who will be the advisor in the story (device code)."
            & (Join-Path $PSScriptRoot 'Connect-IsolatedAzure.ps1')
        }
        az account set --subscription "$SubscriptionId" --only-show-errors
        $advisor = az ad signed-in-user show --query '{id:id, upn:userPrincipalName, name:displayName}' -o json | ConvertFrom-Json
        $analyst = az ad user show --id $AnalystUpn --query '{id:id, upn:userPrincipalName, name:displayName}' -o json | ConvertFrom-Json
        if (-not $analyst) { throw "Analyst $AnalystUpn was not found in the tenant." }
        if ($analyst.id -eq $advisor.id) { throw 'The analyst must be a different user from the signed-in advisor.' }
        $userIds = @($analyst.id) + @($ExtraUserUpns | ForEach-Object { (az ad user show --id $_ --query id -o tsv) })
        Write-Output "Advisor (signed in): $($advisor.name) <$($advisor.upn)>; analyst: $($analyst.name) <$($analyst.upn)>"
        $storyAdvisor = if ($AdvisorName) { $AdvisorName } else { $advisor.name }

        $desiredEnv = [ordered]@{} + $baseEnv
        $desiredEnv['DEMO_ADVISOR_NAME'] = $storyAdvisor
        $desiredEnv['DEMO_ANALYST_NAME'] = $analyst.name
        if ($Force -or $existingEnv.Count -eq 0) {
            if ($PSCmdlet.ShouldProcess($envFile, 'Record the advisor and analyst names')) {
                Set-EnvLocalValue @{ DEMO_ADVISOR_NAME = $storyAdvisor; DEMO_ANALYST_NAME = $analyst.name }
            }
        } else {
            # A new story week is a normal change (no -Force needed); the content must then be published again.
            if ($existingEnv['STORY_MEETING_DATE'] -ne $storyDate -and $PSCmdlet.ShouldProcess($envFile, "Set STORY_MEETING_DATE=$storyDate")) {
                Set-EnvLocalValue @{ STORY_MEETING_DATE = $storyDate }
                if ($existingEnv['STORY_MEETING_DATE']) { Write-Warning "The story week moved from $($existingEnv['STORY_MEETING_DATE']) to ${storyDate}: rerun -Step Fabric, Content and record the review again." }
                $existingEnv['STORY_MEETING_DATE'] = $storyDate
            }
            # The shared resources must be in .env.local too, even when it was written before they were given: the
            # safety checks read them from there.
            $missingShared = @{}
            foreach ($key in $sharedResourceIds.Keys) {
                if ($sharedResourceIds[$key] -and -not "$($existingEnv[$key])") { $missingShared[$key] = $sharedResourceIds[$key] }
            }
            if ($missingShared.Count -and $PSCmdlet.ShouldProcess($envFile, "Record $($missingShared.Keys -join ', ')")) {
                Set-EnvLocalValue $missingShared
                foreach ($key in $missingShared.Keys) { $existingEnv[$key] = $missingShared[$key] }
            }
            Compare-Settings '.env.local' $desiredEnv $existingEnv
        }
        Write-Output ('Story week: client meeting Thursday {0:yyyy-MM-dd} 10:00 AM ET; recorded review Friday {1:yyyy-MM-dd}; demo on Tuesday {2:yyyy-MM-dd} or Wednesday {3:yyyy-MM-dd}.' -f
            (Get-StoryMeetingDate $storyDate), (Get-StoryMeetingDate $storyDate).AddDays(-6), (Get-StoryMeetingDate $storyDate).AddDays(-2), (Get-StoryMeetingDate $storyDate).AddDays(-1))

        $createWorkIqSp = -not (az ad sp show --id fdcc1f02-fc51-4226-8753-f668596af7f7 --query id -o tsv 2>$null)
        if ($createWorkIqSp) { Write-Output 'This tenant has no Work IQ service principal: Terraform will create it.' }
        $quoted = { param($v) if ($v -is [bool]) { "$v".ToLower() } elseif ($v -is [int]) { "$v" } else { "`"$v`"" } }
        $hclList = { param([string[]]$Items) '[{0}]' -f (($Items | ForEach-Object { '"{0}"' -f ($_ -replace '["\\]', '') }) -join ', ') }
        # Web IQ stays on once deployed; a key (parameter or WEBIQ_API_KEY) turns it on.
        $existingWebIq = (Read-KeyValues $tfvarsFile '^\s*(webiq_toolbox_name)\s*=\s*"(.*)"\s*$')['webiq_toolbox_name']
        $webIqToolbox = if ($WebIqApiKey -or $env:WEBIQ_API_KEY -or $existingWebIq) { $toolboxes.WebIq } else { '' }
        $boundaryTerms = Get-WebBoundaryTerms -Extra $storyAdvisor, $analyst.name
        $tfvars = [ordered]@{
            subscription_id         = "$SubscriptionId"
            location                = $Location
            resource_group_name     = $ResourceGroup
            gh_repo                 = $RepoName
            name_suffix             = $NameSuffix
            team_mode               = $TeamMode
            specialist_location     = $SpecialistLocation
            agent_name              = $AgentName
            workiq_toolbox_name     = $toolboxes.Main
            workiq_tool_mode        = 'mcp'
            governance_toolbox_name = $toolboxes.Governance
            fabric_toolbox_name     = $(if ($FabricSku -or $ExistingFabricCapacityId) { $toolboxes.Fabric } else { '' })
            drafts_toolbox_name     = $toolboxes.Drafts
            foundry_managed_network = $false
            workiq_connection_kind  = 'a2a'
            agent_capture_content   = $true
            fabric_capacity_sku     = $FabricSku
            fabric_location         = $FabricLocation
            existing_fabric_capacity_id = $ExistingFabricCapacityId
            existing_foundry_account_id = $ExistingFoundryAccountId
            existing_search_service_id = $ExistingSearchServiceId
            create_workiq_service_principal = $createWorkIqSp
            foundry_iq_enabled      = $true
            foundry_iq_location     = $SearchLocation
            foundryiq_toolbox_name  = $toolboxes.FoundryIq
            webiq_toolbox_name      = $webIqToolbox
            workiq_chat_toolbox_name = $toolboxes.WorkIqChat
        }
        $lines = @(
            '# Written by scripts/Deploy-AdvisorTeam.ps1. Local and git-ignored: never commit.'
            '# The Work IQ MCP connection and the toolboxes are created by scripts (WorkIq step), so Terraform keeps'
            '# workiq_connection_kind = "a2a" and the agent uses Work IQ MCP tools (workiq_tool_mode = "mcp").'
            '# OAuth tool connections need a public-network Foundry account (foundry_managed_network = false).'
            '# agent_capture_content = true records prompts and tool payloads in traces: synthetic demo tenants only.'
            '# web_boundary_terms: names the agent never sends to Web IQ (the clients, the advisor and the analyst).'
        ) + @($tfvars.Keys | ForEach-Object { '{0} = {1}' -f $_, (& $quoted $tfvars[$_]) }) +
            ('user_object_ids = [{0}]' -f (($userIds | ForEach-Object { "`"$_`"" }) -join ', ')) +
            ('web_boundary_terms = {0}' -f (& $hclList $boundaryTerms))
        $existingTf = Read-KeyValues $tfvarsFile '^\s*([a-z_][a-z0-9_]*)\s*=\s*(.*?)\s*$'
        if (-not (Test-Path -LiteralPath $tfvarsFile) -or $Force) {
            if ($PSCmdlet.ShouldProcess($tfvarsFile, 'Write the Terraform inputs')) { $lines | Set-Content -LiteralPath $tfvarsFile -Encoding utf8 }
            else { Write-Output 'Would write terraform/terraform.tfvars:'; $lines | ForEach-Object { "  $_" } }
        } else {
            $desiredTf = [ordered]@{}
            foreach ($key in $tfvars.Keys) { $desiredTf[$key] = & $quoted $tfvars[$key] }
            $desiredTf['user_object_ids'] = '[{0}]' -f (($userIds | ForEach-Object { "`"$_`"" }) -join ', ')
            $desiredTf['web_boundary_terms'] = & $hclList $boundaryTerms
            Compare-Settings 'terraform/terraform.tfvars' $desiredTf $existingTf
        }
    }
}

# Every later step runs in the isolated profile.
$whatIf = $WhatIfPreference; $WhatIfPreference = $false
. (Join-Path $PSScriptRoot 'Use-IsolatedAzure.ps1') -RequireLogin
$WhatIfPreference = $whatIf
$tfvarsFabric = Read-KeyValues $tfvarsFile '^\s*(fabric_capacity_sku|existing_fabric_capacity_id)\s*=\s*"(.*)"\s*$'
$fabricEnabled = $tfvarsFabric['fabric_capacity_sku'] -or $tfvarsFabric['existing_fabric_capacity_id']

if ($Step -contains 'Guard') {
    Write-Step 'Guard'
    Invoke-Script 'Preflight: planned names are free or already ours' { & (Join-Path $PSScriptRoot 'Test-Preflight.ps1') }
    $inventoryDir = Join-Path $env:TEAM_WORK_DIR 'inventory'
    if (($ExistingFoundryAccountId -or $ExistingSearchServiceId -or $ExistingFabricCapacityId) -and
        -not (Get-ChildItem $inventoryDir -Filter 'baseline-*.json' -ErrorAction SilentlyContinue)) {
        Invoke-Script 'Baseline inventory of the shared resources' { & (Join-Path $PSScriptRoot 'Get-SharedInventory.ps1') -Label baseline }
    }
}

if ($Step -contains 'Platform') {
    Write-Step 'Platform'
    if ($WhatIfPreference) { & { $WhatIfPreference = $false; & (Join-Path $PSScriptRoot 'Invoke-TerraformApply.ps1') -Name platform -PlanOnly } }
    else { & (Join-Path $PSScriptRoot 'Invoke-TerraformApply.ps1') -Name platform -AllowChanges }
}

if ($Step -contains 'WorkIq') {
    Write-Step 'WorkIq'
    Invoke-Script 'Work IQ MCP connection' { & (Join-Path $PSScriptRoot 'Set-WorkIqConnection.ps1') -Kind Mcp }
    Invoke-Script "Toolbox $($toolboxes.Main) (read-only Work IQ tools and list_agents)" {
        & (Join-Path $PSScriptRoot 'Set-AdvisorToolbox.ps1') -ToolboxName $toolboxes.Main `
            -AllowedTools retrieve, fetch, fetch_blob, call_function, get_schema, search_paths, ask, list_agents
    }
    Invoke-Script "Toolbox $($toolboxes.WorkIqChat) (Work IQ Chat over A2A)" {
        & (Join-Path $PSScriptRoot 'Set-AdvisorToolbox.ps1') -ToolboxName $toolboxes.WorkIqChat -WorkIqMode A2A `
            -Description 'Work IQ Chat over A2A: delegates a whole question to Work IQ (Copilot reasoning).'
    }
    Invoke-Script "Toolbox $($toolboxes.Governance) (policy probe)" {
        & (Join-Path $PSScriptRoot 'Set-AdvisorToolbox.ps1') -ToolboxName $toolboxes.Governance `
            -AllowedTools fetch, update_entity, delete_entity `
            -Description 'Demo-only Work IQ governance probe: reads, and writes against an item that does not exist.'
    }
    Invoke-Script "Toolbox $($toolboxes.Drafts) (approval-gated drafts)" {
        & (Join-Path $PSScriptRoot 'Set-AdvisorToolbox.ps1') -ToolboxName $toolboxes.Drafts -AllowedTools create_entity `
            -Description 'Approval-gated draft email: Work IQ create_entity only, called after the advisor approves.'
    }
}

if ($Step -contains 'Fabric') {
    Write-Step 'Fabric'
    if (-not $fabricEnabled) { Write-Output 'Skipped: no Fabric capacity (fabric_capacity_sku and existing_fabric_capacity_id are blank).' }
    else {
        Invoke-Script 'Fabric lakehouse, portfolio data and data agent' { & (Join-Path $repoRoot 'data\fabric\Deploy-FabricPortfolio.ps1') }
        Invoke-Script 'Fabric IQ connection' { & (Join-Path $PSScriptRoot 'Set-FabricIqConnection.ps1') }
        Invoke-Script "Toolbox $($toolboxes.Fabric)" {
            & (Join-Path $PSScriptRoot 'Set-AdvisorToolbox.ps1') -FabricOnly -ToolboxName $toolboxes.Fabric
        }
    }
}

if ($Step -contains 'FoundryIq') {
    Write-Step 'FoundryIq'
    Invoke-Script 'Foundry IQ firm library: index, knowledge base, connection and toolbox' {
        & (Join-Path $PSScriptRoot 'Set-FoundryIqKnowledge.ps1') -ToolboxName $toolboxes.FoundryIq
    }
}

if ($Step -contains 'WebIq') {
    Write-Step 'WebIq'
    $webIqName = (Read-KeyValues $tfvarsFile '^\s*(webiq_toolbox_name)\s*=\s*"(.*)"\s*$')['webiq_toolbox_name']
    if (-not $webIqName) { Write-Output 'Skipped: no Web IQ key. Add it later with -WebIqApiKey and -Step Config, WebIq, Agent.' }
    else {
        Invoke-Script 'Web IQ connection' { & (Join-Path $PSScriptRoot 'Set-WebIqConnection.ps1') -ApiKey $WebIqApiKey }
        Invoke-Script "Toolbox $webIqName (Web IQ, public web)" {
            & (Join-Path $PSScriptRoot 'Set-AdvisorToolbox.ps1') -WebIqOnly -ToolboxName $webIqName
        }
    }
}

if ($Step -contains 'Agent') {
    Write-Step 'Agent'
    Invoke-Script "Agent image advisor-$stamp" { & (Join-Path $PSScriptRoot 'Publish-AgentImage.ps1') -Tag "advisor-$stamp" }
}

if ($Step -contains 'Ui') {
    Write-Step 'Ui'
    Invoke-Script "UI image ui-$stamp" { & (Join-Path $PSScriptRoot 'Publish-Ui.ps1') -Tag "ui-$stamp" }
}

if ($Step -contains 'Quality') {
    Write-Step 'Quality'
    $quality = Join-Path $repoRoot 'evals\Invoke-FoundryQuality.ps1'
    Invoke-Script 'Rubric evaluator advisor-team-quality' { & $quality rubric }
    Invoke-Script 'Insights monitor with daily scans' { & $quality insights -ListOnly -ScheduleHours 24 }
    Invoke-Script 'Optimizer run on advisor-brief-writer' { & $quality optimize }
}

if ($Step -contains 'Content') {
    Write-Step 'Content'
    Invoke-Script 'Microsoft 365 demo content (Friday, OneDrive, Meeting)' {
        & (Join-Path $repoRoot 'data\hydration\Publish-DemoContent.ps1') -Stage Friday, OneDrive, Meeting
    }
}

if ($Step -contains 'Prewarm') {
    Write-Step 'Prewarm'
    Invoke-Script 'Prewarm' { & (Join-Path $PSScriptRoot 'Start-DemoPrewarm.ps1') }
}

if ($Step -contains 'Verify') {
    Write-Step 'Verify'
    $principals = terraform "-chdir=$tf" output -json principals 2>$null
    if ($principals) { $principals | Set-Content -LiteralPath (Join-Path $env:TEAM_WORK_DIR 'principals.json') -Encoding utf8 }
    if ($ExistingFoundryAccountId -or $ExistingSearchServiceId -or $ExistingFabricCapacityId) {
        Invoke-Script 'Inventory diff: nothing existing changed' { & (Join-Path $PSScriptRoot 'Compare-SharedInventory.ps1') }
    } else { Write-Output 'Skipped: no shared resources (everything was created from scratch).' }
}

if (-not ($Step -contains 'Content' -or $Step -contains 'Prewarm')) { return }
$ui = (terraform "-chdir=$tf" output -raw -no-color ui_url 2>$null)
if (-not $ui) { $ui = 'terraform output ui_url' }
$meeting = Get-StoryMeetingDate
$day = { param([int]$Offset) $meeting.AddDays($Offset).ToString('dddd yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture) }
$liveAgent = (terraform "-chdir=$tf" output -raw -no-color agent_name 2>$null)
Write-Output ''
Write-Output '=== Steps that need a person ==='
Write-Output @"
1. Work IQ for the tenant: the users need usage-based billing for the Work IQ API (a Copilot Credits spending
   policy that includes it). In the Microsoft 365 admin center > Agents > Tools > Registry (type MCP Server) >
   Work IQ MCP > Policies > Tenant Default: Directory lookups off; Allow write actions and Allow create on;
   partial update, replace and delete off. Changes can take a while to apply.
2. Consent, once per user: open the demo app ($ui) as the advisor and select Authorize on the
   consent banner, then Retry; repeat as the analyst ($AnalystUpn) in a separate browser profile.
3. Record the review on $(& $day -6) (or any time before the demo): hydration\meeting-script.md (the analyst
   opens the Teams meeting the Content step created, both join, record and read the script). Then the analyst
   and the advisor exchange the Teams messages in the script.
4. On or after $(& $day -3): .\data\hydration\Publish-DemoContent.ps1 -Stage Monday -ReplaceFiles 'IPS v2 DRAFT.docx', then save the baseline:
   .\scripts\Invoke-Agent.ps1 -Prompt "$(Convert-StoryText "Save Friday's Q4 portfolio review with Michael Scott as our last discussion.")"
5. Quality loop (after consent; about an hour): .\evals\Invoke-QualityLoop.ps1 -Stage Release, then
   -Stage Evaluate, Insights -Label before; -Stage Optimize; -Stage Status, Apply, Release; then
   -Stage Evaluate, Compare -Label after -Before before; -Stage Promote when the candidate wins. The candidate agent
   ($liveAgent-candidate) needs its own Work IQ consent. Evaluate on or after $(& $day -3): earlier, the story's
   Monday items are still in the future and the agent rightly leaves them out. Daily Insights scans start once the
   agent has traces: .\evals\Invoke-FoundryQuality.ps1 insights -ListOnly -ScheduleHours 24. See docs\QUALITY-LOOP.md.
6. Demo day, $(& $day -2) or $(& $day -1): .\data\hydration\Publish-DemoContent.ps1 -Stage Live minutes before, and
   .\scripts\Start-DemoPrewarm.ps1. The client meeting in the story is $(& $day 0), 10:00 AM Eastern.
"@