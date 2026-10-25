<#
.SYNOPSIS
    Publishes the synthetic Microsoft 365 content for the Michael Scott story into the demo tenant.

.DESCRIPTION
    Uses the app registration "advisor-team-hydration[-<TEAM_NAME_SUFFIX>]" with Microsoft Graph application permissions
    Mail.ReadWrite, Calendars.ReadWrite, Sites.ReadWrite.All and MailboxSettings.ReadWrite, granted by the
    signed-in tenant admin. Every run sets the advisor's mailbox time zone to Eastern Time and replaces the
    app's client secret (kept in memory only; it expires after two days).
    Delete the app after the demo: .\data\hydration\Publish-DemoContent.ps1 -RemoveApp

    Story dates: the text below uses the reference story (client meeting Thursday 2026-10-22). STORY_MEETING_DATE
    in .env.local (a Thursday; Deploy-AdvisorDemo.ps1 -StoryMeetingDate sets it) moves every date, subject and file
    name by whole weeks (scripts/StoryDates.ps1), so the same week works in any month.

    Stages (idempotent: items are matched by subject or file name and skipped when present):
      Friday  SharePoint: IPS v1, Friday meeting notes, NVDA research note.
              Calendar: Thu 10/22 "Year-end planning".
              Mail: Michael's Thursday agenda, Michael's Saturday closing email, the analyst's Monday alert.
      Meeting The Fri 10/16 "Q4 portfolio review - Michael Scott" Teams meeting, 10:30-11:00 PM ET, organized by the
              analyst (DEMO_ANALYST_UPN) with the advisor invited. A person records it with hydration/meeting-script.md.
              The organizer's calendar must allow teamsForBusiness; the demo admin's mailbox can't host Teams meetings.
      OneDrive  The advisor's own OneDrive, folder "Client Files/Michael Scott": two PDFs with charts and
              tables at fixed pages, for the chart-and-page demo. "Portfolio Overview (Oct 2026)": Figure 1
              (sector chart) and Table 1 (top holdings) on page 2; Figure 2 (image-only NVDA chart) on page 3.
              "IPS v1 (signed)": Table 1 (limits) on page 2, Table 2 (signatures) on page 3.
              Needs python, node with pdfkit (NODE_PATH) and Windows (System.Drawing). -ReplacePdfs re-uploads.
      Monday  SharePoint: IPS v2 draft. Run after the recorded meeting so its modified date is after the baseline.
      Live    Mail: Michael's 529 question, received now. Run during the demo.

    Every number comes from fabric/portfolio-story.json (written by fabric/Deploy-FabricPortfolio.ps1).
    Names come from .env.local: DEMO_ADVISOR_NAME, DEMO_ANALYST_NAME, DEMO_ANALYST_UPN, DEMO_CLIENT_EMAIL.
    -Preview lists what the stages would publish for the story week (subjects, times, file names) and builds the
    documents in the work folder, without the hydration app or any change in Microsoft 365.

.EXAMPLE
    .\data\hydration\Publish-DemoContent.ps1 -Stage Friday, Meeting, OneDrive, Monday, Live -Preview
#>
[CmdletBinding()]
param(
    [ValidateSet('Friday', 'Meeting', 'OneDrive', 'Monday', 'Live', 'Status')][string[]]$Stage = @('Friday'),
    [switch]$ReplacePdfs,
    # Re-create these messages with the current text (for example after a story change).
    [string[]]$ReplaceMail = @(),
    # Re-upload these SharePoint files by name (their modified date becomes now).
    [string[]]$ReplaceFiles = @(),
    [switch]$RemoveApp,
    [switch]$Preview,
    # With -Preview only: show another story week than STORY_MEETING_DATE (a Thursday, yyyy-MM-dd).
    [ValidatePattern('^(|\d{4}-\d{2}-\d{2})$')][string]$StoryMeetingDate
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $repoRoot 'scripts\Use-IsolatedAzure.ps1') -RequireLogin
. (Join-Path $repoRoot 'scripts\AdvisorCommon.ps1')
if ($StoryMeetingDate) {
    if (-not $Preview) { throw '-StoryMeetingDate works with -Preview only; set the story week with Deploy-AdvisorTeam.ps1 -StoryMeetingDate.' }
    $env:STORY_MEETING_DATE = $StoryMeetingDate
}
$appName = Get-TenantScopedName 'advisor-team-hydration'
$graph = 'https://graph.microsoft.com/v1.0'

