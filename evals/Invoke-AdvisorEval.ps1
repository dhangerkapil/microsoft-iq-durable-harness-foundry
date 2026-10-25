<#
.SYNOPSIS
    Runs the advisor evaluation as the signed-in advisor and, with -Foundry, scores the stored
    responses in Microsoft Foundry with the Output Quality, Tool Use Quality and Task Adherence judges.

.DESCRIPTION
    1. Sends each case in evals/advisor-cases.json to the hosted agent (new or continued conversation).
    2. Scores every answer locally: all 'expect' patterns must match and no 'forbid' pattern may match.
    3. With -Foundry, creates (once) a Foundry evaluation and a run over the stored response IDs, waits
       for it, and prints per-criterion results. Results also appear under Evaluations in the portal.
    Results are written to the isolated work folder; nothing is committed.
#>
[CmdletBinding()]
param(
    [string[]]$Case,
    [switch]$Foundry,
    [int]$TimeoutSec = 900
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $repoRoot 'scripts\Use-IsolatedAzure.ps1') -RequireLogin
. (Join-Path $repoRoot 'scripts\AdvisorCommon.ps1')
$tf = Join-Path $repoRoot 'terraform'
$project = (terraform "-chdir=$tf" output -raw -no-color foundry_project_endpoint).Trim()
$agent = (terraform "-chdir=$tf" output -raw -no-color agent_name).Trim()
$model = (terraform "-chdir=$tf" output -raw -no-color chat_model_deployment).Trim()
$agentUrl = "$project/agents/$agent/endpoint/protocols/openai"
$cases = (Convert-StoryText (Get-Content (Join-Path $PSScriptRoot 'advisor-cases.json') -Raw) | ConvertFrom-Json).cases
if ($Case) { $cases = $cases | Where-Object { $Case -contains $_.id } }
$outDir = Join-Path $env:TEAM_WORK_DIR 'evals'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$resultsFile = Join-Path $outDir "advisor-eval-$stamp.jsonl"

function Get-Headers { @{ Authorization = "Bearer $(Get-AzToken 'https://ai.azure.com')" } }
$headers = Get-Headers
$conversation = $null
$results = foreach ($c in $cases) {
    if ($c.session -ne 'continue' -or -not $conversation) {
        $conversation = (Invoke-RestMethod -Method Post -Uri "$agentUrl/conversations?api-version=v1" -Headers $headers -ContentType 'application/json' -Body '{}').id
    }
    $started = Get-Date
    $body = @{ input = $c.prompt; conversation = $conversation; store = $true } | ConvertTo-Json
    try {
        $response = Invoke-RestMethod -Method Post -Uri "$agentUrl/responses?api-version=v1" -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec $TimeoutSec
    } catch {
        $headers = Get-Headers
        $response = [pscustomobject]@{ id = $null; status = 'error'; output = @(); error = $_.ErrorDetails.Message }
    }
    $consent = @($response.output | Where-Object type -eq 'oauth_consent_request')
    if ($consent) {
        Write-Host 'Consent is required before the evaluation can run. Open this link in the InPrivate window, then rerun:'
        Write-Host $consent[0].consent_link
        exit 3
    }
    $text = (@($response.output | Where-Object type -eq 'message' | ForEach-Object { $_.content } | Where-Object text | ForEach-Object text) -join "`n")
    $tools = @($response.output | Where-Object { $_.type -in 'function_call', 'mcp_call' } | ForEach-Object { $_.name } | Where-Object { $_ })
    $missing = @($c.expect | Where-Object { $text -notmatch "(?i)$_" })
    $violations = @($c.forbid | Where-Object { $_ -and $text -match "(?i)$_" })
    $record = [ordered]@{
        id = $c.id; category = $c.category; prompt = $c.prompt; response_id = $response.id; status = $response.status
        passed = ($response.status -eq 'completed' -and $missing.Count -eq 0 -and $violations.Count -eq 0)
        missing = $missing; violations = $violations; tools = $tools
        seconds = [int]((Get-Date) - $started).TotalSeconds; answer = $text
    }
    $record | ConvertTo-Json -Depth 6 -Compress | Add-Content -Path $resultsFile -Encoding utf8
    Write-Host ("{0} {1,-5} {2,4}s tools={3} {4}" -f $c.id, $(if ($record.passed) { 'PASS' } else { 'FAIL' }), $record.seconds,
        $tools.Count, $(if ($missing) { "missing: $($missing -join ' | ')" } elseif ($violations) { "forbidden: $($violations -join ' | ')" } else { '' }))
    [pscustomobject]$record
}
$passed = @($results | Where-Object passed).Count
Write-Output "Local checks: $passed / $(@($results).Count) passed. Details: $resultsFile"
if (-not $Foundry) { return }

# Score the stored responses in Foundry.
$evalBase = "$project/openai/v1/evals"
$evalId = $env:ADVISOR_EVAL_ID
if (-not $evalId) {
    $judge = @{ deployment_name = $model; evaluation_level = 'turn' }
    $evalBody = @{
        name               = 'Advisor team - stored responses'
        data_source_config = @{ type = 'azure_ai_source'; scenario = 'responses' }
        testing_criteria   = @(
            @{ type = 'azure_ai_evaluator'; name = 'output_quality'; evaluator_name = 'builtin.output_quality'; initialization_parameters = $judge },
            @{ type = 'azure_ai_evaluator'; name = 'tool_use_quality'; evaluator_name = 'builtin.tool_use_quality'; initialization_parameters = $judge },
            @{ type = 'azure_ai_evaluator'; name = 'task_adherence'; evaluator_name = 'builtin.task_adherence'; initialization_parameters = @{ deployment_name = $model } }
        )
    } | ConvertTo-Json -Depth 8
    $evalId = (Invoke-RestMethod -Method Post -Uri $evalBase -Headers (Get-Headers) -ContentType 'application/json' -Body $evalBody).id
    Set-EnvLocalValue @{ ADVISOR_EVAL_ID = $evalId }
}
$items = @($results | Where-Object response_id | ForEach-Object { @{ item = @{ resp_id = $_.response_id } } })
$runBody = @{
    name        = "advisor-eval-$stamp"
    data_source = @{
        type                   = 'azure_ai_responses'
        item_generation_params = @{
            type   = 'response_retrieval'; data_mapping = @{ response_id = '{{item.resp_id}}' }
            source = @{ type = 'file_content'; content = $items }
        }
    }
} | ConvertTo-Json -Depth 10
$run = Invoke-RestMethod -Method Post -Uri "$evalBase/$evalId/runs" -Headers (Get-Headers) -ContentType 'application/json' -Body $runBody
Write-Output "Foundry evaluation run $($run.id) started for $($items.Count) responses."
do {
    Start-Sleep -Seconds 20
    $run = Invoke-RestMethod -Method Get -Uri "$evalBase/$evalId/runs/$($run.id)" -Headers (Get-Headers)
} while ($run.status -in 'queued', 'in_progress', 'running')
Write-Output "Foundry run status: $($run.status)"
$run.per_testing_criteria_results | ForEach-Object { '  {0,-18} passed {1} failed {2}' -f $_.testing_criteria, $_.passed, $_.failed }
if ($run.report_url) { Write-Output "Report: $($run.report_url)" }
