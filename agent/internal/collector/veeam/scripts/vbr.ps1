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
$historyCut = (Get-Date).AddDays(-10)
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

# Storico delle sessioni di un job, dalla più recente. Prima l'API interna per job; se
# non esiste in questa versione, una sola lettura di tutte le sessioni per l'intero report.
$script:allSessions = $null
function Get-JobSessions($jobId, $known) {
    $list = $null
    try { $list = @([Veeam.Backup.Core.CBackupSession]::GetByJob([guid]$jobId)) } catch {}
    if ($null -eq $list) {
        if ($null -eq $script:allSessions) {
            $script:allSessions = @{}
            try {
                foreach ($s in @(Get-VBRBackupSession)) {
                    $k = [string]$s.JobId
                    if (-not $script:allSessions.ContainsKey($k)) { $script:allSessions[$k] = New-Object System.Collections.ArrayList }
                    [void]$script:allSessions[$k].Add($s)
                }
            } catch { Add-CollectorError 'Get-VBRBackupSession' $_ }
        }
        $list = @()
        if ($script:allSessions.ContainsKey([string]$jobId)) { $list = @($script:allSessions[[string]$jobId]) }
    }
    $ids = @{}
    foreach ($s in $list) { if ($s) { $ids[[string]$s.Id] = $true } }
    foreach ($s in @($known)) { if ($s -and -not $ids.ContainsKey([string]$s.Id)) { $list += $s } }
    return @($list | Where-Object { $_ } | Sort-Object { [datetime](Get-Prop $_ @('CreationTime')) } -Descending)
}

function Get-Result($s) { return [string](Get-Prop $s @('Result')) }

# Stati in cui la sessione è ancora in corso (Working, Starting, Stopping, Postprocessing...).
function Test-Running($s) {
    $st = [string](Get-Prop $s @('State'))
    return $st -and $st -notmatch '^(Stopped|Idle|None)$'
}

function Get-Transferred($s) {
    if (-not $s) { return $null }
    $t = Get-Prop (Get-Prop $s @('Progress')) @('TransferedSize', 'TransferredSize')
    if ($null -ne $t) { return [int64]$t }
    return $null
}

# Sessioni delle ultime 24 ore: i retry indicano un problema anche se poi il job è
# andato a buon fine.
function Get-RecentSessions($sessions) {
    $out = New-Object System.Collections.ArrayList
    foreach ($s in @($sessions | Where-Object { [datetime](Get-Prop $_ @('CreationTime')) -ge $recentCut } | Sort-Object { [datetime](Get-Prop $_ @('CreationTime')) })) {
        $reason = $null
        try { $reason = $s.Info.Reason } catch {}
        [void]$out.Add([ordered]@{
            start = ConvertTo-IsoDate (Get-Prop $s @('CreationTime'))
            end = ConvertTo-IsoDate (Get-Prop $s @('EndTime'))
            result = Get-Result $s
            state = [string](Get-Prop $s @('State'))
            retry = [bool](Test-RetrySession $s)
            reason = $(if ($reason) { Limit-Text $reason 500 } else { $null })
        })
        if ($out.Count -ge 50) { break }
    }
    return ,$out
}

# Storico degli ultimi 10 giorni per la console (al più 150 sessioni, dalla più recente).
function Get-History($sessions) {
    $out = New-Object System.Collections.ArrayList
    foreach ($s in $sessions) {
        $t = [datetime](Get-Prop $s @('CreationTime'))
        if ($t -lt $historyCut) { break }  # $sessions è ordinato dalla più recente
        $reason = $null
        try { $reason = $s.Info.Reason } catch {}
        [void]$out.Add([ordered]@{
            start = ConvertTo-IsoDate $t
            end = ConvertTo-IsoDate (Get-Prop $s @('EndTime'))
            result = Get-Result $s
            state = [string](Get-Prop $s @('State'))
            retry = [bool](Test-RetrySession $s)
            transferred_bytes = Get-Transferred $s
            reason = $(if ($reason) { Limit-Text $reason 300 } else { $null })
        })
        if ($out.Count -ge 150) { break }
    }
    return ,$out
}

# Un job è descritto da tre sessioni diverse:
#   - l'ultima avviata (può essere ancora in corso: esito "None" finché non termina);
#   - l'ultima terminata, che dà l'esito, la fine e i messaggi;
#   - l'ultima riuscita (anche con avvisi: il restore point è stato creato).
function Add-Job($id, $name, $type, $enabled, $session, $next, $details) {
    if ($seen.ContainsKey($id)) { return }
    $seen[$id] = $true
    if (-not $details) { $details = [ordered]@{} }
    $sessions = @()
    try { $sessions = @(Get-JobSessions $id $session) } catch { Add-CollectorError "sessioni $name" $_; if ($session) { $sessions = @($session) } }
    $latest = $null; $done = $null; $ok = $null
    if ($sessions.Count -gt 0) { $latest = $sessions[0] }
    foreach ($s in $sessions) {
        $r = Get-Result $s
        if (-not $done -and $r -match 'Success|Warning|Failed') { $done = $s }
        if (-not $ok -and $r -match 'Success|Warning') { $ok = $s }
        if ($done -and $ok) { break }
    }
    $running = $latest -and (Test-Running $latest)
    $details.running = [bool]$running
    if ($running) {
        $details.running_since = ConvertTo-IsoDate (Get-Prop $latest @('CreationTime'))
        $pct = Get-Prop (Get-Prop $latest @('Progress')) @('Percents', 'Percent')
        if ($null -ne $pct) { $details.progress_pct = [int]$pct }
    }
    if ($done -and (Get-Result $done) -match 'Warning|Failed') {
        try { $details.messages = Get-SessionMessages $done } catch { Add-CollectorError "messaggi $name" $_ }
    }
    $recent = Get-RecentSessions $sessions
    $details.sessions_24h = $recent
    $details.history = Get-History $sessions
    $details.retries_24h = @($recent | Where-Object { $_.retry }).Count
    [void]$jobs.Add([ordered]@{
        id = [string]$id
        name = [string]$name
        type = [string]$type
        enabled = [bool]$enabled
        last_result = $(if ($done) { Get-Result $done } else { $null })
        last_state = $(if ($latest) { [string](Get-Prop $latest @('State')) } else { $null })
        last_start = $(if ($latest) { ConvertTo-IsoDate (Get-Prop $latest @('CreationTime', 'CreationTimeUTC')) } else { $null })
        last_end = $(if ($done) { ConvertTo-IsoDate (Get-Prop $done @('EndTime', 'EndTimeUTC')) } else { $null })
        last_success = $(if ($ok) { ConvertTo-IsoDate (Get-Prop $ok @('EndTime', 'EndTimeUTC')) } else { $null })
        transferred_bytes = Get-Transferred $done
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
                $s = @(Get-VBRComputerBackupJobSession -Name $j.Name)
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