if ($RemoveApp) {
    $ids = az ad app list --display-name $appName --query '[].appId' -o tsv
    foreach ($id in $ids) { az ad app delete --id $id; Write-Output "Deleted app registration $appName ($id)." }
    return
}

$advisorName = if ($env:DEMO_ADVISOR_NAME) { $env:DEMO_ADVISOR_NAME } else { 'Alex Morgan' }
$advisorFirst = $advisorName.Split(' ')[0]
$analystName = if ($env:DEMO_ANALYST_NAME) { $env:DEMO_ANALYST_NAME } else { 'Research Desk' }
$clientEmail = if ($env:DEMO_CLIENT_EMAIL) { $env:DEMO_CLIENT_EMAIL } else { 'michael.scott@scott-family.example' }
$advisor = az ad signed-in-user show --query '{id:id, upn:userPrincipalName, mail:mail}' -o json | ConvertFrom-Json
$advisorUpn = $advisor.upn
$analystUpn = if ($env:DEMO_ANALYST_UPN) { $env:DEMO_ANALYST_UPN } else { $advisorUpn }
$story = (Get-Content (Join-Path $repoRoot 'data\fabric\portfolio-story.json') -Raw | ConvertFrom-Json).michael_scott
if (-not $story) { throw 'fabric/portfolio-story.json has no story numbers; run fabric\Deploy-FabricPortfolio.ps1 first.' }
$meetingDate = Get-StoryMeetingDate
# With Fabric, the lakehouse and this content must describe the same week (rerun Deploy-FabricPortfolio.ps1 after a
# new STORY_MEETING_DATE). Without Fabric only the numbers are used, and they don't depend on the week.
if ($env:FABRIC_DATA_AGENT_MCP_URL -and -not $Preview -and $story.discussion_date -and $story.discussion_date -ne $meetingDate.AddDays(-6).ToString('yyyy-MM-dd')) {
    throw "fabric/portfolio-story.json is for a review on $($story.discussion_date), but STORY_MEETING_DATE $($meetingDate.ToString('yyyy-MM-dd')) puts it on $($meetingDate.AddDays(-6).ToString('yyyy-MM-dd')). Rerun fabric\Deploy-FabricPortfolio.ps1."
}
Write-Output ('Story week: recorded review Fri {0:yyyy-MM-dd}, analyst alert Mon {1:yyyy-MM-dd}, client meeting Thu {2:yyyy-MM-dd} (STORY_MEETING_DATE).' -f $meetingDate.AddDays(-6), $meetingDate.AddDays(-3), $meetingDate)

