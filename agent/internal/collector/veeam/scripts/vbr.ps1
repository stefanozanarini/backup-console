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

$recentCut = (Get-Date).AddHours(-24)
$maxMessages = 30

function Limit-Text($s, [int]$max = 1000) {
    $s = ([string]$s).Trim()
    if ($s.Length -gt $max) { return $s.Substring(0, $max) + '…' }
    return $s
}

# Sessione "core" di Veeam (CBackupSession), che espone task e log; i cmdlet più
# recenti restituiscono a volte oggetti diversi per i job di agent/copy.
function Get-CoreSession($session) {
    if (-not $session) { return $null }
    if ($session.PSObject.Methods['GetTaskSessions']) { return $session }
    try { return [Veeam.Backup.Core.CBackupSession]::Get([guid]$session.Id) } catch { return $null }
}

# Righe di log con avviso/errore (es. "Unable to truncate Microsoft SQL Server transaction logs...").
function Get-LogIssues($obj) {
    $list = @()
    try { $list = @($obj.Logger.GetLog().UpdatedRecords) } catch { return @() }
    return @($list | Where-Object { ([string]$_.Status) -match 'Warning|Failed|Error' } | ForEach-Object {
        [ordered]@{ status = $(if (([string]$_.Status) -match 'Warning') { 'warning' } else { 'error' }); text = Limit-Text $_.Title }
    })
}

# Avvisi ed errori di una sessione: motivo generale, stato delle singole VM/macchine e
# righe di log con avviso o errore.
function Get-SessionMessages($session) {
    $msgs = New-Object System.Collections.ArrayList
    $core = Get-CoreSession $session
    if (-not $core) { return ,$msgs }
    $reason = $null
    try { $reason = $core.Info.Reason } catch {}
    if (-not $reason) { $reason = Get-Prop $core @('Description') }
    if ($reason) { [void]$msgs.Add([ordered]@{ object = $null; status = 'info'; text = Limit-Text $reason }) }
    foreach ($m in @(Get-LogIssues $core)) { [void]$msgs.Add([ordered]@{ object = $null; status = $m.status; text = $m.text }) }
    $tasks = @()
    try { $tasks = @($core.GetTaskSessions()) } catch {}
    foreach ($t in $tasks) {
        if ($msgs.Count -ge $maxMessages) { break }
        $st = [string](Get-Prop $t @('Status'))
        if ($st -notmatch 'Warning|Failed|Error') { continue }
        $obj = [string](Get-Prop $t @('Name', 'ObjectName'))
        $tr = $null
        try { $tr = $t.Info.Reason } catch {}
        if ($tr) { [void]$msgs.Add([ordered]@{ object = $obj; status = $(if ($st -match 'Warning') { 'warning' } else { 'error' }); text = Limit-Text $tr }) }
        foreach ($m in @(Get-LogIssues $t)) {
            if ($msgs.Count -ge $maxMessages) { break }
            [void]$msgs.Add([ordered]@{ object = $obj; status = $m.status; text = $m.text })
        }
    }
    # Stesso testo ripetuto (motivo della sessione = riga di log): una volta sola.
    $uniq = New-Object System.Collections.ArrayList
    $keys = @{}
    foreach ($m in $msgs) {
        $k = "$($m.object)|$($m.text)"
        if ($keys.ContainsKey($k)) { continue }
        $keys[$k] = $true
        [void]$uniq.Add($m)
    }
    return ,$uniq
}

function Test-RetrySession($s) {
    $r = Get-Prop $s @('IsRetryMode', 'IsRetry')
    if ($null -ne $r) { return [bool]$r }
    return ([string](Get-Prop $s @('Name', 'JobName'))) -match '\(Retry'
}

# Sessioni delle ultime 24 ore: i retry indicano un problema anche se poi il job è
# andato a buon fine.
function Get-RecentSessions($jobId, $last) {
    $list = @()
    try { $list = @([Veeam.Backup.Core.CBackupSession]::GetByJob([guid]$jobId)) } catch {
        if ($last) { $list = @($last) }
    }
    $out = New-Object System.Collections.ArrayList
    foreach ($s in @($list | Where-Object { $_ -and $_.CreationTime -ge $recentCut } | Sort-Object CreationTime)) {
        $reason = $null
        try { $reason = $s.Info.Reason } catch {}
        [void]$out.Add([ordered]@{
            start = ConvertTo-IsoDate $s.CreationTime
            end = ConvertTo-IsoDate (Get-Prop $s @('EndTime'))
            result = [string](Get-Prop $s @('Result'))
            retry = [bool](Test-RetrySession $s)
            reason = $(if ($reason) { Limit-Text $reason 500 } else { $null })
        })
        if ($out.Count -ge 50) { break }
    }
    return ,$out
}

function Add-Job($id, $name, $type, $enabled, $session, $next, $details) {
    if ($seen.ContainsKey($id)) { return }
    $seen[$id] = $true
    $transferred = $null
    if ($session) {
        $progress = Get-Prop $session @('Progress')
        $t = Get-Prop $progress @('TransferedSize', 'TransferredSize')
        if ($null -ne $t) { $transferred = [int64]$t }
    }
    if (-not $details) { $details = [ordered]@{} }
    if ($session) {
        $res = [string](Get-Prop $session @('Result'))
        if ($res -match 'Warning|Failed') {
            try { $details.messages = Get-SessionMessages $session } catch { Add-CollectorError "messaggi $name" $_ }
        }
    }
    try {
        $recent = Get-RecentSessions $id $session
        $details.sessions_24h = $recent
        $details.retries_24h = @($recent | Where-Object { $_.retry }).Count
    } catch { Add-CollectorError "sessioni $name" $_ }
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
$out | ConvertTo-Json -Depth 8 -Compress
