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
        $value.Name = 'RegionIds'
        $value.Value = "$region"
        $value
    }
    $parameters = $proxy.GetItemParameters($Report, $null, $true, (ConvertTo-RsArray $proxy 'ParameterValue' @($values)), $null)
} catch {
    throw "GetItemParameters: $(Get-SoapMessage $_)"
}
$dump = foreach ($parameter in $parameters) {
    [pscustomobject]@{
        Name          = $parameter.Name
        Type          = "$($parameter.ParameterTypeName)"
        State         = "$($parameter.State)"
        Dependencies  = ConvertTo-List $parameter.Dependencies { "$_" }
        DefaultValues = ConvertTo-List $parameter.DefaultValues { "$_" }
        ValidValues   = ConvertTo-List $parameter.ValidValues { [pscustomobject]@{ Label = $_.Label; Value = $_.Value } }
        ErrorMessage  = $parameter.ErrorMessage
    }
}
$json = ConvertTo-Json -InputObject @($dump) -Depth 6
[IO.File]::WriteAllText($OutFile, $json, (New-Object Text.UTF8Encoding($false)))
foreach ($item in $dump) {
    $shown = @($item.ValidValues | Select-Object -First 8 | ForEach-Object { "$($_.Value)=$($_.Label)" }) -join ', '
    Write-Output "regions $($Regions -join ','): $($item.Name) [$($item.State)] $(@($item.ValidValues).Count) valid values: $shown $($item.ErrorMessage)"
}