# --- App registration, Graph application permissions and an app-only token -------------------------
function Get-HydrationToken {
    $app = az ad app list --display-name $appName --query '[0]' -o json | ConvertFrom-Json
    if (-not $app) {
        $app = az ad app create --display-name $appName --sign-in-audience AzureADMyOrg -o json | ConvertFrom-Json
        Write-Output "Created app registration $appName."
    }
    $sp = az ad sp list --filter "appId eq '$($app.appId)'" --query '[0]' -o json | ConvertFrom-Json
    if (-not $sp) { $sp = az ad sp create --id $app.appId -o json | ConvertFrom-Json }
    $graphSp = (az rest --method get --url "$graph/servicePrincipals?`$filter=appId eq '00000003-0000-0000-c000-000000000000'&`$select=id,appRoles" -o json | ConvertFrom-Json).value[0]
    $assigned = @((az rest --method get --url "$graph/servicePrincipals/$($sp.id)/appRoleAssignments" -o json | ConvertFrom-Json).value.appRoleId)
    foreach ($permission in 'Mail.ReadWrite', 'Calendars.ReadWrite', 'Sites.ReadWrite.All', 'MailboxSettings.ReadWrite') {
        $role = $graphSp.appRoles | Where-Object { $_.value -eq $permission -and $_.allowedMemberTypes -contains 'Application' }
        if ($assigned -notcontains $role.id) {
            $file = Join-Path $env:TEAM_WORK_DIR 'approle.json'
            @{ principalId = $sp.id; resourceId = $graphSp.id; appRoleId = $role.id } | ConvertTo-Json | Set-Content $file -Encoding ascii
            az rest --method post --url "$graph/servicePrincipals/$($sp.id)/appRoleAssignments" --body "@$file" --headers 'Content-Type=application/json' -o none
            Write-Output "Granted $permission (application) to $appName."
        }
    }
    $endDate = (Get-Date).ToUniversalTime().AddDays(2).ToString('yyyy-MM-dd')
    $secret = az ad app credential reset --id $app.appId --display-name 'hydration-run' --end-date $endDate --query password -o tsv --only-show-errors
    $form = @{ client_id = $app.appId; client_secret = $secret; grant_type = 'client_credentials'; scope = 'https://graph.microsoft.com/.default' }
    for ($i = 1; $i -le 18; $i++) {
        try {
            $token = (Invoke-RestMethod -Method Post -Uri "https://login.microsoftonline.com/$env:AZURE_TENANT_ID/oauth2/v2.0/token" -Body $form).access_token
            $claims = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String((($token.Split('.')[1]).Replace('-', '+').Replace('_', '/')).PadRight([Math]::Ceiling($token.Split('.')[1].Length / 4) * 4, '=')))
            if (($claims | ConvertFrom-Json).roles.Count -ge 4) { return $token }
        } catch { }
        Start-Sleep -Seconds 10
    }
    throw 'Could not get an app-only Graph token with the four hydration roles.'
}

if (-not $Preview) { $script:token = Get-HydrationToken | Select-Object -Last 1 }
function Invoke-Graph([string]$Method, [string]$Path, $Body, [byte[]]$Bytes) {
    $request = @{ Method = $Method; Uri = "$graph$Path"; Headers = @{ Authorization = "Bearer $script:token" } }
    if ($Bytes) { $request.Body = $Bytes; $request.ContentType = 'application/octet-stream' }
    elseif ($null -ne $Body) { $request.Body = ($Body | ConvertTo-Json -Depth 10); $request.ContentType = 'application/json' }
    for ($attempt = 1; ; $attempt++) {
        try { return Invoke-RestMethod @request } catch {
            $code = $_.Exception.Response.StatusCode.value__
            if (($code -in 401, 403, 429) -or $code -ge 500) { if ($attempt -lt 6) { Start-Sleep -Seconds (10 * $attempt); continue } }
            throw "Graph $Method $Path failed ($code): $($_.ErrorDetails.Message)"
        }
    }
}
$user = [uri]::EscapeDataString($advisorUpn)

