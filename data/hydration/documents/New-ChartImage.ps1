<#
.SYNOPSIS
    Renders the image-only NVDA concentration chart for the portfolio overview PDF.

.DESCRIPTION
    The chart's title, axis values and data exist only as pixels, so PDF text extraction cannot read
    them; only the caption printed under the image in the PDF is text. This is the "chart that exists
    only as an image" test case (requirement UC-07). Windows only (System.Drawing).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$SeriesPath,
    [Parameter(Mandatory)][string]$OutputPath,
    # First date on the chart; default September 1 of the reference story, moved with STORY_MEETING_DATE.
    [string]$From
)

$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'scripts\StoryDates.ps1')
if (-not $From) { $From = Convert-StoryText '2026-09-01' }
Add-Type -AssemblyName System.Drawing
$data = Get-Content -LiteralPath $SeriesPath -Raw | ConvertFrom-Json
$points = @($data.series | Where-Object { $_.date -ge $From })
if ($points.Count -lt 2) { throw "Not enough points after $From." }

$width, $height = 1600, 760
$left, $right, $top, $bottom = 130, 60, 120, 110
$min, $max = 20.0, 26.0
$bitmap = [Drawing.Bitmap]::new($width, $height)
$g = [Drawing.Graphics]::FromImage($bitmap)
try {
    $g.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $g.Clear([Drawing.Color]::White)
    $titleFont = [Drawing.Font]::new('Segoe UI Semibold', 30)
    $axisFont = [Drawing.Font]::new('Segoe UI', 19)
    $labelFont = [Drawing.Font]::new('Segoe UI Semibold', 21)
    $dark = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(38, 38, 38))
    $grey = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(110, 110, 110))
    $g.DrawString('NVDA share of portfolio vs. 25% single-stock limit', $titleFont, $dark, $left - 10, 30)

    $plotW = $width - $left - $right; $plotH = $height - $top - $bottom
    function Y([double]$v) { $top + $plotH - (($v - $min) / ($max - $min)) * $plotH }
    function X([int]$i) { $left + ($i / ($points.Count - 1)) * $plotW }

    $gridPen = [Drawing.Pen]::new([Drawing.Color]::FromArgb(225, 225, 225), 2)
    foreach ($v in 20, 21, 22, 23, 24, 25, 26) {
        $y = Y $v
        $g.DrawLine($gridPen, $left, $y, $left + $plotW, $y)
        $g.DrawString(("{0}%" -f $v), $axisFont, $grey, 40, $y - 15)
    }
    $limitPen = [Drawing.Pen]::new([Drawing.Color]::FromArgb(192, 0, 0), 4)
    $limitPen.DashStyle = [Drawing.Drawing2D.DashStyle]::Dash
    $g.DrawLine($limitPen, $left, (Y 25), $left + $plotW, (Y 25))
    $g.DrawString('Limit 25%', $labelFont, [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(192, 0, 0)), $left + $plotW - 150, (Y 25) - 40)

    $linePen = [Drawing.Pen]::new([Drawing.Color]::FromArgb(0, 94, 184), 6)
    $linePen.LineJoin = [Drawing.Drawing2D.LineJoin]::Round
    $pts = [Drawing.PointF[]]::new($points.Count)
    for ($i = 0; $i -lt $points.Count; $i++) { $pts[$i] = [Drawing.PointF]::new((X $i), (Y ([double]$points[$i].nvda_pct))) }
    $g.DrawLines($linePen, $pts)
    $last = $pts[$pts.Count - 1]
    $g.FillEllipse([Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(0, 94, 184)), $last.X - 10, $last.Y - 10, 20, 20)
    $lastDay = ([datetime]$points[-1].date).ToString('MMM d', [Globalization.CultureInfo]::InvariantCulture)
    $g.DrawString(("{0:N1}% on {1}" -f [double]$points[-1].nvda_pct, $lastDay), $labelFont, $dark, $last.X - 215, $last.Y + 34)

    foreach ($i in 0, [int](($points.Count - 1) / 2), ($points.Count - 1)) {
        $label = ([datetime]$points[$i].date).ToString('MMM d', [Globalization.CultureInfo]::InvariantCulture)
        $g.DrawString($label, $axisFont, $grey, (X $i) - 35, $top + $plotH + 18)
    }
    $g.DrawString('Contoso Wealth portfolio system. Synthetic demo data.', $axisFont, $grey, $left, $height - 50)
    $bitmap.Save($OutputPath, [Drawing.Imaging.ImageFormat]::Png)
} finally {
    $g.Dispose(); $bitmap.Dispose()
}
Write-Output "Chart image: $OutputPath ($($points.Count) points from $From)"
