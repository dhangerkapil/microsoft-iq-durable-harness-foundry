<#
.SYNOPSIS
    Validates the agent code in Azure Container Registry: byte-compiles, imports the hosted app and runs
    the advisor unit tests (Dockerfile.test). Nothing is pushed. Use it where local Python packages
    cannot be installed.
    -Ui instead type-checks the web UI and runs its vitest suite (for machines where npm cannot reach the
    public registry).
#>
[CmdletBinding()]
param([string]$Tests = 'tests/test_advisor.py', [switch]$Ui)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
$repoRoot = Split-Path $PSScriptRoot -Parent
$registry = (terraform "-chdir=$(Join-Path $repoRoot 'terraform')" output -raw -no-color registry_name).Trim()
$context = Join-Path $env:TEAM_WORK_DIR 'acr-test-context'
Remove-Item -Recurse -Force $context -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $context | Out-Null
if ($Ui) {
    Get-ChildItem (Join-Path $repoRoot 'ui') -Force | Where-Object { $_.Name -notin 'node_modules', 'dist' } |
        Copy-Item -Destination $context -Recurse
    @(
        'FROM node:24-slim'
        'WORKDIR /frontend'
        'COPY package.json package-lock.json ./'
        'RUN npm ci --no-audit --no-fund'
        'COPY . ./'
        'RUN npx tsc -b && npx vitest run && echo "ui checks ok"'
    ) | Set-Content (Join-Path $context 'Dockerfile.test') -Encoding utf8
} else {
    foreach ($item in 'pyproject.toml', 'requirements-image.txt', 'Dockerfile.test', 'src', '.agent_configs', 'tests', 'scripts', 'evals', 'data', 'demo', 'functions') {
        Copy-Item -Recurse -Path (Join-Path $repoRoot $item) -Destination $context
    }
    # Terraform definitions only: state files, plans and tfvars hold secrets and never leave this machine.
    $tfContext = New-Item -ItemType Directory -Force -Path (Join-Path $context 'terraform')
    Get-ChildItem -Path (Join-Path $repoRoot 'terraform') -Filter '*.tf' -File | Copy-Item -Destination $tfContext
    Get-ChildItem $context -Recurse -Directory | Where-Object Name -in '__pycache__', 'node_modules' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    (Get-Content (Join-Path $context 'Dockerfile.test') -Raw).Replace('tests/test_advisor.py', $Tests) |
        Set-Content (Join-Path $context 'Dockerfile.test') -Encoding utf8
}
$log = Join-Path $env:TEAM_WORK_DIR 'acr-test.log'
Push-Location $context
try { az acr build --registry $registry --file Dockerfile.test --no-push --platform linux/amd64 --image advisor-team-test:local . *> $log }
finally { Pop-Location }
$exit = $LASTEXITCODE
Get-Content $log | Select-String -Pattern 'imports ok|ui checks ok|passed|failed|error|Error|FAILED|assert|Tests ' | Select-Object -Last 40 | ForEach-Object { $_.Line }
if ($exit -ne 0) { throw "ACR validation build failed (full log: $log)." }
Write-Output 'ACR validation build passed.'
