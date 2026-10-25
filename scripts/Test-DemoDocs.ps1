<#
.SYNOPSIS
    Checks the demo documents against demo/expectations.yaml, and the public documents for forbidden terms.

.DESCRIPTION
    1. Prompts, both ways. Every prompt in demo/expectations.yaml must appear verbatim in docs/DEMO-SCRIPT.md as a
       presenter prompt, and every presenter prompt in docs/DEMO-SCRIPT.md must be a prompt in
       demo/expectations.yaml. A presenter prompt is a line that holds only a blockquote with the prompt in straight
       double quotes:

           > "Prepare me for my Thursday meeting with Michael Scott."

       A prompt may appear more than once on either side (the same prompt is used in several steps).
    2. Forbidden terms. README.md, SECURITY.md, SUPPORT.md, CONTRIBUTING.md and every Markdown file under docs\ must
       not contain customer names, real people's names or tenant-specific identifiers (tenant names, resource
       suffixes, IDs, host names). Matching is case-insensitive; the list is stored as SHA-256 hashes, so this public
       script never contains the terms. A two-letter abbreviation counts only as a whole word, so ordinary words that
       contain those letters never match.

    The YAML is read with the powershell-yaml module when it is installed; otherwise a line parser reads every
    prompt: "..." entry (double-quoted, single-quoted or plain scalars; \" and \\ are unescaped).
    Read-only. Exit code 0 when every check passes, 1 otherwise.

.EXAMPLE
    pwsh -NoProfile -File scripts\Test-DemoDocs.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path $PSScriptRoot -Parent
$expectationsPath = Join-Path $root 'demo\expectations.yaml'
$scriptPath = Join-Path $root 'docs\DEMO-SCRIPT.md'
$problems = [System.Collections.Generic.List[string]]::new()

# --- Reading the expectations ----------------------------------------------------------------------------------------
function ConvertFrom-YamlScalar([string]$Raw) {
    $value = $Raw.Trim()
    if ($value.StartsWith('"')) {
        $match = [regex]::Match($value, '^"((?:[^"\\]|\\.)*)"')
        if (-not $match.Success) { throw "Unterminated double-quoted YAML string: $Raw" }
        $sentinel = [string][char]0xE000
        return $match.Groups[1].Value.Replace('\\', $sentinel).Replace('\"', '"').Replace('\/', '/').
            Replace('\t', "`t").Replace('\n', "`n").Replace($sentinel, '\')
    }
    if ($value.StartsWith("'")) {
        $match = [regex]::Match($value, "^'((?:[^']|'')*)'")
        if (-not $match.Success) { throw "Unterminated single-quoted YAML string: $Raw" }
        return $match.Groups[1].Value.Replace("''", "'")
    }
    # A plain scalar ends before a comment.
    return ($value -replace '\s+#.*$', '').Trim()
}

function Get-PromptValue($Node) {
    if ($null -eq $Node -or $Node -is [string]) { return }
    if ($Node -is [System.Collections.IDictionary]) {
        foreach ($key in @($Node.Keys)) {
            $child = $Node[$key]
            if ("$key" -eq 'prompt' -and $child -is [string]) { $child }
            else { Get-PromptValue $child }
        }
        return
    }
    if ($Node -is [System.Collections.IEnumerable]) {
        foreach ($item in $Node) { Get-PromptValue $item }
    }
}

function Read-ExpectationPrompts([string]$Path) {
    $module = Get-Module -ListAvailable -Name powershell-yaml | Select-Object -First 1
    if ($module) {
        Import-Module powershell-yaml -ErrorAction Stop
        $document = ConvertFrom-Yaml (Get-Content -LiteralPath $Path -Raw -Encoding UTF8)
        $prompts = @(Get-PromptValue $document | ForEach-Object { [pscustomobject]@{ Prompt = $_.Trim(); Line = 0 } })
        return [pscustomobject]@{ Parser = 'powershell-yaml'; Prompts = $prompts }
    }
    $prompts = [System.Collections.Generic.List[object]]::new()
    $number = 0
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $number++
        $match = [regex]::Match($line, '^\s*(?:-\s+)?prompt:\s*(\S.*?)\s*$')
        if ($match.Success) {
            $prompts.Add([pscustomobject]@{ Prompt = (ConvertFrom-YamlScalar $match.Groups[1].Value); Line = $number })
        }
    }
    return [pscustomobject]@{ Parser = 'line parser'; Prompts = @($prompts) }
}

# --- Reading the presenter prompts -----------------------------------------------------------------------------------
function Read-ScriptPrompts([string]$Path) {
    $prompts = [System.Collections.Generic.List[object]]::new()
    $number = 0
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $number++
        $match = [regex]::Match($line, '^\s*>\s?"(.*)"\s*$')
        if ($match.Success) {
            $prompts.Add([pscustomobject]@{ Prompt = $match.Groups[1].Value.Trim(); Line = $number })
            continue
        }
        # A prompt in curly quotes can't be copied into the app as written: the convention needs straight quotes.
        if ($line -match '^\s*>\s?[\u201C\u201D]') {
            $problems.Add("docs/DEMO-SCRIPT.md:${number}: a prompt line uses curly quotes; use straight double quotes: $($line.Trim())")
        }
    }
    return @($prompts)
}

