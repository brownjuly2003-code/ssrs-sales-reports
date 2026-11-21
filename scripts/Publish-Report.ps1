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

    # What the server returned for the data sources, seen through reflection and through the
    # PowerShell adapter. An earlier version stored the returned definition in $definition, which
    # in PowerShell is the same variable as the parameter [string]$Definition (variable names are
    # case-insensitive, and a type-constrained parameter converts what is assigned to it), so the
    # object became a String. Variables here never reuse a parameter name
    # (tests/test_windows_workflow.py checks it).
    $sources = @($proxy.GetItemDataSources($report))
    foreach ($source in $sources) {
        $item = Get-RsMember $source 'Item'
        Write-Output "data source $(Get-RsMember $source 'Name') returned: $(Get-RsShape $source); Item by reflection: $(Get-RsShape $item)"
        $adapted = $source.PSObject.Properties['Item']
        if ($adapted) {
            $value = if ($null -ne $adapted.Value) { $adapted.Value.GetType().FullName } else { 'null' }
            Write-Output "  PowerShell adapter: Item is a $($adapted.MemberType) of $($adapted.TypeNameOfValue), value $value"
        }
    }
    if (@($sources | Where-Object { (Get-RsMember $_ 'Name') -eq 'Retail' }).Count -ne 1) {
        throw 'The report has no single data source named Retail.'
    }

    # A new definition instead of the returned one: provider and connection string from the
    # unchanged RDL, credentials stored on the server (subscriptions run without a user).
    # Members: https://learn.microsoft.com/dotnet/api/reportservice2010.datasourcedefinition
    $rdl = New-Object Xml.XmlDocument
    $rdl.Load((Resolve-Path -LiteralPath $Definition).Path)
    $properties = $rdl.SelectSingleNode("//*[local-name()='DataSource'][@Name='Retail']/*[local-name()='ConnectionProperties']")
    if (-not $properties) { throw "No ConnectionProperties of the data source Retail in $Definition" }
    $provider = $properties.SelectSingleNode("*[local-name()='DataProvider']").InnerText
    $connect = $properties.SelectSingleNode("*[local-name()='ConnectString']").InnerText
    Write-Output "data source Retail in the RDL: provider $provider, '$connect'"
    $sourceDefinition = New-RsObject $proxy 'DataSourceDefinition'
    $values = [ordered]@{
        Extension = $provider; ConnectString = $connect; CredentialRetrieval = 'Store'
        UserName = $ReaderLogin; Password = $readerPassword; WindowsCredentials = $false
        ImpersonateUser = $false; ImpersonateUserSpecified = $true; Enabled = $true; EnabledSpecified = $true
    }
    foreach ($member in $values.Keys) { Set-RsMember $sourceDefinition $member $values[$member] }
    $source = New-RsObject $proxy 'DataSource'
    Set-RsMember $source 'Name' 'Retail'
    Set-RsMember $source 'Item' $sourceDefinition
    $proxy.SetItemDataSources($report, (ConvertTo-RsArray $proxy 'DataSource' @($source)))

    # Read back through reflection and, independently of the proxy, from the catalog database.
    $returned = @($proxy.GetItemDataSources($report) | Where-Object { (Get-RsMember $_ 'Name') -eq 'Retail' })
    $stored = Get-RsMember $returned[0] 'Item'
    Write-Output "data source Retail read back: $(Get-RsShape $stored)"
    $catalog = sqlcmd -S localhost -E -C -I -b -h -1 -W -s '|' -d ReportServer -Q "SET NOCOUNT ON; SELECT d.Name, d.Extension, d.CredentialRetrieval, CASE WHEN d.UserName IS NULL THEN 'no user' ELSE 'user stored' END, CASE WHEN d.Password IS NULL THEN 'no password' ELSE 'password stored' END FROM dbo.DataSource AS d JOIN dbo.Catalog AS c ON c.ItemID = d.ItemID WHERE c.Path = N'$report'"
    if ($LASTEXITCODE -ne 0) { throw "the catalog query exited with $LASTEXITCODE" }
    $catalog = @($catalog | Where-Object { $_ -match '\|' })
    Write-Output "catalog dbo.DataSource of ${report}: $($catalog -join ' / ')"
    if ($null -ne $stored -and $stored.GetType().Name -eq 'DataSourceDefinition') {
        $credentials = "$(Get-RsMember $stored 'CredentialRetrieval')"
        Write-Output "data source Retail stored: '$(Get-RsMember $stored 'ConnectString')', credentials $credentials, user $(Get-RsMember $stored 'UserName'), Windows credentials $(Get-RsMember $stored 'WindowsCredentials')"
        if ($credentials -ne 'Store') { throw 'The credentials of the data source are not stored.' }
    } else {
        # The proxy gave no definition back: the catalog row decides.
        Write-Output "data source Retail stored (catalog): $($catalog -join ' / ')"
        if ($catalog.Count -ne 1 -or $catalog[0] -notmatch '\|user stored\|password stored$') {
            throw 'The credentials of the data source are not stored (catalog row above).'
        }
    }

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
