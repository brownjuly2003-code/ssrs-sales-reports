<#
.SYNOPSIS
Publishes rdl/SalesStock.rdl as /Retail/SalesStock and stores the credentials of its data source.

.DESCRIPTION
Runs with the Windows credentials of the current process (a report server administrator).
 1. Content Manager on the root folder for the subscription client (inherited by /Retail);
 2. folder /Retail and the report from the unchanged RDL; the server validates the definition
    and its warnings are printed and saved;
 3. the embedded data source "Retail" keeps its connection string from the RDL and gets stored
    SQL Server credentials (CredentialRetrieval Store): subscriptions run without a user;
 4. TestConnectForItemDataSource with these credentials;
 5. a request of the client with HTTP Basic, as the subscription script sends it.
Passwords come from environment variables, never from parameters, and are not printed.
Source: ReportService2010 methods GetPolicies, SetPolicies, CreateFolder, CreateCatalogItem,
GetItemDataSources, SetItemDataSources, TestConnectForItemDataSource
(https://learn.microsoft.com/dotnet/api/reportservice2010.reportingservice2010).
#>
param(
    [string]$Url = 'http://localhost/ReportServer',
    [Parameter(Mandatory = $true)][string]$ClientUser,
    [string]$ClientPasswordVariable = 'CLIENT_PASSWORD',
    [Parameter(Mandatory = $true)][string]$ReaderLogin,
    [string]$ReaderPasswordVariable = 'READER_PASSWORD',
    [string]$Definition = 'rdl/SalesStock.rdl',
    [string]$Folder = 'Retail',
    [string]$Name = 'SalesStock',
    [Parameter(Mandatory = $true)][string]$OutDir
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'RsProxy.ps1')
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$readerPassword = [Environment]::GetEnvironmentVariable($ReaderPasswordVariable)
$clientPassword = [Environment]::GetEnvironmentVariable($ClientPasswordVariable)
if (-not $readerPassword -or -not $clientPassword) { throw "Set $ReaderPasswordVariable and $ClientPasswordVariable." }
$report = "/$Folder/$Name"

try {
    $proxy = New-RsProxy $Url

    $inherit = $true
    $policies = @($proxy.GetPolicies('/', [ref]$inherit))
    $before = ($policies | ForEach-Object { "$($_.GroupUserName)=$((@($_.Roles) | ForEach-Object { $_.Name }) -join '+')" }) -join ', '
    Write-Output "policies on / before: $before"
    $role = New-RsObject $proxy 'Role'
    $role.Name = 'Content Manager'
    $policy = New-RsObject $proxy 'Policy'
    $policy.GroupUserName = $ClientUser
    $policy.Roles = ConvertTo-RsArray $proxy 'Role' @($role)
    $proxy.SetPolicies('/', (ConvertTo-RsArray $proxy 'Policy' ($policies + $policy)))
    Write-Output "Content Manager on / for $ClientUser"

    $proxy.CreateFolder($Folder, '/', $null) | Out-Null
    $bytes = [IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Definition).Path)
    $warnings = $null
    $item = $proxy.CreateCatalogItem('Report', $Name, "/$Folder", $true, $bytes, $null, [ref]$warnings)
    $lines = @($warnings | Where-Object { $_ } | ForEach-Object { "$($_.Severity) $($_.Code) $($_.ObjectType) $($_.ObjectName): $($_.Message)" })
    Set-Content -Encoding UTF8 -Path (Join-Path $OutDir 'publish-warnings.txt') -Value $lines
    Write-Output "published $($item.Path) ($($item.TypeName), $($item.Size) bytes): $($lines.Count) warnings"
    $lines | ForEach-Object { Write-Output "  $_" }

    $sources = $proxy.GetItemDataSources($report)
    $retail = @($sources | Where-Object { $_.Name -eq 'Retail' })
    if ($retail.Count -ne 1) { throw "The report has $($retail.Count) data sources named Retail." }
    $definition = $retail[0].Item
    Write-Output "data source Retail: $($definition.GetType().Name), extension $($definition.Extension), '$($definition.ConnectString)', credentials $($definition.CredentialRetrieval)"
    $definition.CredentialRetrieval = 'Store'
    $definition.UserName = $ReaderLogin
    $definition.Password = $readerPassword
    $definition.WindowsCredentials = $false
    $definition.ImpersonateUser = $false
    $definition.ImpersonateUserSpecified = $true
    $proxy.SetItemDataSources($report, $sources)
    $stored = @($proxy.GetItemDataSources($report) | Where-Object { $_.Name -eq 'Retail' })[0].Item
    Write-Output "data source Retail stored: '$($stored.ConnectString)', credentials $($stored.CredentialRetrieval), user $($stored.UserName), Windows credentials $($stored.WindowsCredentials)"
    if ("$($stored.CredentialRetrieval)" -ne 'Store') { throw 'The credentials of the data source are not stored.' }

    $connectError = $null
    $connected = $proxy.TestConnectForItemDataSource($report, 'Retail', $ReaderLogin, $readerPassword, [ref]$connectError)
    Write-Output "TestConnectForItemDataSource: $connected $connectError"
    if (-not $connected) { throw "The data source does not connect: $connectError" }
} catch {
    throw "ReportService2010: $(Get-SoapMessage $_)"
}

$token = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${ClientUser}:$clientPassword"))
$listing = Invoke-WebRequest -UseBasicParsing -Headers @{ Authorization = "Basic $token" } -Uri "${Url}?/$Folder&rs:Command=ListChildren" -TimeoutSec 120
Write-Output "Basic authentication of ${ClientUser}: HTTP $($listing.StatusCode), $Name listed: $($listing.Content -match $Name)"
if ($listing.Content -notmatch $Name) { throw "The client does not see $report." }