foreach ($path in $expectationsPath, $scriptPath) {
    if (-not (Test-Path -LiteralPath $path)) { $problems.Add("Missing file: $path") }
}

if (Test-Path -LiteralPath $expectationsPath -PathType Leaf) {
    if (Test-Path -LiteralPath $scriptPath -PathType Leaf) {
        $expected = Read-ExpectationPrompts $expectationsPath
        $presented = Read-ScriptPrompts $scriptPath
        $expectedSet = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        $presentedSet = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($item in $expected.Prompts) { [void]$expectedSet.Add($item.Prompt) }
        foreach ($item in $presented) { [void]$presentedSet.Add($item.Prompt) }
        Write-Output ("Demo prompts: {0} in demo/expectations.yaml ({1} distinct, read with the {2}); {3} presenter prompts in docs/DEMO-SCRIPT.md ({4} distinct)." -f
            @($expected.Prompts).Count, $expectedSet.Count, $expected.Parser, @($presented).Count, $presentedSet.Count)
        if ($expectedSet.Count -eq 0) { $problems.Add('No prompt was read from demo/expectations.yaml.') }

        $scriptText = Get-Content -LiteralPath $scriptPath -Raw -Encoding UTF8
        $reported = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($item in $expected.Prompts) {
            if ($presentedSet.Contains($item.Prompt) -or -not $reported.Add($item.Prompt)) { continue }
            $where = if ($item.Line) { " (demo/expectations.yaml line $($item.Line))" } else { '' }
            $hint = if ($scriptText.Contains($item.Prompt)) { ' It is in the text, but not on its own line as > "<prompt>".' } else { '' }
            $problems.Add("MISSING from docs/DEMO-SCRIPT.md${where}: $($item.Prompt)$hint")
        }
        foreach ($item in $presented) {
            if (-not $expectedSet.Contains($item.Prompt)) {
                $problems.Add("EXTRA in docs/DEMO-SCRIPT.md line $($item.Line), not in demo/expectations.yaml: $($item.Prompt)")
            }
        }
    }
}

# --- Forbidden terms -----------------------------------------------------------------------------------------------
# Stored as SHA-256 hashes of the lowercase term, so this public repository never contains the terms themselves. A term
# matches anywhere inside a word (a run of letters and digits), case-insensitively; the two-letter abbreviation must be
# the whole word. To add a term, append the hash this prints (and the term's length):
#   [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash(
#       [Text.Encoding]::UTF8.GetBytes('<term in lowercase>'))).Replace('-', '').ToLowerInvariant()
$forbidden = @(
    [pscustomobject]@{ Kind = "the customer's name"; Length = 7; WholeWord = $false
                       Hash = '1bf26a0f3f93196133aa4e6e2b4104e1355431f55d58c914226c319633d2ca96' }
    [pscustomobject]@{ Kind = "the customer's abbreviation"; Length = 2; WholeWord = $true
                       Hash = '7a5443b6636713baa6350c1cf3ec620b2771a8d411ea3180c5d46b502b9ab77d' }
    [pscustomobject]@{ Kind = "the demo tenant's name"; Length = 7; WholeWord = $false
                       Hash = '93365133e8a330edb90b9aa8517094be7e7be1510cc3b46ce106e19db08af6e5' }
    [pscustomobject]@{ Kind = "a deployment's resource suffix"; Length = 5; WholeWord = $false
                       Hash = '8f31c7164b8f138624f06dc25429336af81496c113f40c8ca3d96d7fa31a6085' }
    [pscustomobject]@{ Kind = "a deployment's resource suffix"; Length = 9; WholeWord = $false
                       Hash = '342c4e121ca1166166e329ed8607768df4d3cd24ef0f1d69a0f7d7db69f1ddc7' }
    [pscustomobject]@{ Kind = "a real person's name"; Length = 8; WholeWord = $false
                       Hash = '4f22df54942898f169802bbfe34d6d5b1455cdeb50b387bd5b6a3a6a2496cef7' }
    [pscustomobject]@{ Kind = 'a personal resource-group prefix'; Length = 8; WholeWord = $false
                       Hash = '02c590b056138a2232ab2e4baf06e0cea71ef352e0f2c9222b409e28dadeaa0e' }
    [pscustomobject]@{ Kind = 'a tenant or subscription ID'; Length = 8; WholeWord = $false
                       Hash = '3f197bdfc80efbf472e8f581fe66f7fe3d693a8c3d658e32dfaa282df4197e80' }
    [pscustomobject]@{ Kind = 'a tenant or subscription ID'; Length = 8; WholeWord = $false
                       Hash = 'dbcef87ab6186d6d099a4a8aae65b18792601479e206653ad24180af3fa6c258' }
    [pscustomobject]@{ Kind = "a deployed app's host name"; Length = 10; WholeWord = $false
                       Hash = '4cb7ebf12aac526c2072b3b7efa3acffd1086d9f9df47a170379613ddaba30ed' }
    [pscustomobject]@{ Kind = "a deployed app's host name"; Length = 9; WholeWord = $false
                       Hash = '08510d3501e526a41d546fd3f87ed2601a789e127e4cc489d2f5daaae37afc7c' }
)
$sha256 = [System.Security.Cryptography.SHA256]::Create()
$utf8 = [System.Text.Encoding]::UTF8
$byHash = @{}
foreach ($term in $forbidden) { $byHash[$term.Hash] = $term }
$windowLengths = @($forbidden | Where-Object { -not $_.WholeWord } | ForEach-Object { $_.Length } | Sort-Object -Unique)
$wordLengths = @($forbidden | Where-Object { $_.WholeWord } | ForEach-Object { $_.Length } | Sort-Object -Unique)
$shortest = ($forbidden | Measure-Object -Property Length -Minimum).Minimum
# Each distinct word is checked once per run; a file is scanned line by line only when one of its words matched.
$wordKinds = @{}

