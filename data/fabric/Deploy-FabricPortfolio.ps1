<#
.SYNOPSIS
    Deploys the advisor portfolio lakehouse and the Portfolio Analyst data agent to Microsoft Fabric.

.DESCRIPTION
    Idempotent; safe to rerun. Uses the isolated Azure CLI sign-in for Fabric and OneLake tokens:
      1. Finds the Terraform-managed Fabric capacity and resumes it when it is paused.
      2. Creates or reuses the workspace (assigned to that capacity) and the lakehouse.
      3. Converts fabric/notebooks/*.py (# %% cells) to notebooks and creates or updates them.
      4. Runs advisor_portfolio_data, refreshes the SQL analytics endpoint, then creates the data agent
         with advisor_data_agent unless it already exists (-RecreateAgent rebuilds it).
      5. Saves the story numbers to fabric/portfolio-story.json (ground truth for content and evals)
         and records FABRIC_* values, including the data agent MCP URL, in .env.local.
    A rebuilt data agent gets a new ID. The script then recreates the Fabric IQ connection and republishes
    the Fabric IQ check toolbox (Set-AdvisorToolbox.ps1 -FabricOnly -ToolboxName advisor-fabric-check), and
    every user must consent to Fabric again. The Work IQ toolbox is not changed.

.EXAMPLE
    .\data\fabric\Deploy-FabricPortfolio.ps1
    .\data\fabric\Deploy-FabricPortfolio.ps1 -SkipData      # keep the tables; only check the data agent
#>
[CmdletBinding()]
param(
    # Default: FABRIC_WORKSPACE_NAME from .env.local, else "Advisor Team".
    [string]$WorkspaceName,
    [string]$LakehouseName = 'team_portfolio',
    [string]$DataAgentName = 'Team Portfolio Analyst',
    [switch]$SkipData,
    [switch]$SkipAgent,
    [switch]$RecreateAgent
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $repoRoot 'scripts\Use-IsolatedAzure.ps1') -RequireLogin
. (Join-Path $repoRoot 'scripts\AdvisorCommon.ps1')
if (-not $WorkspaceName) {
    $WorkspaceName = if ($env:FABRIC_WORKSPACE_NAME) { $env:FABRIC_WORKSPACE_NAME } else { 'Advisor Team' }
}
$previousMcpUrl = $env:FABRIC_DATA_AGENT_MCP_URL
$tf = Join-Path $repoRoot 'terraform'
# Prefer .env.local so this script never touches Terraform state while an apply holds its lock.
$capacityName = if ($env:FABRIC_CAPACITY_NAME) { $env:FABRIC_CAPACITY_NAME } else { (terraform "-chdir=$tf" output -raw -no-color fabric_capacity_name).Trim() }
$capacityArmId = if ($env:FABRIC_CAPACITY_ID) { $env:FABRIC_CAPACITY_ID } else { (terraform "-chdir=$tf" output -raw -no-color fabric_capacity_id).Trim() }
$api = 'https://api.fabric.microsoft.com/v1'

function Update-FabricToken { $script:fabricHeaders = @{ Authorization = "Bearer $(Get-AzToken 'https://api.fabric.microsoft.com')" } }

function Invoke-Fabric {
    param([string]$Method = 'Get', [string]$Path, $Body, [int]$TimeoutMinutes = 30)
    $uri = if ($Path -match '^https://') { $Path } else { "$api$Path" }
    $request = @{
        Method = $Method; Uri = $uri; Headers = $script:fabricHeaders; ContentType = 'application/json'
        ResponseHeadersVariable = 'responseHeaders'; StatusCodeVariable = 'statusCode'
    }
    if ($null -ne $Body) { $request.Body = ($Body | ConvertTo-Json -Depth 30 -Compress) }
    for ($attempt = 1; ; $attempt++) {
        try { $result = Invoke-RestMethod @request; break } catch {
            $code = $_.Exception.Response.StatusCode.value__
            if (($code -eq 429 -or $code -ge 500) -and $attempt -lt 6) { Start-Sleep -Seconds (10 * $attempt); continue }
            throw "Fabric $Method $uri failed ($code): $($_.ErrorDetails.Message)"
        }
    }
    if ($statusCode -ne 202 -or -not $responseHeaders.Location) { return $result }
    $location = [string]@($responseHeaders.Location)[0]
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while ($true) {
        $wait = if ($responseHeaders.'Retry-After') { [int]@($responseHeaders.'Retry-After')[0] } else { 5 }
        Start-Sleep -Seconds ([Math]::Min([Math]::Max($wait, 3), 20))
        $op = Invoke-RestMethod -Uri $location -Headers $script:fabricHeaders -ResponseHeadersVariable responseHeaders
        if ($op.status -in 'Succeeded', 'Completed') { break }
        if ($op.status -in 'Failed', 'Cancelled', 'Deduped') {
            throw "Fabric operation $($op.status): $($op | ConvertTo-Json -Depth 10 -Compress)"
        }
        if ((Get-Date) -gt $deadline) { throw "Fabric operation timed out: $location" }
    }
    if ($location -match '/operations/[^/?]+$') {
        try { return Invoke-RestMethod -Uri "$location/result" -Headers $script:fabricHeaders } catch { return $op }
    }
    $op
}

function Find-Item([string]$Path, [string]$Name) {
    @((Invoke-Fabric -Path $Path).value | Where-Object displayName -eq $Name)[0]
}

function Read-OneLakeText([string]$RelativePath) {
    $headers = @{ Authorization = "Bearer $(Get-AzToken 'https://storage.azure.com')"; 'x-ms-version' = '2023-11-03' }
    $uri = "https://onelake.dfs.fabric.microsoft.com/$($workspace.id)/$($lakehouse.id)/$RelativePath"
    $content = (Invoke-WebRequest -Uri $uri -Headers $headers).Content
    if ($content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($content) } else { $content }
}

function ConvertTo-NotebookDefinition([string]$SourcePath, [hashtable]$Replace, [switch]$StoryText) {
    $text = Get-Content -LiteralPath $SourcePath -Raw
    # Story dates written as text (instructions, example queries) move with STORY_MEETING_DATE.
    if ($StoryText) { $text = Convert-StoryText $text }
    foreach ($key in $Replace.Keys) { $text = $text.Replace($key, $Replace[$key]) }
    $cells = [System.Collections.Generic.List[object]]::new()
    $current = $null
    foreach ($line in ($text -split "`r?`n")) {
        if ($line -match '^# %%( \[markdown\])?\s*$') {
            if ($current) { $cells.Add($current) }
            $current = @{ Markdown = [bool]$Matches[1]; Lines = [System.Collections.Generic.List[string]]::new() }
            continue
        }
        if (-not $current) { $current = @{ Markdown = $false; Lines = [System.Collections.Generic.List[string]]::new() } }
        $current.Lines.Add($line)
    }
    if ($current) { $cells.Add($current) }
    $notebookCells = foreach ($cell in $cells) {
        $lines = [System.Collections.Generic.List[string]]::new($cell.Lines)
        while ($lines.Count -gt 0 -and [string]::IsNullOrWhiteSpace($lines[$lines.Count - 1])) { $lines.RemoveAt($lines.Count - 1) }
        if ($lines.Count -eq 0) { continue }
        if ($cell.Markdown) { for ($i = 0; $i -lt $lines.Count; $i++) { $lines[$i] = $lines[$i] -replace '^# ?', '' } }
        $source = for ($i = 0; $i -lt $lines.Count; $i++) { if ($i -lt $lines.Count - 1) { "$($lines[$i])`n" } else { $lines[$i] } }
        if ($cell.Markdown) {
            [ordered]@{ cell_type = 'markdown'; metadata = @{}; source = @($source) }
        } else {
            [ordered]@{ cell_type = 'code'; metadata = @{}; source = @($source); outputs = @(); execution_count = $null }
        }
    }
    $notebook = [ordered]@{
        nbformat       = 4
        nbformat_minor = 5
        metadata       = [ordered]@{
            kernel_info   = @{ name = 'synapse_pyspark' }
            kernelspec    = @{ name = 'synapse_pyspark'; display_name = 'Synapse PySpark'; language = 'Python' }
            language_info = @{ name = 'python' }
            dependencies  = @{
                lakehouse = @{
                    default_lakehouse              = $lakehouse.id
                    default_lakehouse_name         = $LakehouseName
                    default_lakehouse_workspace_id = $workspace.id
                }
            }
        }
        cells          = @($notebookCells)
    }
    $payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($notebook | ConvertTo-Json -Depth 20)))
    @{ format = 'ipynb'; parts = @(@{ path = 'notebook-content.ipynb'; payload = $payload; payloadType = 'InlineBase64' }) }
}

function Set-Notebook([string]$Name, [string]$Description, [hashtable]$Definition) {
    $existing = Find-Item "/workspaces/$($workspace.id)/notebooks" $Name
    if ($existing) {
        Invoke-Fabric -Method Post -Path "/workspaces/$($workspace.id)/notebooks/$($existing.id)/updateDefinition" -Body @{ definition = $Definition } | Out-Null
        Write-Host "  updated notebook $Name"
        return $existing.id
    }
    $created = Invoke-Fabric -Method Post -Path "/workspaces/$($workspace.id)/notebooks" -Body @{
        displayName = $Name; description = $Description; definition = $Definition
    }
    if (-not $created.id) { $created = Find-Item "/workspaces/$($workspace.id)/notebooks" $Name }
    Write-Host "  created notebook $Name"
    $created.id
}

function Invoke-Notebook([string]$NotebookId, [string]$Name) {
    Update-FabricToken
    Write-Output "Running $Name (Spark start-up included; usually 3-10 minutes)..."
    $started = Get-Date
    $body = @{ executionData = @{ parameters = @{ _inlineInstallationEnabled = @{ value = 'True'; type = 'bool' } } } }
    try {
        $job = Invoke-Fabric -Method Post -Path "/workspaces/$($workspace.id)/items/$NotebookId/jobs/instances?jobType=RunNotebook" -Body $body -TimeoutMinutes 60
    } catch {
        throw "Notebook $Name failed after $([int]((Get-Date) - $started).TotalMinutes) min: $($_.Exception.Message)"
    }
    Write-Output "  $Name finished in $([int]((Get-Date) - $started).TotalMinutes) min (status $($job.status))."
}

Update-FabricToken

# 1. Capacity
$capacity = @((Invoke-Fabric -Path '/capacities').value | Where-Object displayName -eq $capacityName)[0]
if (-not $capacity) { throw "Fabric capacity $capacityName is not visible to the signed-in admin." }
if ($capacity.state -ne 'Active') {
    Write-Output "Resuming Fabric capacity $capacityName ($($capacity.state))..."
    az rest --method post --url "https://management.azure.com$capacityArmId/resume?api-version=2023-11-01" -o none
    for ($i = 0; $i -lt 40 -and $capacity.state -ne 'Active'; $i++) {
        Start-Sleep -Seconds 15
        $capacity = @((Invoke-Fabric -Path '/capacities').value | Where-Object displayName -eq $capacityName)[0]
    }
    if ($capacity.state -ne 'Active') { throw "Capacity $capacityName did not become active." }
}
Write-Output "Capacity: $capacityName $($capacity.sku) $($capacity.region) $($capacity.state)"

# 2. Workspace and lakehouse
$workspace = Find-Item '/workspaces' $WorkspaceName
if (-not $workspace) {
    $workspace = Invoke-Fabric -Method Post -Path '/workspaces' -Body @{
        displayName = $WorkspaceName; capacityId = $capacity.id
        description = 'Advisor team demo: synthetic client portfolios and the Portfolio Analyst data agent (Fabric IQ).'
    }
    Write-Output "Created workspace $WorkspaceName"
} elseif ($workspace.capacityId -ne $capacity.id) {
    Invoke-Fabric -Method Post -Path "/workspaces/$($workspace.id)/assignToCapacity" -Body @{ capacityId = $capacity.id } | Out-Null
    Write-Output "Assigned workspace $WorkspaceName to $capacityName"
}
$lakehouse = Find-Item "/workspaces/$($workspace.id)/lakehouses" $LakehouseName
if (-not $lakehouse) {
    $lakehouse = Invoke-Fabric -Method Post -Path "/workspaces/$($workspace.id)/lakehouses" -Body @{
        displayName = $LakehouseName; description = 'Synthetic advisor portfolio data (no real client or market data).'
    }
    if (-not $lakehouse.id) { $lakehouse = Find-Item "/workspaces/$($workspace.id)/lakehouses" $LakehouseName }
    Write-Output "Created lakehouse $LakehouseName"
}
for ($i = 0; $i -lt 40; $i++) {
    $lakehouseDetail = Invoke-Fabric -Path "/workspaces/$($workspace.id)/lakehouses/$($lakehouse.id)"
    if ($lakehouseDetail.properties.sqlEndpointProperties.provisioningStatus -eq 'Success') { break }
    Start-Sleep -Seconds 15
}
$sqlEndpoint = $lakehouseDetail.properties.sqlEndpointProperties
Write-Output "Workspace $($workspace.id); lakehouse $($lakehouse.id); SQL endpoint $($sqlEndpoint.provisioningStatus)"
Set-EnvLocalValue @{
    FABRIC_WORKSPACE_ID = $workspace.id; FABRIC_LAKEHOUSE_ID = $lakehouse.id
    FABRIC_SQL_ENDPOINT = $sqlEndpoint.connectionString; FABRIC_CAPACITY_NAME = $capacityName
    FABRIC_CAPACITY_ID = $capacityArmId
}

# 3. Notebooks
$replace = @{
    '__WORKSPACE_ID__' = $workspace.id; '__LAKEHOUSE_ID__' = $lakehouse.id
    '__LAKEHOUSE_NAME__' = $LakehouseName; '__DATA_AGENT_NAME__' = $DataAgentName
    '__STORY_SHIFT_DAYS__' = [string](Get-StoryShiftDays)
}
Write-Output "Story client meeting: Thursday $((Get-StoryMeetingDate).ToString('yyyy-MM-dd')) (STORY_MEETING_DATE; shift $(Get-StoryShiftDays) days)."
$notebooks = Join-Path $PSScriptRoot 'notebooks'
$dataNotebookId = Set-Notebook 'advisor_portfolio_data' 'Generates the synthetic advisor portfolio Delta tables.' `
    (ConvertTo-NotebookDefinition (Join-Path $notebooks 'portfolio_data.py') $replace) | Select-Object -Last 1
$agentNotebookId = Set-Notebook 'advisor_data_agent' 'Creates and publishes the Portfolio Analyst data agent.' `
    (ConvertTo-NotebookDefinition (Join-Path $notebooks 'data_agent.py') $replace -StoryText) | Select-Object -Last 1

# 4. Data, SQL endpoint refresh, data agent
if (-not $SkipData) {
    Invoke-Notebook $dataNotebookId 'advisor_portfolio_data'
    $status = Read-OneLakeText 'Files/setup/portfolio_data_status.json' | ConvertFrom-Json
    $story = [ordered]@{
        generated_utc = $status.finished; tables = $status.tables; michael_scott = $status.michael_scott
        note          = 'Synthetic demo data. Microsoft 365 content and evaluation ground truth must use these numbers.'
    }
    $story | ConvertTo-Json -Depth 10 | Set-Content -Path (Join-Path $PSScriptRoot 'portfolio-story.json') -Encoding utf8
    Write-Output 'Story numbers saved to fabric/portfolio-story.json:'
    $status.michael_scott | Format-List | Out-String -Width 160 | Write-Output
}
Update-FabricToken
try {
    Invoke-Fabric -Method Post -Path "/workspaces/$($workspace.id)/sqlEndpoints/$($sqlEndpoint.id)/refreshMetadata" -Body @{} | Out-Null
    Write-Output 'SQL analytics endpoint metadata refreshed.'
} catch { Write-Warning "SQL endpoint metadata refresh failed; the agent notebook retries table selection. $($_.Exception.Message)" }

if (-not $SkipAgent) {
    $existingAgent = Find-Item "/workspaces/$($workspace.id)/items?type=DataAgent" $DataAgentName
    if ($existingAgent -and -not $RecreateAgent) {
        Write-Output "Keeping the existing data agent $DataAgentName ($($existingAgent.id)). Use -RecreateAgent to rebuild it."
    } else {
        if ($existingAgent) {
            # The SDK's delete uses a retired item type, so remove the agent through the REST API.
            Invoke-Fabric -Method Delete -Path "/workspaces/$($workspace.id)/items/$($existingAgent.id)" | Out-Null
            Write-Output "Deleted the existing data agent $DataAgentName before recreating it."
        }
        Invoke-Notebook $agentNotebookId 'advisor_data_agent'
        $agentStatus = Read-OneLakeText 'Files/setup/data_agent_status.json' | ConvertFrom-Json
        $failed = @($agentStatus.steps | Where-Object { -not $_.ok -and $_.step -ne 'delete existing' })
        if ($failed) { throw "Data agent setup failed: $($failed | ConvertTo-Json -Depth 5 -Compress)" }
    }
}
Update-FabricToken
$dataAgent = Find-Item "/workspaces/$($workspace.id)/items?type=DataAgent" $DataAgentName
if (-not $dataAgent) { throw "Data agent '$DataAgentName' was not found in workspace $WorkspaceName." }

# The SDK selects columns but can leave table nodes unselected, and publish() on recent SDKs does
# nothing. Make both deterministic: select every table in the draft and published configuration,
# add table descriptions, and copy draft to published when no published configuration exists.
$tableDescriptions = @{
    clients              = 'One row per client: client_id, client_name, segment, risk profile, city, state, client since.'
    ips_profiles         = 'Investment policy per client: single-stock and sector limits, annual realized-gain tax budget, liquidity reserve, income need, ESG preference, IPS version and dates.'
    securities           = 'Ticker reference data: security name, sector, asset class, ESG rating.'
    prices_daily         = 'Daily close price per ticker.'
    positions_daily      = 'Daily holdings per client and ticker: quantity, price, market value, weight, cost basis, unrealized gain, limits and breach flags.'
    position_changes     = Convert-StoryText 'Precomputed changes per client and ticker from 2026-06-30, 2026-09-30 or 2026-10-16 to 2026-10-19.'
    client_summary_daily = 'Daily totals per client: total value, cash and equivalents, unrealized gain, largest equity, breach counts.'
    transactions         = 'Trades, dividends, deposits and withdrawals with cash amounts.'
}
function Select-AllElements($elements) {
    foreach ($element in @($elements)) {
        if ($null -eq $element) { continue }
        $element.is_selected = $true
        if ($element.type -like '*.table' -and $tableDescriptions.ContainsKey($element.display_name)) {
            $element.description = $tableDescriptions[$element.display_name]
        }
        if ($element.children) { Select-AllElements $element.children }
    }
}
$definition = Invoke-Fabric -Method Post -Path "/workspaces/$($workspace.id)/dataAgents/$($dataAgent.id)/getDefinition"
$parts = [ordered]@{}
foreach ($part in $definition.definition.parts) {
    if ($part.path -eq '.platform') { continue }
    $parts[$part.path] = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($part.payload))
}
foreach ($path in @($parts.Keys)) {
    if ($path -notlike 'Files/Config/*/datasource.json') { continue }
    $source = $parts[$path] | ConvertFrom-Json -Depth 100
    Select-AllElements $source.elements
    $parts[$path] = $source | ConvertTo-Json -Depth 100
}
if (-not ($parts.Keys | Where-Object { $_ -like 'Files/Config/published/*' })) {
    foreach ($path in @($parts.Keys | Where-Object { $_ -like 'Files/Config/draft/*' })) {
        $parts[$path -replace '^Files/Config/draft/', 'Files/Config/published/'] = $parts[$path]
    }
    $parts['Files/Config/publish_info.json'] = @{
        '$schema'   = 'https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition/publishInfo/1.0.0/schema.json'
        description = 'Portfolio Analyst for the advisor harness demo (synthetic data).'
    } | ConvertTo-Json
}
$updatedParts = foreach ($path in $parts.Keys) {
    @{ path = $path; payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($parts[$path])); payloadType = 'InlineBase64' }
}
Invoke-Fabric -Method Post -Path "/workspaces/$($workspace.id)/dataAgents/$($dataAgent.id)/updateDefinition" -Body @{ definition = @{ parts = @($updatedParts) } } | Out-Null
$publishedParts = @($parts.Keys | Where-Object { $_ -like 'Files/Config/published/*' })
Write-Output "Data agent configuration: all tables selected; $($publishedParts.Count) published part(s)."
$mcpUrl = "https://api.fabric.microsoft.com/v1/mcp/workspaces/$($workspace.id)/dataagents/$($dataAgent.id)/agent"
Set-EnvLocalValue @{ FABRIC_DATA_AGENT_ID = $dataAgent.id; FABRIC_DATA_AGENT_MCP_URL = $mcpUrl }
Write-Output "Data agent '$DataAgentName' published: $($dataAgent.id)"
Write-Output "MCP endpoint: $mcpUrl"

