<#
.SYNOPSIS
Configures a freshly installed SQL Server 2022 Reporting Services (native mode) without
the Report Server Configuration Manager.

.DESCRIPTION
The same calls the Configuration Manager makes, through the WMI provider of Reporting Services
(class MSReportServer_ConfigurationSetting, namespace root\Microsoft\SqlServer\ReportServer\RS_<instance>\v<version>\Admin):
 1. URLs of the web service (/ReportServer) and the web portal (/Reports) on one port;
 2. the catalog database: GenerateDatabaseCreationScript and GenerateDatabaseRightsScript are
    executed with sqlcmd, then SetDatabaseConnection with the service account (CredentialsType 2);
 3. e-mail delivery: SetEmailConfiguration to a local SMTP server;
 4. rsreportserver.config: RSWindowsBasic next to the default Windows authentication
    (the subscription script uses Basic), SendEmailToUserAlias False, SMTP port;
 5. SetServiceState, service restart, wait for the web service, InitializeReportServer if needed.
Prints the version and edition of the instance. The generated SQL scripts and the edited
configuration sections are saved in OutDir.

Sources:
 https://learn.microsoft.com/sql/reporting-services/wmi-provider-library-reference/configurationsetting-class
 (methods GenerateDatabaseCreationScript, GenerateDatabaseRightsScript, SetDatabaseConnection,
 SetVirtualDirectory, ReserveURL, SetEmailConfiguration, SetServiceState, InitializeReportServer);
 https://learn.microsoft.com/sql/reporting-services/security/configure-basic-authentication-on-the-report-server;
 the same sequence in the module ReportingServicesTools (Set-RsDatabase, Set-RsUrlReservation),
 https://github.com/microsoft/ReportingServicesTools.
