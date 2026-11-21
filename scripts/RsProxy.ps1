<#
.SYNOPSIS
Dot-sourced helpers: a SOAP proxy of ReportService2010 with the Windows credentials of the
current process (New-WebServiceProxy, Windows PowerShell 5.1) and typed arrays for its calls.
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
