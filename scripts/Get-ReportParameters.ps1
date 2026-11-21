<#
.SYNOPSIS
Asks the report server for the parameters of the report as it would render them
(GetItemParameters, ForRendering = true) with the given regions selected, and saves the answer as JSON.

.DESCRIPTION
The server runs the queries of the valid values (dbo.Regions, dbo.Stores with the selected
regions, dbo.Categories, dbo.SnapshotDates) through the stored credentials of the data source.
The JSON (a list of parameters: Name, Type, State, Dependencies, DefaultValues, ValidValues
with Label and Value, ErrorMessage) is checked by scripts/check_server.py cascade.
Source: ReportingService2010.GetItemParameters
(https://learn.microsoft.com/dotnet/api/reportservice2010.reportingservice2010.getitemparameters).
#>
param(
    [string]$Url = 'http://localhost/ReportServer',
    [string]$Report = '/Retail/SalesStock',
    [Parameter(Mandatory = $true)][int[]]$Regions,
    [Parameter(Mandatory = $true)][string]$OutFile
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'RsProxy.ps1')

function ConvertTo-List($Items, [scriptblock]$Map) {
    if ($null -eq $Items) { return , @() }
    return , @($Items | ForEach-Object $Map)
}

try {
    $proxy = New-RsProxy $Url
    $values = foreach ($region in $Regions) {
        $value = New-RsObject $proxy 'ParameterValue'
        Set-RsMember $value 'Name' 'RegionIds'
        Set-RsMember $value 'Value' "$region"
        $value
    }
    # HistoryID is a .NET string: PowerShell turns $null into '' for string arguments, and
    # the server then parses '' as a snapshot date (rsParameterTypeMismatch 'snapshotID').
    # [NullString]::Value passes a real null, i.e. the live report, not a snapshot.
    $values = ConvertTo-RsArray $proxy 'ParameterValue' @($values)
    $parameters = $proxy.GetItemParameters($Report, [NullString]::Value, $true, $values, $null)
} catch {
    throw "GetItemParameters: $(Get-SoapMessage $_)"
}
# Members are read through reflection (scripts/RsProxy.ps1), not the PowerShell adapter.
$dump = foreach ($parameter in $parameters) {
    [pscustomobject]@{
        Name          = Get-RsMember $parameter 'Name'
        Type          = "$(Get-RsMember $parameter 'ParameterTypeName')"
        State         = "$(Get-RsMember $parameter 'State')"
        Dependencies  = ConvertTo-List (Get-RsMember $parameter 'Dependencies') { "$_" }
        DefaultValues = ConvertTo-List (Get-RsMember $parameter 'DefaultValues') { "$_" }
        ValidValues   = ConvertTo-List (Get-RsMember $parameter 'ValidValues') {
            [pscustomobject]@{ Label = (Get-RsMember $_ 'Label'); Value = (Get-RsMember $_ 'Value') }
        }
        ErrorMessage  = Get-RsMember $parameter 'ErrorMessage'
    }
}
$json = ConvertTo-Json -InputObject @($dump) -Depth 6
# .NET resolves relative paths against the process directory, not the PowerShell location.
$path = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutFile)
[IO.File]::WriteAllText($path, $json, (New-Object Text.UTF8Encoding($false)))
foreach ($item in $dump) {
    $shown = @($item.ValidValues | Select-Object -First 8 | ForEach-Object { "$($_.Value)=$($_.Label)" }) -join ', '
    Write-Output "regions $($Regions -join ','): $($item.Name) [$($item.State)] $(@($item.ValidValues).Count) valid values: $shown $($item.ErrorMessage)"
}
