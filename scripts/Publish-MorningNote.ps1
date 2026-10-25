<#
.SYNOPSIS
    Deploys the morning note (add-on O1) to its Function app: zips functions\morning_note and deploys it with a remote
    build (Flex Consumption installs the Python packages in Azure, so nothing is built locally).

.DESCRIPTION
    Needs the Terraform resources first (enable_morning_note = true in terraform.tfvars, then the Platform step or
    Invoke-TerraformApply.ps1 -Name platform). Then run a note with scripts\Invoke-MorningNote.ps1.

.EXAMPLE
    .\scripts\Publish-MorningNote.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Use-IsolatedAzure.ps1" -RequireLogin
$repoRoot = Split-Path $PSScriptRoot -Parent
$tf = Join-Path $repoRoot 'terraform'
$app = (terraform "-chdir=$tf" output -raw -no-color morning_note_function_name 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $app -or $app -eq 'null') { throw 'The morning note is not provisioned: set enable_morning_note = true and apply Terraform first.' }
$resourceGroup = (terraform "-chdir=$tf" output -raw -no-color resource_group_name).Trim()

$source = Join-Path $repoRoot 'functions\morning_note'
$stage = Join-Path $env:TEAM_WORK_DIR 'morning-note-package'
$zip = Join-Path $env:TEAM_WORK_DIR 'morning-note.zip'
Remove-Item $stage, $zip -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $stage | Out-Null
$ignore = @(Get-Content (Join-Path $source '.funcignore') | Where-Object { $_.Trim() })
Get-ChildItem $source -Force | Where-Object { $_.Name -notin $ignore -and $_.Name -ne '.funcignore' -and $_.Extension -ne '.md' } |
    Copy-Item -Destination $stage -Recurse
Get-ChildItem $stage -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip
Write-Output "Package: $((Get-ChildItem $stage -Recurse -File).Count) files -> $zip"

function Remove-KeyBasedStorageSettings {
    # The storage account allows no shared keys (identity only, as policy often requires). Key-based connection strings
    # that a create or deploy may add would take precedence over the identity-based AzureWebJobsStorage__* settings and
    # stop the host at startup (403), so remove them.
    $names = @('AzureWebJobsStorage', 'DEPLOYMENT_STORAGE_CONNECTION_STRING')
    $present = @(az functionapp config appsettings list --resource-group $resourceGroup --name $app --query "[?contains('$($names -join ',')', name)].name" -o tsv)
    $present = @($present | Where-Object { $_ -in $names })
    if ($present) {
        az functionapp config appsettings delete --resource-group $resourceGroup --name $app --setting-names @present -o none
        Write-Output "Removed key-based settings: $($present -join ', ')"
    }
}

Remove-KeyBasedStorageSettings
az functionapp deployment source config-zip --resource-group $resourceGroup --name $app --src $zip --build-remote true --timeout 900 -o none
$deployExit = $LASTEXITCODE
Remove-KeyBasedStorageSettings
# The CLI's health check reads a host key right after the deployment; a host that's still restarting fails it. The
# package is deployed either way: check the functions instead.
$functions = @(az functionapp function list --resource-group $resourceGroup --name $app --query '[].name' -o tsv)
if ($functions.Count -eq 0) { throw "Deploying $app failed (exit $deployExit, no functions registered; see the output above)." }
Write-Output "Deployed $app with $($functions.Count) functions: $(($functions | ForEach-Object { $_.Split('/')[-1] }) -join ', ')"
