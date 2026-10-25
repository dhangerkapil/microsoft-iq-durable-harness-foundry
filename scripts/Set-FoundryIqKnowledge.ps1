<#
.SYNOPSIS
    Foundry IQ: publishes the firm library and the knowledge base the agent searches with search_firm_knowledge.

.DESCRIPTION
    Needs the Terraform Foundry IQ resources (foundry_iq_enabled = true): Azure AI Search and the embedding
    deployment. Idempotent; rerun after changing the library.
      1. Builds the Contoso Wealth firm library: six fictional policy and research PDFs (dates follow
         STORY_MEETING_DATE) and one chunk per section with its document code, section and page.
      2. Creates the search index (hybrid: text, vectors with a query-time vectorizer that calls the embedding
         deployment as the search service's managed identity, and a semantic configuration) and pushes the
         sections with their embeddings. No storage account or indexer is involved, so this works where policy
         turns off public network access to storage.
      3. Creates the search index knowledge source and the knowledge base: agentic retrieval with query planning by
         the agent's chat model (low reasoning effort) and extractive output, so the agent writes the answer and
         cites the passages.
      4. Creates the project connection to the knowledge base's MCP endpoint (the project's managed identity,
         audience https://search.azure.com/) and the toolbox advisor-foundryiq (knowledge_base_retrieve only).
      5. Asks the knowledge base one question and lists the toolbox's tools.
    Records FOUNDRY_IQ_MCP_URL, FOUNDRY_IQ_CONNECTION_ID and FOUNDRY_IQ_KB_NAME in .env.local.

.EXAMPLE
    .\scripts\Set-FoundryIqKnowledge.ps1
#>
[CmdletBinding()]
param(
    [string]$IndexName = 'team-firm-library-index',
    [string]$KnowledgeSourceName = 'team-firm-library-ks',
    [string]$KnowledgeBaseName = 'team-firm-knowledge',
    # Default: firm-knowledge-kb (plus this deployment's suffix in a shared Foundry account).
    [string]$ConnectionName = '',
    [string]$ToolboxName = 'team-foundryiq',
    [ValidateSet('minimal', 'low', 'medium')][string]$ReasoningEffort = 'low'
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
. "$PSScriptRoot\AdvisorCommon.ps1"
if (-not $ConnectionName) { $ConnectionName = Get-ProjectConnectionName 'firm-knowledge-kb' }
$repoRoot = Split-Path $PSScriptRoot -Parent
$tf = Join-Path $repoRoot 'terraform'
function Get-TfOutput([string]$Name) { $v = terraform "-chdir=$tf" output -raw -no-color $Name 2>$null; if ($LASTEXITCODE -eq 0) { "$v".Trim() } else { '' } }
$apiVersion = '2026-08-01-preview'

$search = Get-TfOutput 'foundry_iq_search_endpoint'
if (-not $search) { throw 'Foundry IQ is not provisioned: set foundry_iq_enabled = true in terraform.tfvars and run the Platform step.' }
$embedding = Get-TfOutput 'embedding_model_deployment'
$aoai = Get-TfOutput 'foundry_account_endpoint'
$chatModel = Get-TfOutput 'chat_model_deployment'
$projectId = Get-TfOutput 'foundry_project_id'
$accountId = ($projectId -split '/projects/')[0]
$chatModelName = az rest --method get --url "https://management.azure.com$accountId/deployments/${chatModel}?api-version=2024-10-01" --query properties.model.name -o tsv 2>$null
if (-not $chatModelName) { $chatModelName = $chatModel }

function Invoke-Search([string]$Method, [string]$Path, $Body, [int]$Attempts = 15) {
    for ($attempt = 1; ; $attempt++) {
        $request = @{ Method = $Method; Uri = "$search$Path"; ContentType = 'application/json'
                      Headers = @{ Authorization = "Bearer $(Get-AzToken 'https://search.azure.com')" } }
        if ($null -ne $Body) { $request.Body = ($Body | ConvertTo-Json -Depth 20 -Compress) }
        try { return Invoke-RestMethod @request } catch {
            $code = $_.Exception.Response.StatusCode.value__
            # New role assignments take a few minutes to reach Azure AI Search.
            if ($code -in 401, 403, 429, 503 -and $attempt -lt $Attempts) { Start-Sleep -Seconds 20; continue }
            throw "Azure AI Search $Method $Path failed ($code): $($_.ErrorDetails.Message)"
        }
    }
}

# 1. The firm library and its sections.
$out = Join-Path $env:TEAM_WORK_DIR 'firm-library'
Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
if (-not $env:NODE_PATH) { $env:NODE_PATH = Join-Path $env:TEAM_WORK_DIR 'docgen\node_modules' }
if (-not (Test-Path (Join-Path $env:NODE_PATH 'pdfkit'))) {
    $docgen = Split-Path $env:NODE_PATH -Parent
    New-Item -ItemType Directory -Force -Path $docgen | Out-Null
    Push-Location $docgen
    try { if (-not (Test-Path package.json)) { npm init -y *> $null }; npm install pdfkit@0.17 --no-audit --no-fund *> $null } finally { Pop-Location }
}
node (Join-Path $repoRoot 'data\hydration\documents\build-firm-library.cjs') $out
if ($LASTEXITCODE -ne 0) { throw 'Building the firm library failed.' }
$chunks = @(Get-Content (Join-Path $out 'firm-library.json') -Raw | ConvertFrom-Json)

# 2. The index: text and vectors (hybrid), a query-time vectorizer and a semantic configuration.
$dimensions = 3072
Invoke-Search Put "/indexes/${IndexName}?api-version=$apiVersion" @{
    name = $IndexName
    fields = @(
        @{ name = 'id'; type = 'Edm.String'; key = $true; filterable = $true }
        @{ name = 'doc_code'; type = 'Edm.String'; searchable = $true; filterable = $true; facetable = $true }
        @{ name = 'title'; type = 'Edm.String'; searchable = $true }
        @{ name = 'section'; type = 'Edm.String'; searchable = $true }
        @{ name = 'effective'; type = 'Edm.String'; searchable = $false }
        @{ name = 'owner'; type = 'Edm.String'; searchable = $false }
        @{ name = 'source_file'; type = 'Edm.String'; searchable = $false; filterable = $true }
        @{ name = 'page'; type = 'Edm.Int32'; filterable = $true }
        @{ name = 'content'; type = 'Edm.String'; searchable = $true }
        @{ name = 'content_vector'; type = 'Collection(Edm.Single)'; searchable = $true; retrievable = $false; stored = $false
           dimensions = $dimensions; vectorSearchProfile = 'library-vectors' }
    )
    vectorSearch = @{
        algorithms = @(@{ name = 'hnsw'; kind = 'hnsw' })
        vectorizers = @(@{ name = 'embedding'; kind = 'azureOpenAI'
                           azureOpenAIParameters = @{ resourceUri = $aoai; deploymentId = $embedding; modelName = $embedding } })
        profiles = @(@{ name = 'library-vectors'; algorithm = 'hnsw'; vectorizer = 'embedding' })
    }
    semantic = @{
        defaultConfiguration = 'library'
        configurations = @(@{ name = 'library'; prioritizedFields = @{
            titleField = @{ fieldName = 'section' }
            prioritizedContentFields = @(@{ fieldName = 'content' })
            prioritizedKeywordsFields = @(@{ fieldName = 'doc_code' }, @{ fieldName = 'title' }) } })
    }
} | Out-Null
Write-Output "Index $IndexName ($($chunks.Count) sections to push)."

# Embeddings for the sections, as the signed-in user, then push them.
$embedBody = @{ model = $embedding; input = @($chunks | ForEach-Object content) } | ConvertTo-Json -Depth 5
$vectors = (Invoke-RestMethod -Method Post -Uri "$aoai/openai/v1/embeddings" -ContentType 'application/json' -Body $embedBody `
    -Headers @{ Authorization = "Bearer $(Get-AzToken 'https://cognitiveservices.azure.com')" } -TimeoutSec 120).data
$documents = for ($i = 0; $i -lt $chunks.Count; $i++) {
    $c = $chunks[$i]
    [ordered]@{ '@search.action' = 'mergeOrUpload'; id = $c.id; doc_code = $c.doc_code; title = $c.title; section = $c.section
                effective = $c.effective; owner = $c.owner; source_file = $c.source_file; page = [int]$c.page
                content = $c.content; content_vector = @($vectors[$i].embedding) }
}
$pushed = Invoke-Search Post "/indexes/$IndexName/docs/index?api-version=$apiVersion" @{ value = @($documents) }
$failed = @($pushed.value | Where-Object { -not $_.status })
if ($failed) { throw "Pushing $($failed.Count) section(s) failed: $(($failed | ForEach-Object errorMessage) -join '; ')" }
Write-Output "Pushed $(@($pushed.value).Count) sections with $dimensions-dimension embeddings."

# 3. Knowledge source over the index, and the knowledge base.
Invoke-Search Put "/knowledgesources/${KnowledgeSourceName}?api-version=$apiVersion" @{
    name = $KnowledgeSourceName; kind = 'searchIndex'
    description = 'Contoso Wealth firm library: policies, procedures, the approved product shelf, planning guides and the investment committee house view (fictional).'
    searchIndexParameters = @{
        searchIndexName = $IndexName; semanticConfigurationName = 'library'
        sourceDataFields = @('doc_code', 'title', 'section', 'effective', 'source_file', 'page' | ForEach-Object { @{ name = $_ } })
        searchFields = @(@{ name = 'content' }, @{ name = 'section' })
    }
} | Out-Null
$models = if ($ReasoningEffort -eq 'minimal') { @() } else {
    @(@{ kind = 'azureOpenAI'; azureOpenAIParameters = @{ resourceUri = $aoai; deploymentId = $chatModel; modelName = $chatModelName } })
}
Invoke-Search Put "/knowledgebases/${KnowledgeBaseName}?api-version=$apiVersion" @{
    name = $KnowledgeBaseName
    description = 'Contoso Wealth firm knowledge for advisors: what the firm requires (policies and procedures), what it offers (approved products) and what it thinks (house view). Fictional demo content.'
    retrievalInstructions = 'Prefer the policy or procedure that governs the question (CP-104 concentrated positions, OPS-220 large withdrawals and wires, COM-310 client communications, ESG-201 approved ESG funds, EDU-115 529 plans); use the house view (IC-HV) only for market views.'
    outputMode = 'extractiveData'
    knowledgeSources = @(@{ name = $KnowledgeSourceName })
    models = @($models)
    retrievalReasoningEffort = @{ kind = $ReasoningEffort }
} | Out-Null
Write-Output "Knowledge source $KnowledgeSourceName and knowledge base $KnowledgeBaseName (retrieval reasoning effort: $ReasoningEffort)."

$check = Invoke-Search Post "/knowledgebases/$KnowledgeBaseName/retrieve?api-version=$apiVersion" @{
    messages = @(@{ role = 'user'; content = @(@{ type = 'text'; text = 'What must happen before a large wire for a home closing is released?' }) })
}
$found = @($check.references | ForEach-Object { "$($_.sourceData.doc_code) $($_.sourceData.section)" }) | Select-Object -Unique
Write-Output "Retrieve check: $(@($check.references).Count) reference(s): $($found -join '; ')"

# 4. Project connection to the knowledge base's MCP endpoint, then the toolbox.
$mcpUrl = "$search/knowledgebases/$KnowledgeBaseName/mcp?api-version=$apiVersion"
$connectionId = "$projectId/connections/$ConnectionName"
$bodyFile = Join-Path $env:TEAM_WORK_DIR 'foundryiq-connection.json'
@{
    name = $ConnectionName; type = 'Microsoft.MachineLearningServices/workspaces/connections'
    properties = @{ authType = 'ProjectManagedIdentity'; category = 'RemoteTool'; target = $mcpUrl; isSharedToAll = $true
                    audience = 'https://search.azure.com/'; metadata = @{ ApiType = 'Azure' } }
} | ConvertTo-Json -Depth 6 | Set-Content -Path $bodyFile -Encoding utf8
az rest --method put --url "https://management.azure.com${connectionId}?api-version=2025-10-01-preview" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
if ($LASTEXITCODE -ne 0) { throw "Creating the project connection $ConnectionName failed." }
Set-EnvLocalValue @{ FOUNDRY_IQ_MCP_URL = $mcpUrl; FOUNDRY_IQ_CONNECTION_ID = $connectionId; FOUNDRY_IQ_KB_NAME = $KnowledgeBaseName }
Write-Output "Project connection $ConnectionName -> $mcpUrl"
& "$PSScriptRoot\Set-AdvisorToolbox.ps1" -FoundryIqOnly -ToolboxName $ToolboxName

# 5. The toolbox's tools, as the agent will see them.
& "$PSScriptRoot\Test-ToolboxTool.ps1" -Toolbox $ToolboxName | Select-String -Pattern '^(server|tool|  input):' | ForEach-Object { "  $($_.Line)" }