# Smoke test: one question through the data agent's MCP endpoint as the signed-in user.
$mcpHeaders = @{ Authorization = "Bearer $(Get-AzToken 'https://api.fabric.microsoft.com')"; Accept = 'application/json, text/event-stream' }
function Send-Mcp($Message) {
    $response = Invoke-WebRequest -Method Post -Uri $mcpUrl -Headers $mcpHeaders -ContentType 'application/json' `
        -Body ($Message | ConvertTo-Json -Depth 10 -Compress) -SkipHttpErrorCheck -TimeoutSec 300
    $text = if ($response.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($response.Content) } else { [string]$response.Content }
    if (-not $text.TrimStart().StartsWith('{')) {
        $text = ($text -split "`n" | Where-Object { $_ -like 'data:*' } | ForEach-Object { $_.Substring(5).Trim() } | Select-Object -Last 1)
    }
    $text | ConvertFrom-Json -Depth 50
}
$null = Send-Mcp @{ jsonrpc = '2.0'; id = 1; method = 'initialize'; params = @{ protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = @{ name = 'deploy-check'; version = '1' } } }
$mcpTool = (Send-Mcp @{ jsonrpc = '2.0'; id = 2; method = 'tools/list'; params = @{} }).result.tools[0]
$question = Convert-StoryText "What is Michael Scott's NVDA weight on 2026-10-16 and on the latest date, and is it above his single-stock limit?"
$answer = Send-Mcp @{ jsonrpc = '2.0'; id = 3; method = 'tools/call'; params = @{ name = $mcpTool.name; arguments = @{ userQuestion = $question } } }
$answerText = ($answer.result.content | Where-Object type -eq 'text' | ForEach-Object text) -join "`n"
Write-Output "Smoke test ($($mcpTool.name)): $question"
Write-Output $answerText
if ($answer.error -or $answer.result.isError) { throw "The data agent MCP smoke test failed: $answerText $($answer.error | ConvertTo-Json -Compress)" }
if ($previousMcpUrl -and $previousMcpUrl -ne $mcpUrl) {
    Write-Warning 'The data agent has a new ID. Recreating the Fabric IQ connection and the advisor-fabric-check toolbox version; every user must consent to Fabric again.'
    & (Join-Path $repoRoot 'scripts\Set-FabricIqConnection.ps1')
    # Only the Fabric IQ check toolbox uses the data agent; without -FabricOnly this would republish the Work IQ toolbox.
    & (Join-Path $repoRoot 'scripts\Set-AdvisorToolbox.ps1') -FabricOnly -ToolboxName advisor-fabric-check
} elseif (-not $previousMcpUrl) {
    Write-Output 'Next: .\scripts\Set-FabricIqConnection.ps1, then .\scripts\Set-AdvisorToolbox.ps1 -FabricOnly -ToolboxName advisor-fabric-check'
}