function Update-WordKind([string]$Text) {
    foreach ($token in [regex]::Matches($Text.ToLowerInvariant(), '[a-z0-9]+')) {
        $word = $token.Value
        if ($word.Length -lt $shortest -or $wordKinds.ContainsKey($word)) { continue }
        $kinds = [System.Collections.Generic.List[string]]::new()
        if ($wordLengths -contains $word.Length) {
            $hash = [BitConverter]::ToString($sha256.ComputeHash($utf8.GetBytes($word))).Replace('-', '').ToLowerInvariant()
            if ($byHash.ContainsKey($hash) -and $byHash[$hash].WholeWord) { $kinds.Add($byHash[$hash].Kind) }
        }
        foreach ($length in $windowLengths) {
            if ($length -gt $word.Length) { break }
            for ($start = 0; $start -le $word.Length - $length; $start++) {
                $hash = [BitConverter]::ToString($sha256.ComputeHash($utf8.GetBytes($word.Substring($start, $length)))).
                    Replace('-', '').ToLowerInvariant()
                if ($byHash.ContainsKey($hash) -and -not $byHash[$hash].WholeWord) { $kinds.Add($byHash[$hash].Kind) }
            }
        }
        $wordKinds[$word] = @($kinds)
    }
}

function Find-ForbiddenTerm([string]$Line) {
    $kinds = [System.Collections.Generic.List[string]]::new()
    foreach ($token in [regex]::Matches($Line.ToLowerInvariant(), '[a-z0-9]+')) {
        if ($wordKinds.ContainsKey($token.Value)) { foreach ($kind in $wordKinds[$token.Value]) { $kinds.Add($kind) } }
    }
    return @($kinds | Select-Object -Unique)
}

$documents = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
foreach ($name in 'README.md', 'SECURITY.md', 'SUPPORT.md', 'CONTRIBUTING.md') {
    $path = Join-Path $root $name
    if (Test-Path -LiteralPath $path -PathType Leaf) { $documents.Add((Get-Item -LiteralPath $path)) }
}
if (-not (Test-Path -LiteralPath (Join-Path $root 'README.md') -PathType Leaf)) { $problems.Add('Missing file: README.md') }
$docsDir = Join-Path $root 'docs'
if (Test-Path -LiteralPath $docsDir -PathType Container) {
    Get-ChildItem -LiteralPath $docsDir -Filter '*.md' -File -Recurse | ForEach-Object { $documents.Add($_) }
}
$hits = 0
foreach ($file in $documents) {
    $relative = $file.FullName.Substring($root.Length + 1)
    $text = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
    if (-not $text) { continue }
    Update-WordKind $text
    $flagged = $false
    foreach ($token in [regex]::Matches($text.ToLowerInvariant(), '[a-z0-9]+')) {
        if ($wordKinds.ContainsKey($token.Value) -and @($wordKinds[$token.Value]).Count -gt 0) { $flagged = $true; break }
    }
    if (-not $flagged) { continue }
    $number = 0
    foreach ($line in $text -split "`r?`n") {
        $number++
        foreach ($kind in Find-ForbiddenTerm $line) {
            $hits++
            $excerpt = $line.Trim()
            if ($excerpt.Length -gt 160) { $excerpt = $excerpt.Substring(0, 157) + '...' }
            $problems.Add("FORBIDDEN ($kind) in ${relative}:${number}: $excerpt")
        }
    }
}
Write-Output ("Forbidden terms: {0} documents checked, {1} hit(s)." -f $documents.Count, $hits)

# --- Result --------------------------------------------------------------------------------------------------------
if ($problems.Count -gt 0) {
    $problems | ForEach-Object { Write-Output "  $_" }
    Write-Output "FAIL: $($problems.Count) problem(s)."
    exit 1
}
Write-Output 'PASS: every expectation prompt is in the demo script, every presenter prompt is in the expectations, and no forbidden term was found.'
exit 0
