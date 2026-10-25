<#
.SYNOPSIS
    Points Azure CLI, Terraform, uv and Docker at a private per-tenant profile for the current PowerShell
    process only.

.DESCRIPTION
    Dot-source this script before any command that signs in to or deploys to Azure:

        . .\scripts\Use-IsolatedAzure.ps1                 # configure this process
        . .\scripts\Use-IsolatedAzure.ps1 -RequireLogin   # also verify the tenant

    Settings come from .env.local in the repository root (git-ignored). Sign-ins, tokens, Terraform state,
    the Python environment, Docker credentials and work files live under
    %LOCALAPPDATA%\advisor-team\<TEAM_PROFILE>. The default %USERPROFILE%\.azure, .azd and .docker profiles
    are never read or written, so other terminals, editors and projects keep their own sign-ins.
#>
[CmdletBinding()]
param(
    [switch]$RequireLogin
)

$repoRoot = Split-Path $PSScriptRoot -Parent
$settings = @{}
$envFile = Join-Path $repoRoot '.env.local'
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*#') { continue }
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $settings[$Matches[1]] = $Matches[2].Trim('"')
        }
    }
}

$profileName = if ($settings['TEAM_PROFILE']) { $settings['TEAM_PROFILE'] } else { 'demo' }
$base = Join-Path $env:LOCALAPPDATA "advisor-team\$profileName"
$dirs = [ordered]@{}
foreach ($dirName in 'azure', 'azd', 'bin', 'terraform', 'venv', 'docker', 'msal', 'work', 'browser') {
    $dirs[$dirName] = Join-Path $base $dirName
    [void][IO.Directory]::CreateDirectory($dirs[$dirName])
}

# Azure CLI: private profile, no Windows account broker, no interactive subscription picker.
$env:AZURE_CONFIG_DIR = $dirs['azure']
$env:AZURE_CORE_ENABLE_BROKER_ON_WINDOWS = 'false'
$env:AZURE_CORE_LOGIN_EXPERIENCE_V2 = 'off'
$env:AZURE_CORE_COLLECT_TELEMETRY = 'false'
$env:AZURE_LOGGING_ENABLE_LOG_FILE = 'false'

$env:AZD_CONFIG_DIR = $dirs['azd']
$env:TF_DATA_DIR = Join-Path $dirs['terraform'] '.terraform'
$env:TEAM_TF_STATE = Join-Path $dirs['terraform'] 'terraform.tfstate'
$env:UV_PROJECT_ENVIRONMENT = $dirs['venv']
$env:DOCKER_CONFIG = $dirs['docker']
$env:TEAM_MSAL_CACHE = Join-Path $dirs['msal'] 'graph-token-cache.bin'
$env:TEAM_WORK_DIR = $dirs['work']
$env:TEAM_BROWSER_DIR = $dirs['browser']
$env:TEAM_PROFILE_DIR = $base
$env:TEAM_REPO_ROOT = $repoRoot

$exported = 'TEAM_*', 'EXISTING_*', 'DEMO_*', 'WEB_*', 'WEBIQ_*', 'STORY_*', 'DRAFT_*', 'FABRIC_*', 'FOUNDRY_*', 'ADVISOR_*'
foreach ($key in $settings.Keys) {
    foreach ($pattern in $exported) {
        if ($key -like $pattern) { Set-Item -Path "Env:$key" -Value $settings[$key]; break }
    }
}
if ($settings['AZURE_TENANT_ID']) {
    $env:ARM_TENANT_ID = $settings['AZURE_TENANT_ID']
    $env:AZURE_TENANT_ID = $settings['AZURE_TENANT_ID']
}
if ($settings['AZURE_SUBSCRIPTION_ID']) {
    $env:ARM_SUBSCRIPTION_ID = $settings['AZURE_SUBSCRIPTION_ID']
    $env:AZURE_SUBSCRIPTION_ID = $settings['AZURE_SUBSCRIPTION_ID']
}
if (($env:PATH -split ';') -notcontains $dirs['bin']) {
    $env:PATH = "$($dirs['bin']);$env:PATH"
}

# On a busy machine an Azure CLI token call can exceed the ~10 s that Terraform's azapi provider allows.
# This profile-local shim runs the real CLI at high priority; it is only on PATH inside this process.
$realAzPython = Join-Path $env:ProgramFiles 'Microsoft SDKs\Azure\CLI2\python.exe'
$azShim = Join-Path $dirs['bin'] 'az.cmd'
# -X utf8: az streams build logs with characters the Windows code page can't encode.
$azShimContent = @(
    '@echo off'
    'set AZ_INSTALLER=MSI'
    "start `"`" /B /WAIT /HIGH `"$realAzPython`" -X utf8 -IBm azure.cli %*"
    'exit /b %ERRORLEVEL%'
)
if ((Test-Path -LiteralPath $realAzPython) -and
    (-not (Test-Path -LiteralPath $azShim) -or ((Get-Content -LiteralPath $azShim) -join "`n") -ne ($azShimContent -join "`n"))) {
    $azShimContent | Set-Content -LiteralPath $azShim -Encoding ascii
}

if ($RequireLogin) {
    if (-not $settings['AZURE_TENANT_ID']) { throw 'AZURE_TENANT_ID is missing from .env.local.' }
    $account = az account show --output json 2>$null | ConvertFrom-Json
    if (-not $account) { throw 'The isolated profile is not signed in. Run scripts\Connect-IsolatedAzure.ps1.' }
    if ($account.tenantId -ne $settings['AZURE_TENANT_ID']) {
        throw "The isolated profile is signed in to tenant $($account.tenantId), not $($settings['AZURE_TENANT_ID'])."
    }
    if ($settings['AZURE_SUBSCRIPTION_ID'] -and $account.id -ne $settings['AZURE_SUBSCRIPTION_ID']) {
        az account set --subscription $settings['AZURE_SUBSCRIPTION_ID'] --output none
        if ($LASTEXITCODE -ne 0) { throw "Cannot select subscription $($settings['AZURE_SUBSCRIPTION_ID'])." }
    }
}
