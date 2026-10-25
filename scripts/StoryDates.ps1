<#
.SYNOPSIS
    Story dates: moves the demo story's week to any Thursday client meeting. Dot-source this file.

.DESCRIPTION
    Same rules as hydration/story_dates.py (the reference) and hydration/documents/story-dates.cjs:
    STORY_MEETING_DATE (yyyy-MM-dd, a Thursday) moves every story date by whole weeks from the original
    Thursday 2026-10-22 meeting. Unset or 2026-10-22 changes nothing. scripts/Test-StoryDates.ps1 checks
    that the three implementations agree.
#>

$script:StoryOriginalMeeting = [datetime]::new(2026, 10, 22)
$script:StoryFull = @('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December')
$script:StoryAbbr = @('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
$script:StorySeasonMonth = @{ winter = 1; spring = 4; summer = 7; fall = 10; autumn = 10 }
$script:StoryRegex = & {
    $year = '202[5-7]'
    $months = (@($script:StoryFull) + 'Sept' + @($script:StoryAbbr | Where-Object { $_ -cne 'May' })) -join '|'
    [regex]::new(
        "(?<![\d-])(?<iy>$year)-(?<im>\d\d)-(?<id>\d\d)(?!\d)" +
        "|\b(?<mn>$months)(?<dot>\.?) (?<d>\d{1,2})(?:, (?<y>$year))?\b" +
        "|\b(?<mmn>$months) (?<my>$year)\b" +
        "|\b(?<pre>since|from|until|through|before|after|in|by) (?<bm>$($script:StoryFull -join '|'))\b(?![ .]\d)" +
        "|\b(?<sn>[Ss]pring|[Ss]ummer|[Ff]all|[Aa]utumn|[Ww]inter) (?<sy>$year)\b" +
        "|\b(?<bp>than|before|until|by) (?<bs>spring|summer|fall|autumn|winter)\b(?! \d)" +
        "|\bQ(?<q>[1-4])\b" +
        "|(?<![\d-])\b(?<year>$year)\b(?![-\d])",
        [Text.RegularExpressions.RegexOptions]::CultureInvariant)
}

function Get-StoryMeetingDate {
    <# The story's client meeting: -Value, else $env:STORY_MEETING_DATE, else 2026-10-22. Must be a Thursday. #>
    param([string]$Value)
    if (-not $Value) { $Value = $env:STORY_MEETING_DATE }
    $date = if ($Value -and $Value.Trim()) {
        [datetime]::ParseExact($Value.Trim(), 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
    } else { $script:StoryOriginalMeeting }
    if ($date.DayOfWeek -ne [DayOfWeek]::Thursday) {
        throw "STORY_MEETING_DATE must be a Thursday; $($date.ToString('yyyy-MM-dd')) is a $($date.DayOfWeek)."
    }
    $date
}

function Get-StoryShiftDays {
    param([string]$Value)
    [int]((Get-StoryMeetingDate $Value) - $script:StoryOriginalMeeting).TotalDays
}

function Get-StoryDefaultMeetingDate {
    <# The next Thursday after today (Eastern Time): the demo then runs on the Tuesday or Wednesday before it. #>
    $today = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([datetime]::UtcNow, 'Eastern Standard Time').Date
    $days = ([int][DayOfWeek]::Thursday - [int]$today.DayOfWeek + 7) % 7
    $today.AddDays($(if ($days -eq 0) { 7 } else { $days })).ToString('yyyy-MM-dd')
}

function Get-StoryNearestDate([int]$Month, [int]$Day) {
    $best = $null
    foreach ($year in 2025, 2026, 2027) {
        try { $candidate = [datetime]::new($year, $Month, $Day) } catch { continue }
        if ($null -eq $best -or [Math]::Abs(($candidate - $script:StoryOriginalMeeting).TotalDays) -lt [Math]::Abs(($best - $script:StoryOriginalMeeting).TotalDays)) {
            $best = $candidate
        }
    }
    if ($null -eq $best) { throw "$Month/$Day is not a date in 2025-2027." }
    $best
}

function Get-StorySeasonName([datetime]$Date, [string]$Like) {
    $name = @('winter', 'winter', 'spring', 'spring', 'spring', 'summer', 'summer', 'summer', 'fall', 'fall', 'fall', 'winter')[$Date.Month - 1]
    if ($name -eq 'fall' -and $Like -ieq 'autumn') { $name = 'autumn' }
    if ([char]::IsUpper($Like[0])) { $name = $name.Substring(0, 1).ToUpperInvariant() + $name.Substring(1) }
    $name
}

function Get-StoryMonthName([int]$Index, [string]$Like) {
    if ($script:StoryFull -ccontains $Like) { return $script:StoryFull[$Index - 1] }
    if ($Like -ceq 'Sept' -and $Index -eq 9) { return 'Sept' }
    $script:StoryAbbr[$Index - 1]
}

function Get-StoryMonthIndex([string]$Name) {
    if ($Name -ceq 'Sept') { $Name = 'Sep' }
    $i = [Array]::IndexOf($script:StoryFull, $Name)
    if ($i -lt 0) { $i = [Array]::IndexOf($script:StoryAbbr, $Name) }
    $i + 1
}

function ConvertTo-StoryMatch([Text.RegularExpressions.Match]$Match, [int]$Days, [int]$YearDelta) {
    $g = $Match.Groups
    $inv = [Globalization.CultureInfo]::InvariantCulture
    try {
        if ($g['iy'].Success) {
            return [datetime]::new([int]$g['iy'].Value, [int]$g['im'].Value, [int]$g['id'].Value).AddDays($Days).ToString('yyyy-MM-dd', $inv)
        }
        if ($g['mn'].Success) {
            $month = Get-StoryMonthIndex $g['mn'].Value
            $base = if ($g['y'].Success) { [datetime]::new([int]$g['y'].Value, $month, [int]$g['d'].Value) } else { Get-StoryNearestDate $month ([int]$g['d'].Value) }
            $moved = $base.AddDays($Days)
            $out = "$(Get-StoryMonthName $moved.Month $g['mn'].Value)$($g['dot'].Value) $($moved.Day)"
            return $(if ($g['y'].Success) { "$out, $($moved.Year)" } else { $out })
        }
        if ($g['mmn'].Success) {
            $moved = [datetime]::new([int]$g['my'].Value, (Get-StoryMonthIndex $g['mmn'].Value), 15).AddDays($Days)
            return "$(Get-StoryMonthName $moved.Month $g['mmn'].Value) $($moved.Year)"
        }
        if ($g['bm'].Success) {
            $moved = (Get-StoryNearestDate (Get-StoryMonthIndex $g['bm'].Value) 1).AddDays($Days)
            return "$($g['pre'].Value) $($script:StoryFull[$moved.Month - 1])"
        }
        if ($g['sn'].Success) {
            $moved = [datetime]::new([int]$g['sy'].Value, $script:StorySeasonMonth[$g['sn'].Value], 15).AddDays($Days)
            return "$(Get-StorySeasonName $moved $g['sn'].Value) $($moved.Year)"
        }
        if ($g['bs'].Success) {
            $next = [datetime]::new($script:StoryOriginalMeeting.Year, $script:StorySeasonMonth[$g['bs'].Value], 15)
            if ($next -le $script:StoryOriginalMeeting) { $next = $next.AddYears(1) }
            return "$($g['bp'].Value) $(Get-StorySeasonName $next.AddDays($Days) $g['bs'].Value)"
        }
        if ($g['q'].Success) {
            $moved = [datetime]::new($script:StoryOriginalMeeting.Year, 3 * [int]$g['q'].Value - 2, 16).AddDays($Days)
            return "Q$([Math]::Floor(($moved.Month - 1) / 3) + 1)"
        }
        return [string]([int]$g['year'].Value + $YearDelta)
    } catch {
        return $Match.Value
    }
}

function Convert-StoryText {
    <# Moves every story date in -Text by -Days (default: from STORY_MEETING_DATE). No-op when the shift is 0. #>
    param([AllowNull()][AllowEmptyString()][string]$Text, [Nullable[int]]$Days)
    if ($null -eq $Days) { $Days = Get-StoryShiftDays }
    if (-not $Days -or -not $Text) { return $Text }
    $yearDelta = $script:StoryOriginalMeeting.AddDays([int]$Days).Year - $script:StoryOriginalMeeting.Year
    $sb = [Text.StringBuilder]::new()
    $last = 0
    foreach ($m in $script:StoryRegex.Matches($Text)) {
        [void]$sb.Append($Text, $last, $m.Index - $last)
        [void]$sb.Append((ConvertTo-StoryMatch $m ([int]$Days) $yearDelta))
        $last = $m.Index + $m.Length
    }
    [void]$sb.Append($Text, $last, $Text.Length - $last)
    $sb.ToString()
}
