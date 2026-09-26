# Veeam Backup & Replication v11 / v12 / v13 (Windows). Gira come LocalSystem sul server
# VBR: il gruppo Administrators locale è amministratore Veeam per impostazione predefinita.
$out = [ordered]@{ product = 'vbr'; version = $null; jobs = @(); repositories = @(); license = $null; errors = @() }

try {
    Import-Module Veeam.Backup.PowerShell -DisableNameChecking -ErrorAction Stop | Out-Null
} catch {
    try { Add-PSSnapin VeeamPSSnapin -ErrorAction Stop } catch {
        throw "modulo PowerShell Veeam non disponibile: $($_.Exception.Message)"
    }
}

$out.version = Get-FileVersion (Join-Path $env:ProgramFiles 'Veeam\Backup and Replication\Backup\Veeam.Backup.Service.exe')

$jobs = New-Object System.Collections.ArrayList
$seen = @{}

function Add-Job($id, $name, $type, $enabled, $session, $next, $details) {
    if ($seen.ContainsKey($id)) { return }
    $seen[$id] = $true
    $transferred = $null
    if ($session) {
        $progress = Get-Prop $session @('Progress')
        $t = Get-Prop $progress @('TransferedSize', 'TransferredSize')
        if ($null -ne $t) { $transferred = [int64]$t }
    }
    [void]$jobs.Add([ordered]@{
        id = [string]$id
        name = [string]$name
        type = [string]$type
        enabled = [bool]$enabled
        last_result = $(if ($session) { [string](Get-Prop $session @('Result')) } else { $null })
        last_state = $(if ($session) { [string](Get-Prop $session @('State')) } else { $null })
        last_start = $(if ($session) { ConvertTo-IsoDate (Get-Prop $session @('CreationTime', 'CreationTimeUTC')) } else { $null })
        last_end = $(if ($session) { ConvertTo-IsoDate (Get-Prop $session @('EndTime', 'EndTimeUTC')) } else { $null })
        transferred_bytes = $transferred
        next_run = $next
        details = $details
    })
}

# Job "classici": backup VM, replica, backup copy (fino a v12), file share...
try {
    foreach ($j in @(Get-VBRJob)) {
        $s = $null
        try { $s = $j.FindLastSession() } catch {}
        $next = $null
        try { $next = ConvertTo-IsoDate $j.ScheduleOptions.NextRun } catch {}
        $enabled = Get-Prop $j @('IsScheduleEnabled', 'IsEnabled')
        if ($null -eq $enabled) { $enabled = $true }
        Add-Job $j.Id $j.Name $j.JobType $enabled $s $next ([ordered]@{ description = [string]$j.Description })
    }
} catch { Add-CollectorError 'Get-VBRJob' $_ }

# Job di Veeam Agent gestiti da VBR (protection group).
try {
    if (Get-Command Get-VBRComputerBackupJob -ErrorAction SilentlyContinue) {
        foreach ($j in @(Get-VBRComputerBackupJob)) {
            $s = $null
            try {
                $s = @(Get-VBRComputerBackupJobSession -Name $j.Name) | Sort-Object CreationTime -Descending | Select-Object -First 1
            } catch {}
            $enabled = Get-Prop $j @('JobEnabled', 'IsEnabled', 'Enabled')
            if ($null -eq $enabled) { $enabled = $true }
            Add-Job $j.Id $j.Name 'ComputerBackup' $enabled $s $null ([ordered]@{ description = [string](Get-Prop $j @('Description')) })
        }
    }
} catch { Add-CollectorError 'Get-VBRComputerBackupJob' $_ }

# Backup copy job (v12+ hanno cmdlet dedicati).
try {
    if (Get-Command Get-VBRBackupCopyJob -ErrorAction SilentlyContinue) {
        foreach ($j in @(Get-VBRBackupCopyJob)) {
            if ($seen.ContainsKey([string]$j.Id)) { continue }
            $s = $null
            try { $s = [Veeam.Backup.Core.CBackupSession]::FindLastByJob($j.Id) } catch {}
            $enabled = Get-Prop $j @('JobEnabled', 'IsEnabled', 'Enabled')
            if ($null -eq $enabled) { $enabled = $true }
            Add-Job $j.Id $j.Name 'BackupCopy' $enabled $s $null ([ordered]@{})
        }
    }
} catch { Add-CollectorError 'Get-VBRBackupCopyJob' $_ }
$out.jobs = $jobs

# Repository (anche le estensioni degli scale-out).
$repos = New-Object System.Collections.ArrayList
function Add-Repo($r, [string]$name) {
    $total = $null; $free = $null
    try {
        $c = $r.GetContainer()
        $total = [int64]$c.CachedTotalSpace.InBytes
        $free = [int64]$c.CachedFreeSpace.InBytes
    } catch {}
    [void]$repos.Add([ordered]@{ name = $name; type = [string]$r.Type; total_bytes = $total; free_bytes = $free })
}
try { foreach ($r in @(Get-VBRBackupRepository)) { Add-Repo $r $r.Name } } catch { Add-CollectorError 'Get-VBRBackupRepository' $_ }
try {
    foreach ($so in @(Get-VBRBackupRepository -ScaleOut)) {
        foreach ($e in @($so.Extent)) { Add-Repo $e.Repository ("{0} / {1}" -f $so.Name, $e.Repository.Name) }
    }
} catch { Add-CollectorError 'Get-VBRBackupRepository -ScaleOut' $_ }
$out.repositories = $repos

try {
    $l = Get-VBRInstalledLicense
    $out.license = [ordered]@{
        edition = [string]$l.Edition
        status = [string]$l.Status
        expiration = ConvertTo-IsoDate $l.ExpirationDate
        support_expiration = ConvertTo-IsoDate $l.SupportExpirationDate
    }
} catch { Add-CollectorError 'Get-VBRInstalledLicense' $_ }

$out.errors = $script:errors
$out | ConvertTo-Json -Depth 6 -Compress
