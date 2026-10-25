<#
.SYNOPSIS
    Shared helpers for the advisor team scripts. Dot-source after Use-IsolatedAzure.ps1.
#>

. (Join-Path $PSScriptRoot 'StoryDates.ps1')

function Get-TenantScopedName {
    <# Names that are unique per tenant (Entra apps, the Fabric workspace) get the deployment's TEAM_NAME_SUFFIX,
       so two deployments in one tenant never share or delete each other's items. No suffix: the name as given. #>
    param([Parameter(Mandatory)][string]$Base)
    if ($env:TEAM_NAME_SUFFIX) { "$Base-$($env:TEAM_NAME_SUFFIX)" } else { $Base }
}

function Set-EnvLocalValue {
    <# Upserts KEY=value pairs in the git-ignored .env.local and exports them to this process. #>
    param([Parameter(Mandatory)][hashtable]$Values)
    $file = Join-Path (Split-Path $PSScriptRoot -Parent) '.env.local'
    $lines = [System.Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $file) { foreach ($l in Get-Content -LiteralPath $file) { $lines.Add($l) } }
    foreach ($key in $Values.Keys) {
        $entry = "$key=$($Values[$key])"
        $index = -1
        for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^\s*$([regex]::Escape($key))\s*=") { $index = $i; break } }
        if ($index -ge 0) { $lines[$index] = $entry } else { $lines.Add($entry) }
        Set-Item -Path "Env:$key" -Value ([string]$Values[$key])
    }
    Set-Content -LiteralPath $file -Value $lines -Encoding utf8
}

function Get-AzToken {
    param([Parameter(Mandatory)][string]$Resource)
    $token = az account get-access-token --resource $Resource --query accessToken -o tsv
    if ($LASTEXITCODE -ne 0 -or -not $token) { throw "Could not get a token for $Resource from the isolated Azure CLI profile." }
    $token
}

function Get-ProjectConnectionName {
    <# A project connection's name for this deployment. Connection names are unique across a Foundry account, so a
       deployment in a shared, existing account adds its own suffix (Terraform output connection_suffix); a
       deployment that created its account uses the plain name. #>
    param([Parameter(Mandatory)][string]$Base)
    if ($null -eq $script:AdvisorConnectionSuffix) {
        $tf = Join-Path (Split-Path $PSScriptRoot -Parent) 'terraform'
        $value = terraform "-chdir=$tf" output -raw -no-color connection_suffix 2>$null
        $script:AdvisorConnectionSuffix = if ($LASTEXITCODE -eq 0 -and $value) { "$value".Trim() } else { '' }
    }
    "$Base$($script:AdvisorConnectionSuffix)"
}

function Get-WebBoundaryTerms {
    <# Names Web IQ must never receive (WEB_BOUNDARY_TERMS): every client in the synthetic portfolio data, the advisor
       and the analyst. Tickers are deliberately not included: public market news about them is the point of Web IQ. #>
    param([string[]]$Extra = @())
    $data = Join-Path (Split-Path $PSScriptRoot -Parent) 'data\fabric\notebooks\portfolio_data.py'
    # Client rows: id, name, segment, risk profile, city, state, client since, portfolio value.
    $clients = [regex]::Matches((Get-Content -LiteralPath $data -Raw),
        '(?m)^\s*\("C\d{4}", "([^"]+)", "[^"]+", "[^"]+", "[^"]+", "[A-Z]{2}", dt\.date\(') | ForEach-Object { $_.Groups[1].Value }
    if (-not $clients) { throw "No client rows found in $data; update Get-WebBoundaryTerms." }
    @(@($clients) + @($env:DEMO_ADVISOR_NAME, $env:DEMO_ANALYST_NAME) + $Extra | Where-Object { $_ } | Select-Object -Unique)
}

