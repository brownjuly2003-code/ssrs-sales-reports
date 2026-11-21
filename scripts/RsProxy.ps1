<#
.SYNOPSIS
Dot-sourced helpers: a SOAP proxy of ReportService2010 with the Windows credentials of the
current process (New-WebServiceProxy, Windows PowerShell 5.1), typed arrays for its calls and
reflection access to members of proxy objects.
#>

function New-RsProxy([string]$Url) {
    $proxy = New-WebServiceProxy -Uri "$Url/ReportService2010.asmx?wsdl" -UseDefaultCredential
    $proxy.Timeout = 300000
    return $proxy
}

function New-RsObject($Proxy, [string]$Type) {
    New-Object "$($Proxy.GetType().Namespace).$Type"
}

function ConvertTo-RsArray($Proxy, [string]$Type, [object[]]$Items) {
    $array = [Array]::CreateInstance((New-RsObject $Proxy $Type).GetType(), @($Items).Count)
    for ($i = 0; $i -lt @($Items).Count; $i++) { $array[$i] = @($Items)[$i] }
    return , $array
}

function Get-SoapMessage($ErrorRecord) {
    $exception = $ErrorRecord.Exception
    while ($exception.InnerException) { $exception = $exception.InnerException }
    $text = $exception.Message
    if ($exception.Detail) { $text += " | $($exception.Detail.OuterXml)" }
    return $text
}

# Members of proxy objects are read and written through .NET reflection, not the PowerShell
# adapter, and Get-RsShape logs the declared types: DataSource.Item is typed
# DataSourceDefinitionOrReference in ReportService2010
# (https://learn.microsoft.com/dotnet/api/reportservice2010.datasource.item); a run on the
# runner saw a String in its place (the cause is explained in scripts/Publish-Report.ps1).
# GetProperty(name, Type[0]) asks for the property without index parameters, so an indexer
# named Item cannot be picked by mistake.
function Get-RsMember($Object, [string]$Name) {
    $type = $Object.GetType()
    $property = $type.GetProperty($Name, [Type[]]@())
    if ($property) { return , $property.GetValue($Object, $null) }
    $field = $type.GetField($Name)
    if ($field) { return , $field.GetValue($Object) }
    throw "$($type.FullName) has no property or field $Name"
}

function Set-RsMember($Object, [string]$Name, $Value) {
    $type = $Object.GetType()
    $property = $type.GetProperty($Name, [Type[]]@())
    $field = if ($property) { $null } else { $type.GetField($Name) }
    if (-not $property -and -not $field) { throw "$($type.FullName) has no property or field $Name" }
    $target = if ($property) { $property.PropertyType } else { $field.FieldType }
    if ($Value -is [string] -and $target.IsEnum) { $Value = [Enum]::Parse($target, $Value) }
    if ($property) { $property.SetValue($Object, $Value, $null) } else { $field.SetValue($Object, $Value) }
}

function Get-RsShape($Object) {
    # Type, properties and fields with their declared types, for the log.
    if ($null -eq $Object) { return 'null' }
    $type = $Object.GetType()
    $members = @($type.GetProperties() | ForEach-Object { "$($_.Name):$($_.PropertyType.Name)" }) +
    @($type.GetFields() | ForEach-Object { "$($_.Name):$($_.FieldType.Name) (field)" })
    return "$($type.FullName) {$($members -join ', ')}"
}
