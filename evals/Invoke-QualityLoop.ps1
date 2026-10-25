<#
.SYNOPSIS
    The quality loop for the Investment Advisor Agent with Microsoft IQ: evaluate, find recurring problems with Insights, let the
    agent optimizer improve the instructions, skills and model, apply and release the winner, and measure again.

.DESCRIPTION
    Run one or more stages, in this order. Each stage is a thin wrapper around the scripts it names, so you can also
    run those directly.

      Evaluate  Rehearses the demo prompts that have no side effects (no drafts, no crash test) against -AgentName,
                saves the turns, and scores them with Foundry's built-in judges, the rubric evaluator and the
                prompt-injection judge (evals\Invoke-FoundryQuality.ps1 dataset). The run is saved under -Label.
      Insights  Scans the agent's recent traces with Insights in Foundry and lists recurring problems, each with
                evidence traces and a proposed fix.
      Optimize  Submits an agent-optimizer job for the hosted agent itself: instruction tuning, skill improvement and
                model selection across Terraform's model_search_deployments, scored on evals\hosted-optimizer-cases.json.
                The optimizer calls the agent as you, so Work IQ reads your Microsoft 365 content: consent once first.
      Status    Shows the last optimization job, its candidates and their scores.
      Apply     Writes the best candidate's instructions, skills and model into .agent_configs\baseline. Review the
                change with git diff before releasing it.
      Release   Builds the image and releases it as the candidate agent (scripts\Publish-CandidateAgent.ps1), with
                its own state stores, next to the live agent.
      Compare   Compares the evaluation saved under -Before with the one saved under -Label.
      Promote   Publishes the same code to the live agent (scripts\Publish-AgentImage.ps1). Only after review.

.EXAMPLE
    .\evals\Invoke-QualityLoop.ps1 -Stage Evaluate, Insights -Label before
    .\evals\Invoke-QualityLoop.ps1 -Stage Optimize
    .\evals\Invoke-QualityLoop.ps1 -Stage Status
    .\evals\Invoke-QualityLoop.ps1 -Stage Apply, Release
    .\evals\Invoke-QualityLoop.ps1 -Stage Evaluate, Compare -Label after -Before before
    .\evals\Invoke-QualityLoop.ps1 -Stage Promote
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Evaluate', 'Insights', 'Optimize', 'Status', 'Apply', 'Release', 'Compare', 'Promote')]
    [string[]]$Stage,
    # The agent the loop measures and improves: by default the candidate agent next to the live one
    # (ADVISOR_CANDIDATE_AGENT from .env.local, else <live agent>-candidate).
    [string]$AgentName,
    [ValidatePattern('^[a-z0-9-]+$')][string]$Label = ('q' + (Get-Date -Format 'yyyyMMdd-HHmm')),
    [ValidatePattern('^[a-z0-9-]*$')][string]$Before,
    [ValidateSet('prep', 'chart', 'graph', 'changed', 'fabric')][string[]]$Segment = @('prep', 'chart', 'graph', 'changed'),
    [double]$InsightsHours = 6,
    [int]$Candidates = 3,
    [string[]]$Cases,
    # Apply: apply even if the leakage guard flags the candidate (after you reviewed why).
    [switch]$AllowLeaks
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$quality = Join-Path $PSScriptRoot 'Invoke-FoundryQuality.ps1'
. (Join-Path $repoRoot 'scripts\Use-IsolatedAzure.ps1') -RequireLogin
$tf = Join-Path $repoRoot 'terraform'
$liveAgent = (terraform "-chdir=$tf" output -raw -no-color agent_name).Trim()
if (-not $AgentName) { $AgentName = if ($env:ADVISOR_CANDIDATE_AGENT) { $env:ADVISOR_CANDIDATE_AGENT } else { "$liveAgent-candidate" } }
# The configuration the loop tunes: the team's by default (TEAM_MODE=solo tunes the single agent's).
$configDir = Join-Path $repoRoot ".agent_configs\$(if ($env:TEAM_MODE) { $env:TEAM_MODE } else { 'team' })\baseline"
$order = 'Evaluate', 'Insights', 'Optimize', 'Status', 'Apply', 'Release', 'Compare', 'Promote'

foreach ($name in $order | Where-Object { $Stage -contains $_ }) {
    Write-Output ''
    Write-Output "=== $name ($AgentName) ==="
    switch ($name) {
        'Evaluate' {
            $prefix = "q-$Label"
            & (Join-Path $repoRoot 'scripts\Invoke-DemoRehearsal.ps1') -AgentName $AgentName -SavePrefix $prefix -Segment $Segment
            $saved = @(Get-ChildItem $env:TEAM_WORK_DIR -Filter "$prefix-*.json" | ForEach-Object BaseName)
            if (-not $saved) { throw "No turns were saved for $prefix (see the rehearsal output above)." }
            & $quality dataset -Names $saved -Safety -Label $Label -Agent $AgentName
        }
        'Insights' { & $quality insights -Hours $InsightsHours -Agent $AgentName }
        'Optimize' {
            $optimizeArgs = @{ Agent = $AgentName; Candidates = $Candidates }
            if ($Cases) { $optimizeArgs.Cases = $Cases }
            & $quality optimize-hosted @optimizeArgs
        }
        'Status' { & $quality optimizer -Agent $AgentName }
        'Apply' {
            & $quality apply -Agent $AgentName -AllowLeaks:$AllowLeaks
            git -C $repoRoot --no-pager diff --stat -- .agent_configs
        }
        'Release' {
            # An applied model change lives in metadata.yaml; the candidate runs on it explicitly.
            $model = ((Get-Content (Join-Path $configDir 'metadata.yaml')) |
                Where-Object { $_ -match '^model:\s*(\S+)' } | ForEach-Object { $Matches[1] } | Select-Object -First 1)
            & (Join-Path $repoRoot 'scripts\Publish-CandidateAgent.ps1') -AgentName $AgentName -Model $model
        }
        'Compare' {
            if (-not $Before) { throw 'Compare needs -Before <label> (an earlier Evaluate run) and -Label <label>.' }
            & $quality compare -Before $Before -After $Label
        }
        'Promote' {
            if ($AgentName -eq $liveAgent) { throw "$AgentName is already the live agent." }
            $model = ((Get-Content (Join-Path $configDir 'metadata.yaml')) |
                Where-Object { $_ -match '^model:\s*(\S+)' } | ForEach-Object { $Matches[1] } | Select-Object -First 1)
            $liveModel = (terraform "-chdir=$tf" output -raw -no-color chat_model_deployment).Trim()
            if ($model -and $model -ne $liveModel) {
                Write-Warning "The baseline now runs on $model, but the live agent's deployment is ${liveModel}: set chat_model in terraform.tfvars first."
            }
            & (Join-Path $repoRoot 'scripts\Publish-AgentImage.ps1')
        }
    }
}
