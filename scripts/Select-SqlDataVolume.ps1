<#
.SYNOPSIS
Chooses where SQL Server keeps its database files on the runner: the default folder on C: when
the sector size of C: is supported, otherwise a virtual disk with 4 KB (or 512-byte) sectors.

.DESCRIPTION
SQL Server supports disks whose physical sector size for atomicity is 512 bytes or 4 KB. Some
hosted runners have a system disk that reports 8 KB; setup then fails with 0x851A0019 ("Could not
find the Database Engine startup handle") and ERRORLOG error 5178 on master.mdf. Microsoft's fix
for such disks is a stornvme registry value that needs a reboot, which a hosted runner cannot do;
the other documented way is to keep the database files on a volume with a supported sector size.
Sources:
 https://learn.microsoft.com/troubleshoot/sql/database-engine/database-file-operations/troubleshoot-os-4kb-disk-sector-size
 https://learn.microsoft.com/troubleshoot/sql/azure-sql/sql-installation-fails-sector-size-error-azure-vm
 diskpart create vdisk and attach vdisk:
 https://learn.microsoft.com/windows-server/administration/windows-commands/create-vdisk
 A VHDX has 512-byte logical and 4 KB physical sectors by default, a VHD 512-byte sectors
 (https://learn.microsoft.com/windows-server/storage/disk-management/manage-virtual-hard-disks).
 The script measures the attached disk with fsutil and does not rely on these defaults:
 VHDX first, VHD if the VHDX reports more than 4 KB, an error if both do.

Outputs the sector information of C:, D: (if present) and the new volume, and the choice, as lines.
Appends SQL_DATA_ROOT (empty for the default folder) to the file named by -EnvFile.
#>
param(
    [string]$Folder = $env:RUNNER_TEMP,
    [int]$SizeMB = 30720,
    [Parameter(Mandatory = $true)][string]$EnvFile
)
$ErrorActionPreference = 'Stop'
$Supported = 4096
$script:Report = @()

function Get-SectorSize([string]$Drive) {
    $lines = fsutil fsinfo sectorinfo $Drive
    if ($LASTEXITCODE -ne 0) { throw "fsutil fsinfo sectorinfo $Drive exited with $LASTEXITCODE" }
    $values = @{}
    foreach ($line in $lines) {
        if ($line -match '^\s*(\w+)\s*:\s*(\d+)\s*$') { $values[$Matches[1]] = [int]$Matches[2] }
    }
    # The physical sector size for atomicity decides (Microsoft articles above); the file-system
    # effective value is taken too when present, and the larger of both is compared.
    $names = 'PhysicalBytesPerSectorForAtomicity', 'FileSystemEffectivePhysicalBytesPerSectorForAtomicity'
    $atomicity = @($names | Where-Object { $values.ContainsKey($_) } | ForEach-Object { $values[$_] })
    if ($atomicity.Count -eq 0) { throw "fsutil printed no PhysicalBytesPerSectorForAtomicity for ${Drive}: $($lines -join ' | ')" }
    $max = ($atomicity | Measure-Object -Maximum).Maximum
    $script:Report += "sectors ${Drive}: logical $($values['LogicalBytesPerSector']), physical for atomicity $($values[$names[0]]), file system effective $($values[$names[1]]) -> $max"
    return $max
}

function Invoke-Diskpart([string[]]$Commands) {
    $file = Join-Path $Folder "diskpart-$([guid]::NewGuid().ToString('N')).txt"
    Set-Content -Encoding ASCII -Path $file -Value $Commands
    $output = diskpart /s $file
    $code = $LASTEXITCODE
    $output | Where-Object { "$_".Trim() } | ForEach-Object { Write-Host "  diskpart: $_" }
    if ($code -ne 0) { throw "diskpart exited with $code ($($Commands -join '; '))" }
}

function New-DataVolume([string]$Extension) {
    $used = @(Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Name })
    $letter = @('S', 'T', 'R', 'Q', 'P') | Where-Object { $used -notcontains $_ } | Select-Object -First 1
    if (-not $letter) { throw 'No free drive letter for the SQL Server data volume.' }
    $file = Join-Path $Folder "sqldata.$Extension"
    Invoke-Diskpart @(
        "create vdisk file=`"$file`" maximum=$SizeMB type=expandable",
        "select vdisk file=`"$file`"",
        'attach vdisk',
        'online disk noerr',
        'attributes disk clear readonly noerr',
        'convert mbr noerr',
        'create partition primary',
        'format fs=ntfs quick label=SQLData',
        "assign letter=$letter"
    )
    for ($i = 0; $i -lt 30 -and -not (Test-Path -LiteralPath "${letter}:\"); $i++) { Start-Sleep -Seconds 1 }
    return @{ Letter = $letter; File = $file; Sector = (Get-SectorSize "${letter}:") }
}

function Remove-DataVolume($Volume) {
    Invoke-Diskpart @("select vdisk file=`"$($Volume.File)`"", 'detach vdisk')
    Remove-Item -LiteralPath $Volume.File -Force
}

$system = Get-SectorSize 'C:'
if (Test-Path -LiteralPath 'D:\') {
    # For the protocol only: the temporary disk of a runner may report 8 KB as well.
    Get-SectorSize 'D:' | Out-Null
}
$root = ''
if ($system -le $Supported) {
    $script:Report += "SQL Server data: default folder on C: ($system-byte sectors are supported)"
} else {
    $volume = $null
    foreach ($extension in 'vhdx', 'vhd') {
        $candidate = New-DataVolume $extension
        if ($candidate.Sector -le $Supported) {
            $volume = $candidate
            break
        }
        Write-Host "virtual disk $extension reports $($candidate.Sector)-byte sectors; trying the next format"
        Remove-DataVolume $candidate
    }
    if (-not $volume) {
        $script:Report
        throw "C: reports $system-byte sectors and no virtual disk with sectors of $Supported bytes or less could be made; SQL Server cannot keep its files on this runner."
    }
    $root = "$($volume.Letter):\SQLData"
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $script:Report += "SQL Server data: $root on a $(Split-Path -Leaf $volume.File) virtual disk with $($volume.Sector)-byte sectors (C: reports $system bytes)"
}
[IO.File]::AppendAllText($EnvFile, "SQL_DATA_ROOT=$root`n")
$script:Report
