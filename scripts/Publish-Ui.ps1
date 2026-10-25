<#
.SYNOPSIS
    Builds the advisor UI in Azure Container Registry and deploys it to Container Apps (PowerShell port
    of deploy_ui.sh): image build, digest pin in terraform/ui.auto.tfvars, Terraform apply, delegated
    admin consent for the UI app registrations, and a /readyz check.
#>
[CmdletBinding()]
param([string]$Tag = (Get-Date -Format 'yyyyMMdd-HHmm'))

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
. "$PSScriptRoot\AdvisorCommon.ps1"
$repoRoot = Split-Path $PSScriptRoot -Parent
$tf = Join-Path $repoRoot 'terraform'
$registry = (terraform "-chdir=$tf" output -raw -no-color registry_name).Trim()
$log = Join-Path $env:TEAM_WORK_DIR 'acr-ui-build.log'
Push-Location $repoRoot
try { az acr build --registry $registry --image "advisor-ui:$Tag" --platform linux/amd64 --file ui/Dockerfile . *> $log }
finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Get-Content $log | Select-Object -Last 30; throw "UI image build failed (full log: $log)." }
$digest = (az acr repository show --name $registry --image "advisor-ui:$Tag" --query digest -o tsv).Trim()
if ($digest -notmatch '^sha256:[0-9a-f]{64}$') { throw "Could not read the digest for advisor-ui:$Tag." }
"ui_image_uri = `"$registry.azurecr.io/advisor-ui@$digest`"" | Set-Content -Path (Join-Path $tf 'ui.auto.tfvars') -Encoding ascii
Write-Output "Built advisor-ui:$Tag ($digest)"

& "$PSScriptRoot\Invoke-TerraformApply.ps1" -Name ui -AllowChanges
foreach ($name in 'ui_api_client_id', 'ui_browser_client_id') {
    Grant-DelegatedAdminConsent -ClientId (terraform "-chdir=$tf" output -raw -no-color $name).Trim()
}
$url = (terraform "-chdir=$tf" output -raw -no-color ui_url).Trim()
for ($i = 1; $i -le 60; $i++) {
    try {
        Invoke-WebRequest -Uri "$url/readyz" -TimeoutSec 20 | Out-Null
        Write-Output "UI ready: $url"
        Write-Output 'Sign in with the demo admin account in the InPrivate window.'
        return
    } catch { Start-Sleep -Seconds 10 }
}
throw "UI readiness did not succeed at $url/readyz; check the Container Apps logs."