# --- Content helpers ------------------------------------------------------------------------------
function Add-InboxMessage([string]$Subject, [string]$Html, [string]$FromName, [string]$FromAddress, [datetime]$ReceivedUtc) {
    $replace = ($ReplaceMail -contains $Subject) -or ($ReplaceMail -contains (Convert-StoryText $Subject))
    $Subject = Convert-StoryText $Subject
    $Html = Convert-StoryText $Html
    $existing = Invoke-Graph Get "/users/$user/messages?`$filter=subject eq '$([uri]::EscapeDataString($Subject.Replace("'", "''")))'&`$select=id"
    if ($existing.value.Count -gt 0 -and $replace) {
        foreach ($old in $existing.value) { Invoke-Graph Delete "/users/$user/messages/$($old.id)" | Out-Null }
        Write-Output "  mail replaced: $Subject"
    } elseif ($existing.value.Count -gt 0) { Write-Output "  mail exists: $Subject"; return }
    $sender = @{ emailAddress = @{ name = $FromName; address = $FromAddress } }
    $message = Invoke-Graph Post "/users/$user/mailFolders/inbox/messages" @{
        subject = $Subject; body = @{ contentType = 'HTML'; content = $Html }; from = $sender; sender = $sender
        toRecipients = @(@{ emailAddress = @{ name = $advisorName; address = $advisorUpn } })
        singleValueExtendedProperties = @(
            @{ id = 'Integer 0x0E07'; value = '1' }
            @{ id = 'SystemTime 0x0E06'; value = $ReceivedUtc.ToString('yyyy-MM-ddTHH:mm:ssZ') }
            @{ id = 'SystemTime 0x0039'; value = $ReceivedUtc.AddMinutes(-1).ToString('yyyy-MM-ddTHH:mm:ssZ') }
        )
    }
    Invoke-Graph Patch "/users/$user/messages/$($message.id)" @{ isRead = $false } | Out-Null
    Write-Output "  mail added: $Subject ($($ReceivedUtc.ToString('u')))"
}

function Add-CalendarEvent([string]$Subject, [string]$StartLocal, [string]$EndLocal, [string]$Html, [switch]$Teams) {
    $Subject, $StartLocal, $EndLocal, $Html = @($Subject, $StartLocal, $EndLocal, $Html) | ForEach-Object { Convert-StoryText $_ }
    $day = ([datetime]$StartLocal).Date
    $view = Invoke-Graph Get "/users/$user/calendarView?startDateTime=$($day.AddDays(-1).ToString('yyyy-MM-dd'))T00:00:00Z&endDateTime=$($day.AddDays(2).ToString('yyyy-MM-dd'))T00:00:00Z&`$select=subject,onlineMeeting&`$top=50"
    $match = @($view.value | Where-Object subject -eq $Subject)
    if ($match) { Write-Output "  event exists: $Subject"; return }
    $event = @{
        subject = $Subject; body = @{ contentType = 'HTML'; content = $Html }; showAs = 'busy'
        start = @{ dateTime = $StartLocal; timeZone = 'Eastern Standard Time' }
        end = @{ dateTime = $EndLocal; timeZone = 'Eastern Standard Time' }
    }
    if ($Teams) { $event.isOnlineMeeting = $true; $event.onlineMeetingProvider = 'teamsForBusiness' }
    try { $created = Invoke-Graph Post "/users/$user/events" $event } catch {
        if (-not $Teams) { throw }
        Write-Warning "Teams link creation failed; creating the event without it. $($_.Exception.Message)"
        $event.Remove('isOnlineMeeting'); $event.Remove('onlineMeetingProvider')
        $created = Invoke-Graph Post "/users/$user/events" $event
    }
    Write-Output "  event added: $Subject $StartLocal ET$(if ($created.onlineMeeting.joinUrl) { ' (Teams link created)' })"
}

$script:drive = $null
function Add-SharePointFile([string]$Folder, [string]$Name, [string]$SourcePath) {
    $replace = ($ReplaceFiles -contains $Name) -or ($ReplaceFiles -contains (Convert-StoryText $Name))
    $Name = Convert-StoryText $Name
    $SourcePath = Join-Path (Split-Path $SourcePath -Parent) (Convert-StoryText (Split-Path $SourcePath -Leaf))
    if (-not $script:drive) { $script:drive = Invoke-Graph Get '/sites/root/drive?$select=id,webUrl' }
    $relative = (($Folder.Split('/') + $Name) | ForEach-Object { [uri]::EscapeDataString($_) }) -join '/'
    if (-not $replace) {
        try { $item = Invoke-Graph Get "/drives/$($script:drive.id)/root:/${relative}?`$select=id,webUrl"; Write-Output "  file exists: $Folder/$Name"; return } catch {
            if ($_.Exception.Message -notmatch '\(404\)') { throw }
        }
    }
    $item = Invoke-Graph Put "/drives/$($script:drive.id)/root:/${relative}:/content" -Bytes ([IO.File]::ReadAllBytes($SourcePath))
    Write-Output "  file $(if ($replace) { 'replaced' } else { 'added' }): $($item.webUrl)"
}