#>
param(
    [string]$DatabaseServer = 'localhost',
    [string]$DatabaseName = 'ReportServer',
    [int]$Port = 80,
    [string]$SmtpServer = '127.0.0.1',
    [int]$SmtpPort = 25,
    [string]$Sender = 'ssrs@example.invalid',
    [Parameter(Mandatory = $true)][string]$OutDir
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Lcid = 1033

function Get-RsNamespace {
    $root = 'root\Microsoft\SqlServer\ReportServer'
    $instance = Get-CimInstance -Namespace $root -ClassName __NAMESPACE | Select-Object -First 1
    if (-not $instance) { throw "No Reporting Services instance under the WMI namespace $root" }
    $version = Get-CimInstance -Namespace "$root\$($instance.Name)" -ClassName __NAMESPACE |
        Where-Object { $_.Name -match '^v\d+$' } |
        Sort-Object { [int]$_.Name.Substring(1) } -Descending | Select-Object -First 1
    if (-not $version) { throw "No version namespace under $root\$($instance.Name)" }
    "$root\$($instance.Name)\$($version.Name)"
}

function Get-RsConfig {
    Get-CimInstance -Namespace "$script:Namespace\Admin" -ClassName MSReportServer_ConfigurationSetting |
        Select-Object -First 1
}

# HRESULT of ERROR_ALREADY_EXISTS: a URL that is already reserved is not an error here.
$AlreadyExists = -2147024713

function Invoke-RsConfig([string]$Method, [hashtable]$Arguments = @{}, [int[]]$Accept = @()) {
    $result = Invoke-CimMethod -InputObject (Get-RsConfig) -MethodName $Method -Arguments $Arguments
    if ($result.HRESULT -ne 0 -and $Accept -notcontains $result.HRESULT) {
        throw "$Method failed: HRESULT $($result.HRESULT); $(@($result.Error) -join '; ')"
    }
    Write-Host "$Method -> HRESULT $($result.HRESULT)"
    return $result
}

function Invoke-SqlScript([string]$Text, [string]$Name) {
    $file = Join-Path $OutDir "$Name.sql"
    [IO.File]::WriteAllText($file, $Text, (New-Object Text.UTF8Encoding($true)))
    # -x: no sqlcmd variable substitution inside the generated script.
    sqlcmd -S $DatabaseServer -E -C -b -x -i $file
    if ($LASTEXITCODE -ne 0) { throw "sqlcmd $Name.sql exited with $LASTEXITCODE" }
    Write-Host "$Name.sql executed"
}

$script:Namespace = Get-RsNamespace
$config = Get-RsConfig
$instance = Get-CimInstance -Namespace $script:Namespace -ClassName MSReportServer_Instance | Select-Object -First 1
Write-Output "Reporting Services WMI namespace: $script:Namespace"
Write-Output "Reporting Services version: $($instance.Version)"
Write-Output "Reporting Services edition: $($instance.EditionName) (EditionID $($instance.EditionID))"
Write-Output "Reporting Services service: $($config.ServiceName), account $($config.WindowsServiceIdentityActual)"
Write-Output "Reporting Services configuration file: $($config.PathName)"

# 1. URLs. Nothing else should listen on the port: print the owner if something does.
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($connection in $busy) {
    $owner = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
    Write-Host "port $Port is already open: $($connection.LocalAddress) by $($owner.ProcessName) ($($connection.OwningProcess))"
}
Invoke-RsConfig SetVirtualDirectory @{ Application = 'ReportServerWebService'; VirtualDirectory = 'ReportServer'; Lcid = $Lcid } | Out-Null
Invoke-RsConfig ReserveURL @{ Application = 'ReportServerWebService'; UrlString = "http://+:$Port"; Lcid = $Lcid } -Accept $AlreadyExists | Out-Null
Invoke-RsConfig SetVirtualDirectory @{ Application = 'ReportServerWebApp'; VirtualDirectory = 'Reports'; Lcid = $Lcid } | Out-Null
Invoke-RsConfig ReserveURL @{ Application = 'ReportServerWebApp'; UrlString = "http://+:$Port"; Lcid = $Lcid } -Accept $AlreadyExists | Out-Null

# 2. Catalog database on the local engine, connected with the service account.
$create = Invoke-RsConfig GenerateDatabaseCreationScript @{ DatabaseName = $DatabaseName; Lcid = $Lcid; IsSharePointMode = $false }
Invoke-SqlScript $create.Script 'reportserver-create'
$account = $config.WindowsServiceIdentityActual
$rights = Invoke-RsConfig GenerateDatabaseRightsScript @{ UserName = $account; DatabaseName = $DatabaseName; IsRemote = $false; IsWindowsUser = $true }
Invoke-SqlScript $rights.Script 'reportserver-rights'
Invoke-RsConfig SetDatabaseConnection @{ Server = $DatabaseServer; DatabaseName = $DatabaseName; CredentialsType = 2; UserName = ''; Password = '' } | Out-Null

# 3. E-mail delivery to the local SMTP receiver.
Invoke-RsConfig SetEmailConfiguration @{ SendUsingSMTPServer = $true; SMTPServer = $SmtpServer; SenderEmailAddress = $Sender } | Out-Null

# 4. rsreportserver.config: Basic authentication and the e-mail details WMI does not set.
$path = (Get-RsConfig).PathName
if (-not $path -or -not (Test-Path -LiteralPath $path)) {
    $path = 'C:\Program Files\Microsoft SQL Server Reporting Services\SSRS\ReportServer\rsreportserver.config'
}
$xml = New-Object Xml.XmlDocument
$xml.PreserveWhitespace = $true
$xml.Load($path)
$types = $xml.SelectSingleNode('/Configuration/Authentication/AuthenticationTypes')
if (-not $types) { throw "No Authentication/AuthenticationTypes in $path" }
if (-not $types.SelectSingleNode('RSWindowsBasic')) {
    $basic = $xml.CreateElement('RSWindowsBasic')
    foreach ($pair in @(@('LogonMethod', '3'), @('Realm', ''), @('DefaultDomain', ''))) {
        $node = $xml.CreateElement($pair[0])
        $node.InnerText = $pair[1]
        $basic.AppendChild($node) | Out-Null
    }
    $types.AppendChild($basic) | Out-Null
}
$email = $xml.SelectSingleNode("//Extension[@Name='Report Server Email']/Configuration/RSEmailDPConfiguration")
if (-not $email) { throw "No RSEmailDPConfiguration in $path" }
foreach ($pair in @(@('SendEmailToUserAlias', 'False'), @('SMTPServerPort', "$SmtpPort"), @('SendUsing', '2'), @('SMTPAuthenticate', '0'))) {
    $node = $email.SelectSingleNode($pair[0])
    if (-not $node) {
        $node = $xml.CreateElement($pair[0])
        $email.AppendChild($node) | Out-Null
    }
    $node.InnerText = $pair[1]
}
$xml.Save($path)
$sections = @($types.OuterXml, $email.OuterXml) -join "`r`n"
Set-Content -Encoding UTF8 -Path (Join-Path $OutDir 'rsreportserver-sections.xml') -Value $sections
Write-Host $sections

# 5. Services on, restart, wait for the web service.
Invoke-RsConfig SetServiceState @{ EnableWindowsService = $true; EnableWebService = $true; EnableReportManager = $true } | Out-Null
$service = $config.ServiceName
if (-not $service) { $service = 'SQLServerReportingServices' }
Restart-Service -Name $service -Force
(Get-Service -Name $service).WaitForStatus('Running', [TimeSpan]::FromMinutes(3))
$reserved = Invoke-RsConfig ListReservedUrls
for ($i = 0; $i -lt @($reserved.Application).Count; $i++) {
    Write-Output "reserved URL: $(@($reserved.Application)[$i]) $(@($reserved.UrlString)[$i])"
}
$url = "http://localhost:$Port/ReportServer"
$watch = [Diagnostics.Stopwatch]::StartNew()
$status = $null
while ($watch.Elapsed.TotalMinutes -lt 6) {
    try {
        $status = (Invoke-WebRequest -Uri $url -UseBasicParsing -UseDefaultCredentials -TimeoutSec 60).StatusCode
        if ($status -eq 200) { break }
    } catch {
        $status = $_.Exception.Message
        $response = $_.Exception.Response
        # 401 or 403: the server is up, but the runner account is not admitted (UAC token?).
        if ($response -and @(401, 403) -contains [int]$response.StatusCode) { break }
    }
    Start-Sleep -Seconds 10
}
Write-Output "web service $url answered $status after $([math]::Round($watch.Elapsed.TotalSeconds)) s"
if ($status -ne 200) { throw "The report server web service did not answer 200: $status" }
$config = Get-RsConfig
if (-not $config.IsInitialized) {
    Invoke-RsConfig InitializeReportServer @{ InstallationID = $config.InstallationID } | Out-Null
    $config = Get-RsConfig
}
Write-Output "Reporting Services initialized: $($config.IsInitialized); database $($config.DatabaseServerName)/$($config.DatabaseName)"
if (-not $config.IsInitialized) { throw 'The report server is not initialized.' }
