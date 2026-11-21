<#
.SYNOPSIS
Renders the published report on the server (URL access) for the five cases of the local
ReportViewer export, with the same parameter values, in XLSX and PDF.

.DESCRIPTION
The parameter values of every case are read from docs/runs/2026-11-19-render/replay.zip
(<case>.json, key Parameters). The server queries the data through its own data source.
Files: OutDir/<case>.xlsx and <case>.pdf (the names scripts/check_render.py expects);
on an error the answer of the server is saved as <case>.<ext>.error.html and the remaining
cases are still rendered. Source: URL access parameters rs:Command=Render, rs:Format and
rs:ParameterLanguage (https://learn.microsoft.com/sql/reporting-services/url-access-parameter-reference).
#>
param(
    [string]$Url = 'http://localhost/ReportServer',
    [string]$Report = '/Retail/SalesStock',
    [string]$Replay = 'docs/runs/2026-11-19-render/replay.zip',
    [string[]]$Cases = @('all', 'north', 'two_categories', 'partial', 'first_day'),
    [Parameter(Mandatory = $true)][string]$OutDir
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
$formats = [ordered]@{ EXCELOPENXML = 'xlsx'; PDF = 'pdf' }
$failed = @()
$log = Join-Path $OutDir 'render.txt'
$zip = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $Replay).Path)
try {
    foreach ($case in $Cases) {
        $entry = $zip.GetEntry("$case.json")
        if (-not $entry) { throw "replay.zip has no $case.json" }
        $reader = New-Object IO.StreamReader($entry.Open(), [Text.Encoding]::UTF8)
        $parameters = ($reader.ReadToEnd() | ConvertFrom-Json).Parameters
        $reader.Dispose()
        $query = foreach ($property in $parameters.PSObject.Properties) {
            foreach ($value in @($property.Value)) { "&$($property.Name)=$([Uri]::EscapeDataString("$value"))" }
        }
        foreach ($format in $formats.Keys) {
            $file = Join-Path $OutDir "$case.$($formats[$format])"
            $uri = "${Url}?${Report}&rs:Command=Render&rs:Format=$format&rs:ParameterLanguage=en-US$($query -join '')"
            $watch = [Diagnostics.Stopwatch]::StartNew()
            try {
                Invoke-WebRequest -Uri $uri -UseBasicParsing -UseDefaultCredentials -TimeoutSec 300 -OutFile $file
                $line = "$case $format ok: $((Get-Item -LiteralPath $file).Length) bytes in $([math]::Round($watch.Elapsed.TotalSeconds, 1)) s; $uri"
            } catch {
                $failed += "$case $format"
                $line = "$case $format FAILED: $($_.Exception.Message); $uri"
                $response = $_.Exception.Response
                if ($response) {
                    $body = (New-Object IO.StreamReader($response.GetResponseStream())).ReadToEnd()
                    Set-Content -Encoding UTF8 -Path "$file.error.html" -Value $body
                    $text = (($body -replace '<[^>]+>', ' ') -replace '\s+', ' ').Trim()
                    $line += "`r`n  server: $($text.Substring(0, [math]::Min(1500, $text.Length)))"
                }
            }
            Add-Content -Encoding UTF8 -Path $log -Value $line
            Write-Output $line
        }
    }
} finally {
    $zip.Dispose()
}
if ($failed.Count -gt 0) { throw "Rendering failed: $($failed -join ', ')" }
