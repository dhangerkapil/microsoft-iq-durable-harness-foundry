<#
.SYNOPSIS
    Builds the hosted-agent image in Azure Container Registry, pins its digest in
    terraform/agent.auto.tfvars and (unless -NoApply) applies it as a new hosted-agent version.
#>
[CmdletBinding()]
param(
    [string]$Tag = (Get-Date -Format 'yyyyMMdd-HHmm'),
    [switch]$NoApply
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
$repoRoot = Split-Path $PSScriptRoot -Parent
$tf = Join-Path $repoRoot 'terraform'
$registry = (terraform "-chdir=$tf" output -raw -no-color registry_name).Trim()
$log = Join-Path $env:TEAM_WORK_DIR 'acr-agent-build.log'
Push-Location $repoRoot
try {
    az acr build --registry $registry --image "advisor-team:$Tag" --platform linux/amd64 --file Dockerfile . *> $log
} finally { Pop-Location }
if ($LASTEXITCODE -ne 0) {
    Get-Content $log | Select-Object -Last 30
    throw "Image build failed (full log: $log)."
}
$digest = (az acr repository show --name $registry --image "advisor-team:$Tag" --query digest -o tsv).Trim()
if ($digest -notmatch '^sha256:[0-9a-f]{64}$') { throw "Could not read the digest for advisor-team:$Tag." }
$uri = "$registry.azurecr.io/advisor-team@$digest"
"agent_image_uri = `"$uri`"" | Set-Content -Path (Join-Path $tf 'agent.auto.tfvars') -Encoding ascii
Write-Output "Built advisor-team:$Tag -> $uri"
if ($NoApply) { return }
& "$PSScriptRoot\Invoke-TerraformApply.ps1" -Name agent -AllowChanges
