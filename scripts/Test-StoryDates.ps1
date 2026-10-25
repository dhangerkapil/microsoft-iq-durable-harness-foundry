<#
.SYNOPSIS
    Checks that the three story-date implementations (Python, PowerShell, Node) agree.

.DESCRIPTION
    Runs hydration/story_dates.py, scripts/StoryDates.ps1 and hydration/documents/story-dates.cjs over the
    story sources plus edge cases for several whole-week shifts and fails on any difference. Shift 0 must
    return every input unchanged. Needs python and node on PATH. Used by CI and before publishing content.
#>
[CmdletBinding()]
param([int[]]$Days = @(0, 7, -7, 35, 77, 133, 182, 364, -140))

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'StoryDates.ps1')
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)

$sources = 'hydration/Publish-DemoContent.ps1', 'hydration/documents/build-documents.cjs', 'hydration/documents/build-pdfs.cjs',
    'hydration/meeting-script.md', 'fabric/notebooks/data_agent.py', 'evals/hosted-optimizer-cases.json',
    'evals/advisor-cases.json', 'scripts/Invoke-DemoRehearsal.ps1'
$edge = @(
    'Thursday October 22, 2026; Friday, October 16, 2026; Monday October 19; Oct. 16; Sept 30; Sep 7; Dec 10.',
    'Meeting notes 2026-10-16 Q4 review.docx | 2026-10-22T10:00:00 | 2026-10-15 15:12 | Q1 Q2 Q3 Q4 | Q42 | FAQ4',
    'Portfolio Overview (Oct 2026) | October 2026 | March 2014 | November 3, 2025 | June 1, 2026 | March 2027 retirement',
    'spring 2027 | Spring 2027 | much sooner than spring | by summer | fall 2026 | autumn 2026 | could fall 10% | winter 2027',
    'since September | since September 1 | in October 2026 | by December 10 | from May | in May 2026 | in March.',
    'the 2026 realized-gain budget | for 2026. | 2025 | 2027 capex | 2024 | 2028 | api-version=2023-11-01 | 2026-13-45 | February 29',
    'protocolVersion 2025-06-18 | $2,026 | 12026 | 2026x | x2026 | 1-2026 | 2026-1 | C0007 | 10/16 | 24.1%'
)
$corpus = ((($sources | ForEach-Object { Get-Content -LiteralPath (Join-Path $repoRoot $_) -Raw }) + $edge) -join "`n") -replace "`r`n", "`n"

function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$InputText) {
    $output = $InputText | & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE." }
    (($output -join "`n") -replace "`r`n", "`n").TrimEnd("`n")
}

$failures = 0
$expectedCorpus = $corpus.TrimEnd("`n")
foreach ($shift in $Days) {
    $ps = (Convert-StoryText -Text $corpus -Days $shift).TrimEnd("`n")
    $py = Invoke-Native 'python' @((Join-Path $repoRoot 'data' 'hydration' 'story_dates.py'), "$shift") $corpus
    $js = Invoke-Native 'node' @((Join-Path $repoRoot 'data' 'hydration' 'documents' 'story-dates.cjs'), "$shift") $corpus
    $problems = @()
    if ($shift -eq 0 -and $ps -cne $expectedCorpus) { $problems += 'shift 0 changed the text' }
    foreach ($pair in @(@('PowerShell', $ps), @('Node', $js))) {
        if ($pair[1] -cne $py) {
            $a = $py -split "`n"; $b = $pair[1] -split "`n"
            for ($i = 0; $i -lt [Math]::Max($a.Count, $b.Count); $i++) {
                if ($a[$i] -cne $b[$i]) { $problems += "$($pair[0]) differs from Python at line $($i + 1):`n    py: $($a[$i])`n    $($pair[0].Substring(0, 2).ToLower()): $($b[$i])"; break }
            }
        }
    }
    if ($problems) { $failures++; Write-Output "shift $shift days: FAILED"; $problems | ForEach-Object { "  $_" } }
    else { Write-Output ("shift {0,4} days: Python, PowerShell and Node agree ({1} changed lines)" -f $shift, (@(Compare-Object ($corpus -split "`n") ($py -split "`n") -SyncWindow 0).Count / 2)) }
}
if ($failures) { throw "$failures shift(s) disagree." }