function Add-RecordedMeeting([string]$OrganizerUpn, [string]$Subject, [string]$StartLocal, [string]$EndLocal, [string]$Html) {
    # A Teams meeting in the organizer's calendar with the advisor invited; a person records it from the script.
    $Subject, $StartLocal, $EndLocal, $Html = @($Subject, $StartLocal, $EndLocal, $Html) | ForEach-Object { Convert-StoryText $_ }
    $organizer = [uri]::EscapeDataString($OrganizerUpn)
    $calendar = Invoke-Graph Get "/users/$organizer/calendar?`$select=allowedOnlineMeetingProviders"
    if ($calendar.allowedOnlineMeetingProviders -notcontains 'teamsForBusiness') {
        throw "$OrganizerUpn's calendar can't host Teams meetings (allowed: '$($calendar.allowedOnlineMeetingProviders -join ',')'). Set DEMO_ANALYST_UPN to a user whose calendar allows teamsForBusiness."
    }
    $day = ([datetime]$StartLocal).Date
    $view = Invoke-Graph Get "/users/$organizer/calendarView?startDateTime=$($day.AddDays(-1).ToString('yyyy-MM-dd'))T00:00:00Z&endDateTime=$($day.AddDays(2).ToString('yyyy-MM-dd'))T00:00:00Z&`$select=subject,onlineMeeting&`$top=50"
    if (@($view.value | Where-Object subject -eq $Subject)) { Write-Output "  meeting exists: $Subject"; return }
    $created = Invoke-Graph Post "/users/$organizer/events" @{
        subject = $Subject; body = @{ contentType = 'HTML'; content = $Html }; showAs = 'busy'
        start = @{ dateTime = $StartLocal; timeZone = 'Eastern Standard Time' }
        end = @{ dateTime = $EndLocal; timeZone = 'Eastern Standard Time' }
        attendees = @(@{ emailAddress = @{ address = $advisorUpn; name = $advisorName }; type = 'required' })
        isOnlineMeeting = $true; onlineMeetingProvider = 'teamsForBusiness'; allowNewTimeProposals = $false
    }
    Write-Output "  meeting added: $Subject $StartLocal ET, organizer $OrganizerUpn$(if ($created.onlineMeeting.joinUrl) { ' (Teams link created)' })"
}

function Add-OneDriveFile([string]$Folder, [string]$Name, [string]$SourcePath) {
    $Name = Convert-StoryText $Name
    $SourcePath = Join-Path (Split-Path $SourcePath -Parent) (Convert-StoryText (Split-Path $SourcePath -Leaf))
    $relative = (($Folder.Split('/') + $Name) | ForEach-Object { [uri]::EscapeDataString($_) }) -join '/'
    if (-not $ReplacePdfs) {
        try {
            $item = Invoke-Graph Get "/users/$user/drive/root:/${relative}?`$select=id,webUrl"
            Write-Output "  file exists: OneDrive/$Folder/$Name"; return
        } catch { if ("$_" -notmatch '\(404\)') { throw } }
    }
    $item = Invoke-Graph Put "/users/$user/drive/root:/${relative}:/content" -Bytes ([IO.File]::ReadAllBytes($SourcePath))
    Write-Output "  uploaded: OneDrive/$Folder/$Name ($($item.webUrl))"
}

function Get-Pdfs {
    $out = Join-Path $env:TEAM_WORK_DIR 'pdfs'
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    $series = Join-Path $out 'series.json'
    python (Join-Path $PSScriptRoot 'documents\portfolio_series.py') (Join-Path $repoRoot 'data\fabric\notebooks\portfolio_data.py') $series
    if ($LASTEXITCODE -ne 0) { throw 'Could not compute the portfolio series.' }
    & (Join-Path $PSScriptRoot 'documents\New-ChartImage.ps1') -SeriesPath $series -OutputPath (Join-Path $out 'nvda-trend.png') | Out-Null
    if (-not $env:NODE_PATH) { $env:NODE_PATH = Join-Path $env:TEAM_WORK_DIR 'docgen\node_modules' }
    node (Join-Path $PSScriptRoot 'documents\build-pdfs.cjs') (Join-Path $repoRoot 'data\fabric\portfolio-story.json') $series (Join-Path $out 'nvda-trend.png') $out $advisorName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'PDF generation failed (npm install pdfkit in the docgen work folder).' }
    return $out
}

