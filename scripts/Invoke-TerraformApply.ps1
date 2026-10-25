<#
.SYNOPSIS
    Plans and applies Terraform in the isolated profile, refusing to apply when the
    plan changes or destroys existing resources unless -AllowChanges is given.

.EXAMPLE
    .\scripts\Invoke-TerraformApply.ps1 -Name platform
    .\scripts\Invoke-TerraformApply.ps1 -Name agent -AllowChanges
#>
[CmdletBinding()]
param(
    [string]$Name = 'apply',
    [string[]]$Target = @(),
    [switch]$AllowChanges,
    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
$repoRoot = Split-Path $PSScriptRoot -Parent
$tfDir = Join-Path $repoRoot 'terraform'
$plan = Join-Path $env:TEAM_WORK_DIR "$Name.tfplan"

if (-not (Test-Path (Join-Path $env:TF_DATA_DIR 'terraform.tfstate'))) {
    terraform "-chdir=$tfDir" init -input=false -no-color "-backend-config=path=$env:TEAM_TF_STATE" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'terraform init failed.' }
}

$targetArgs = @($Target | ForEach-Object { "-target=$_" })
$output = terraform "-chdir=$tfDir" plan -input=false -no-color "-out=$plan" @targetArgs 2>&1
$planExit = $LASTEXITCODE
$output | Select-String -Pattern '^\s+# .+ (will be|must be)' | ForEach-Object { $_.Line.Trim() }
$output | Select-String -Pattern '^(Error|│ Error)' -Context 0, 25 | ForEach-Object { $_.Line; $_.Context.PostContext }
if ($planExit -ne 0) { throw "terraform plan failed for '$Name'." }

$summary = $output | Select-String -Pattern 'Plan: (\d+) to add, (\d+) to change, (\d+) to destroy' | Select-Object -Last 1
if (-not $summary) {
    Write-Output 'No changes.'
    return
}
Write-Output $summary.Line.Trim()
$changes = [int]$summary.Matches[0].Groups[2].Value
$destroys = [int]$summary.Matches[0].Groups[3].Value

# Plan gate: inside shared resources, only approved additions.
$planJson = Join-Path $env:TEAM_WORK_DIR "$Name.plan.json"
terraform "-chdir=$tfDir" show -json "$plan" | Set-Content -LiteralPath $planJson -Encoding utf8
$python = Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
& $python (Join-Path $PSScriptRoot 'check_plan.py') $planJson
if ($LASTEXITCODE -ne 0) { throw "The plan for '$Name' would change shared resources beyond the approved additions. Nothing was applied." }
if (($changes -gt 0 -or $destroys -gt 0) -and -not $AllowChanges) {
    Write-Output 'Stopping: the plan changes or destroys existing resources. Review it, then rerun with -AllowChanges.'
    exit 2
}
if ($PlanOnly) { return }

$applyLog = Join-Path $env:TEAM_WORK_DIR "$Name-apply.log"
terraform "-chdir=$tfDir" apply -input=false -no-color "$plan" *> $applyLog
$applyExit = $LASTEXITCODE
Get-Content $applyLog | Select-String -Pattern 'Creation complete|Modifications complete|Destruction complete|Apply complete' |
    ForEach-Object { $_.Line }
Get-Content $applyLog | Select-String -Pattern '^Error:' -Context 0, 25 | ForEach-Object { $_.Line; $_.Context.PostContext }
if ($applyExit -ne 0) { throw "terraform apply failed for '$Name' (full log: $applyLog)." }