function Grant-OidcAdminConsent {
    <# Tenant-wide consent for the OpenID scopes a tool connection requests (openid, profile, offline_access),
       so a user's one-time tool authorization is a silent sign-in rather than a consent prompt. #>
    param([Parameter(Mandatory)][string]$ClientId)
    $graph = 'https://graph.microsoft.com/v1.0'
    $graphSp = (az rest --method get --url "$graph/servicePrincipals?`$filter=appId eq '00000003-0000-0000-c000-000000000000'&`$select=id" -o json | ConvertFrom-Json).value[0].id
    $caller = (az rest --method get --url "$graph/servicePrincipals?`$filter=appId eq '$ClientId'&`$select=id,displayName" -o json | ConvertFrom-Json).value[0]
    if (-not $caller) { throw "Service principal for $ClientId is missing." }
    $wanted = @('openid', 'profile', 'offline_access')
    $grant = @((az rest --method get --url "$graph/oauth2PermissionGrants?`$filter=clientId eq '$($caller.id)' and resourceId eq '$graphSp'" -o json | ConvertFrom-Json).value |
        Where-Object consentType -eq 'AllPrincipals')[0]
    $bodyFile = Join-Path $env:TEAM_WORK_DIR 'oidc-grant.json'
    if (-not $grant) {
        @{ clientId = $caller.id; consentType = 'AllPrincipals'; resourceId = $graphSp; scope = ($wanted -join ' ') } |
            ConvertTo-Json | Set-Content -Path $bodyFile -Encoding ascii
        az rest --method post --url "$graph/oauth2PermissionGrants" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
    } else {
        $current = @($grant.scope -split ' ' | Where-Object { $_ })
        if (@($wanted | Where-Object { $current -notcontains $_ }).Count -gt 0) {
            @{ scope = ((($current + $wanted) | Sort-Object -Unique) -join ' ') } | ConvertTo-Json | Set-Content -Path $bodyFile -Encoding ascii
            az rest --method patch --url "$graph/oauth2PermissionGrants/$($grant.id)" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
        }
    }
    Remove-Item -Path $bodyFile -ErrorAction SilentlyContinue
    Write-Output "  sign-in scopes pre-approved for $($caller.displayName): $($wanted -join ', ')"
}

function Grant-DelegatedAdminConsent {
    <# Grants tenant-wide (AllPrincipals) consent for every delegated scope an app registration requests. #>
    param([Parameter(Mandatory)][string]$ClientId)
    $graph = 'https://graph.microsoft.com/v1.0'
    $app = (az rest --method get --url "$graph/applications?`$filter=appId eq '$ClientId'&`$select=displayName,requiredResourceAccess" -o json | ConvertFrom-Json).value[0]
    $caller = (az rest --method get --url "$graph/servicePrincipals?`$filter=appId eq '$ClientId'&`$select=id" -o json | ConvertFrom-Json).value[0]
    if (-not $app -or -not $caller) { throw "App registration or service principal $ClientId is missing." }
    foreach ($access in $app.requiredResourceAccess) {
        $resource = (az rest --method get --url "$graph/servicePrincipals?`$filter=appId eq '$($access.resourceAppId)'&`$select=id,oauth2PermissionScopes" -o json | ConvertFrom-Json).value[0]
        if (-not $resource) { throw "Service principal for $($access.resourceAppId) is missing." }
        $requested = @($access.resourceAccess | Where-Object type -eq 'Scope' | ForEach-Object {
            $id = $_.id; ($resource.oauth2PermissionScopes | Where-Object id -eq $id).value
        } | Where-Object { $_ })
        if (-not $requested) { continue }
        $grants = @((az rest --method get --url "$graph/oauth2PermissionGrants?`$filter=clientId eq '$($caller.id)' and resourceId eq '$($resource.id)'" -o json | ConvertFrom-Json).value |
            Where-Object consentType -eq 'AllPrincipals')
        $bodyFile = Join-Path $env:TEAM_WORK_DIR 'grant.json'
        if ($grants.Count -eq 0) {
            @{ clientId = $caller.id; resourceId = $resource.id; consentType = 'AllPrincipals'; scope = ($requested -join ' ') } |
                ConvertTo-Json | Set-Content -Path $bodyFile -Encoding ascii
            az rest --method post --url "$graph/oauth2PermissionGrants" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
        } else {
            $current = @($grants[0].scope -split ' ' | Where-Object { $_ })
            $missing = @($requested | Where-Object { $current -notcontains $_ })
            if ($missing.Count -gt 0) {
                @{ scope = (($current + $missing) | Sort-Object -Unique) -join ' ' } | ConvertTo-Json | Set-Content -Path $bodyFile -Encoding ascii
                az rest --method patch --url "$graph/oauth2PermissionGrants/$($grants[0].id)" --body "@$bodyFile" --headers 'Content-Type=application/json' -o none
            }
        }
        Write-Output "  consent for $($app.displayName): $($requested -join ', ')"
    }
}