function Get-Documents {
    $out = Join-Path $env:TEAM_WORK_DIR 'documents'
    $env:NODE_PATH = Join-Path $env:TEAM_WORK_DIR 'docgen\node_modules'
    if (-not (Test-Path (Join-Path $env:NODE_PATH 'docx'))) {
        Push-Location (Split-Path $env:NODE_PATH -Parent)
        try { if (-not (Test-Path package.json)) { npm init -y *> $null }; npm install docx@9 --no-audit --no-fund *> $null } finally { Pop-Location }
    }
    node (Join-Path $PSScriptRoot 'documents\build-documents.cjs') (Join-Path $repoRoot 'data\fabric\portfolio-story.json') $out $advisorName $analystName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Document generation failed.' }
    $out
}

function ConvertTo-Html([string[]]$Paragraphs) { ($Paragraphs | ForEach-Object { "<p>$([Net.WebUtility]::HtmlEncode($_))</p>" }) -join '' }
function ConvertTo-EasternUtc([string]$Local) {
    [TimeZoneInfo]::ConvertTimeToUtc([datetime]::ParseExact((Convert-StoryText $Local), 'yyyy-MM-dd HH:mm', $null), [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time'))
}
$fmtUsd = { param($n) '$' + ([double]$n).ToString('N0', [Globalization.CultureInfo]::InvariantCulture) }

if ($Preview) {
    # Same inputs as the real helpers, printed instead of published.
    $eastern = [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')
    function Add-InboxMessage([string]$Subject, [string]$Html, [string]$FromName, [string]$FromAddress, [datetime]$ReceivedUtc) {
        $text = [Net.WebUtility]::HtmlDecode(((Convert-StoryText $Html) -replace '</p><p>', ' ' -replace '<[^>]+>', ''))
        Write-Output ("  mail  {0:ddd yyyy-MM-dd HH:mm} ET  {1}  (from {2})" -f [TimeZoneInfo]::ConvertTimeFromUtc($ReceivedUtc, $eastern), (Convert-StoryText $Subject), $FromName)
        Write-Output "        $($text.Substring(0, [Math]::Min(150, $text.Length)))..."
    }
    function Add-CalendarEvent([string]$Subject, [string]$StartLocal, [string]$EndLocal, [string]$Html, [switch]$Teams) {
        Write-Output ("  event {0:ddd yyyy-MM-dd HH:mm}-{1:HH:mm} ET  {2}{3}" -f [datetime](Convert-StoryText $StartLocal), [datetime](Convert-StoryText $EndLocal), (Convert-StoryText $Subject), $(if ($Teams) { ' (Teams)' }))
    }
    function Add-RecordedMeeting([string]$OrganizerUpn, [string]$Subject, [string]$StartLocal, [string]$EndLocal, [string]$Html) {
        Write-Output ("  meeting {0:ddd yyyy-MM-dd HH:mm}-{1:HH:mm} ET  {2}  (organizer {3})" -f [datetime](Convert-StoryText $StartLocal), [datetime](Convert-StoryText $EndLocal), (Convert-StoryText $Subject), $OrganizerUpn)
    }
    function Add-SharePointFile([string]$Folder, [string]$Name, [string]$SourcePath) {
        $file = Join-Path (Split-Path $SourcePath -Parent) (Convert-StoryText (Split-Path $SourcePath -Leaf))
        Write-Output "  SharePoint  $Folder/$(Convert-StoryText $Name)$(if (-not (Test-Path -LiteralPath $file)) { '  (MISSING: ' + $file + ')' })"
    }
    function Add-OneDriveFile([string]$Folder, [string]$Name, [string]$SourcePath) {
        $file = Join-Path (Split-Path $SourcePath -Parent) (Convert-StoryText (Split-Path $SourcePath -Leaf))
        Write-Output "  OneDrive    $Folder/$(Convert-StoryText $Name)$(if (-not (Test-Path -LiteralPath $file)) { '  (MISSING: ' + $file + ')' })"
    }
    $Stage = @($Stage | Where-Object { $_ -ne 'Status' })
    Write-Output 'Preview: nothing is published.'
}

# --- Stages ----------------------------------------------------------------------------------------
# The advisor works in Eastern Time; without a mailbox time zone, Work IQ reports meeting times in UTC.
$mailbox = if ($Preview) { @{ timeZone = 'Eastern Standard Time' } } else { Invoke-Graph Get "/users/$user/mailboxSettings?`$select=timeZone" }
if ($mailbox.timeZone -ne 'Eastern Standard Time') {
    Invoke-Graph Patch "/users/$user/mailboxSettings" @{ timeZone = 'Eastern Standard Time' } | Out-Null
    Write-Output "Mailbox time zone set to Eastern Standard Time (was '$($mailbox.timeZone)')."
}

if ($Stage -contains 'Friday') {
    Write-Output 'Friday stage'
    $docs = Get-Documents
    Add-SharePointFile 'Client Files/Michael Scott' 'IPS v1.docx' (Join-Path $docs 'IPS v1.docx')
    Add-SharePointFile 'Client Files/Michael Scott' 'Meeting notes 2026-10-16 Q4 review.docx' (Join-Path $docs 'Meeting notes 2026-10-16 Q4 review.docx')
    Add-SharePointFile 'Research' 'Research note - NVDA AI infrastructure update - 2026-10-14.docx' (Join-Path $docs 'Research note - NVDA AI infrastructure update - 2026-10-14.docx')

    Add-CalendarEvent 'Year-end planning - Michael Scott' '2026-10-22T10:00:00' '2026-10-22T11:00:00' (ConvertTo-Html @(
        'Year-end planning with Michael Scott (client C0007).',
        'Agenda: funding the home down payment, NVDA position review, IPS version 2 review and signature.')) -Teams

    Add-InboxMessage "Agenda for tomorrow's review" (ConvertTo-Html @(
        "Hi $advisorFirst,",
        'Looking forward to our review tomorrow. Three things on my mind:',
        '1. NVDA has had a huge run. Are we getting close to the 25% limit in my policy?',
        '2. We are house hunting. We are aiming to buy in spring 2027 and will probably need about $400,000 for the down payment.',
        '3. My niece keeps telling me about ESG investing. Could we tilt new money that way?',
        'Talk tomorrow,', 'Michael')) 'Michael Scott' $clientEmail (ConvertTo-EasternUtc '2026-10-15 15:12')
    Add-InboxMessage 'Change of plans: closing moved to December 15' (ConvertTo-Html @(
        "Hi $advisorFirst,",
        'Big news: we found the house! The seller wants to close on December 15, much sooner than spring. The title company needs the $400,000 down payment wired by December 10.',
        "Can we go through where that money comes from on Thursday? I'd rather not blow through my tax budget if we have to sell anything. My accountant also confirmed she wants the 2026 realized-gain budget at `$15,000.",
        'Thanks,', 'Michael')) 'Michael Scott' $clientEmail (ConvertTo-EasternUtc '2026-10-17 10:24')
    Add-InboxMessage "NVDA +11.2% today: Michael Scott over his single-stock limit" (ConvertTo-Html @(
        "$advisorFirst,",
        ("NVDA closed at `${0:N2} today, up 11.2% on the AI infrastructure guidance. On today's close, Michael Scott's NVDA position is {1:N0} shares worth `${2:N2}, {3:N1}% of his `${4:N2} portfolio, above his 25% single-stock limit. His cost basis is `${7:N2} a share, so any trim realizes a large gain. Information Technology is {5:N1}% against the 40% sector limit. Cash and equivalents: `${6:N2}." -f $story.nvda_price_as_of, $story.nvda_quantity, $story.nvda_market_value_as_of_usd, $story.nvda_weight_as_of_pct, $story.total_value_as_of_usd, $story.it_sector_weight_as_of_pct, $story.cash_and_equivalents_as_of_usd, $story.nvda_basis_per_share),
        'Worth reviewing before your Thursday meeting. The portfolio overview in your OneDrive has the allocation chart and the holdings table; my note from last week has the concentration table.',
        $analystName.Split(' ')[0])) $analystName $analystUpn (ConvertTo-EasternUtc '2026-10-19 16:45')
}

if ($Stage -contains 'Meeting') {
    Write-Output 'Meeting stage'
    Add-RecordedMeeting $analystUpn 'Q4 portfolio review - Michael Scott' '2026-10-16T22:30:00' '2026-10-16T23:00:00' (
        '<p>Quarterly review with Michael Scott (client C0007). Recorded and transcribed for the demo; all content is synthetic.</p>' +
        '<p>Agenda: portfolio and NVDA concentration against the 25% single-stock limit; home purchase timing and down payment; ESG preference for new contributions; 2026 realized-gain tax budget.</p>')
    $scriptCopy = Join-Path $env:TEAM_WORK_DIR 'meeting-script.md'
    Convert-StoryText (Get-Content (Join-Path $PSScriptRoot 'meeting-script.md') -Raw) | Set-Content -LiteralPath $scriptCopy -Encoding utf8
    Write-Output "  Recording script with this story week's dates: $scriptCopy"
}

if ($Stage -contains 'OneDrive') {
    Write-Output 'OneDrive stage'
    $pdfs = Get-Pdfs
    foreach ($name in 'Michael Scott - Portfolio Overview (Oct 2026).pdf', 'Michael Scott - IPS v1 (signed).pdf') {
        Add-OneDriveFile 'Client Files/Michael Scott' $name (Join-Path $pdfs $name)
    }
}

if ($Stage -contains 'Monday') {
    Write-Output 'Monday stage'
    $docs = Get-Documents
    Add-SharePointFile 'Client Files/Michael Scott' 'IPS v2 DRAFT.docx' (Join-Path $docs 'IPS v2 DRAFT.docx')
}

if ($Stage -contains 'Live') {
    Write-Output 'Live stage'
    Add-InboxMessage 'One more thing before Thursday: 529 plan' (ConvertTo-Html @(
        "Hi $advisorFirst,",
        "One more thing for Thursday: I'd like to open a 529 college savings plan for my niece and put in `$25,000 this year. Does that still work with the house closing?",
        'Michael')) 'Michael Scott' $clientEmail ((Get-Date).ToUniversalTime())
}

if ($Stage -contains 'Status') {
    Write-Output 'Calendar (Michael Scott):'
    $meeting = Get-StoryMeetingDate
    $events = Invoke-Graph Get "/users/$user/calendarView?startDateTime=$($meeting.AddDays(-21).ToString('yyyy-MM-dd'))T00:00:00Z&endDateTime=$($meeting.AddDays(10).ToString('yyyy-MM-dd'))T00:00:00Z&`$select=subject,start,onlineMeeting,isOnlineMeeting&`$top=100"
    $events.value | Where-Object subject -like '*Michael Scott*' | ForEach-Object {
        "  {0} | {1} UTC | Teams: {2}" -f $_.subject, $_.start.dateTime, $(if ($_.onlineMeeting.joinUrl) { $_.onlineMeeting.joinUrl } else { 'none' })
    }
    Write-Output 'Mail:'
    $mail = Invoke-Graph Get "/users/$user/messages?`$search=`"Michael Scott`"&`$select=subject,from,receivedDateTime,isDraft&`$top=25"
    $mail.value | ForEach-Object { "  {0} | {1} | from {2} | draft={3}" -f $_.receivedDateTime, $_.subject, $_.from.emailAddress.address, $_.isDraft }
    Write-Output 'OneDrive files:'
    try { (Invoke-Graph Get "/users/$user/drive/root:/Client%20Files/Michael%20Scott:/children?`$select=name,lastModifiedDateTime,webUrl").value | ForEach-Object { "  OneDrive/Client Files/Michael Scott/$($_.name) | $($_.lastModifiedDateTime)" } } catch { '  (none)' }
    Write-Output 'SharePoint files:'
    foreach ($folder in 'Client Files/Michael Scott', 'Research') {
        $path = ($folder.Split('/') | ForEach-Object { [uri]::EscapeDataString($_) }) -join '/'
        try { (Invoke-Graph Get "/sites/root/drive/root:/${path}:/children?`$select=name,lastModifiedDateTime,webUrl").value | ForEach-Object { "  $folder/$($_.name) | $($_.lastModifiedDateTime)" } } catch { "  $folder (none)" }
    }
}
Write-Output 'Done. Work IQ indexes new mail within minutes; new SharePoint files can take longer.'
