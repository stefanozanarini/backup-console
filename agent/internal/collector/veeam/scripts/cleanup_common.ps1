# Funzioni comuni ad analisi e pulizia dei backup Veeam (da includere dopo common.ps1).
#
# Regola di sicurezza: un backup è eliminabile solo se il suo job è disabilitato o non
# esiste più (orfano) E il suo restore point più recente è più vecchio della soglia.
# Per i job attivi l'analisi riporta i restore point oltre la retention e le catene
# complete teoricamente eliminabili (beyond_retention), ma non li marca eliminabili.
# Se lo stato del job non si riesce a determinare, il backup non è eliminabile.

function Import-VeeamModule {
    try {
        Import-Module Veeam.Backup.PowerShell -DisableNameChecking -ErrorAction Stop | Out-Null
    } catch {
        try { Add-PSSnapin VeeamPSSnapin -ErrorAction Stop } catch {
            throw "modulo PowerShell Veeam non disponibile: $($_.Exception.Message)"
        }
    }
}

# id job → @{ enabled; job }, per tutti i tipi di job che i cmdlet sanno elencare.
function Get-JobIndex {
    $idx = @{}
    foreach ($cmd in @('Get-VBRJob', 'Get-VBRComputerBackupJob', 'Get-VBRBackupCopyJob')) {
        if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { continue }
        try {
            foreach ($j in @(& $cmd)) {
                $enabled = Get-Prop $j @('IsScheduleEnabled', 'JobEnabled', 'IsEnabled', 'Enabled')
                if ($null -eq $enabled) { $enabled = $true }
                $idx[[string]$j.Id] = @{ enabled = [bool]$enabled; job = $j }
            }
        } catch { Add-CollectorError $cmd $_ }
    }
    return $idx
}

# Retention del job: @{ type = 'points' | 'days'; value = N } oppure $null se non leggibile.
function Get-Retention($job) {
    if (-not $job) { return $null }
    $o = $null
    try { $o = $job.BackupStorageOptions } catch {}
    if (-not $o) { try { $o = $job.GetOptions().BackupStorageOptions } catch {} }
    if (-not $o) { try { $o = $job.Options.BackupStorageOptions } catch {} }
    if (-not $o) { return $null }
    $type = [string](Get-Prop $o @('RetentionType'))
    if ($type -match 'Day') {
        $v = Get-Prop $o @('RetainDaysToKeep', 'RetainDays')
        if ($v) { return @{ type = 'days'; value = [int]$v } }
    }
    $v = Get-Prop $o @('RetainCycles')
    if ($v) { return @{ type = 'points'; value = [int]$v } }
    return $null
}

function Test-FullStorage($s) {
    $full = Get-Prop $s @('IsFull', 'IsFullBackup')
    if ($null -ne $full) { return [bool]$full }
    $path = [string](Get-Prop $s @('FilePath', 'PartialPath'))
    return $path -match '\.vbk$'
}

# Per un job attivo: restore point oltre la retention e catene complete eliminabili in
# linea di principio (tutti i punti oltre la retention e una catena più recente esiste).
# Solo analisi: la cancellazione di queste catene non è ancora abilitata (serve una
# verifica sulla versione di Veeam installata).
function Get-BeyondRetention($backup, $job, $storages) {
    $ret = Get-Retention $job
    $out = [ordered]@{ retention = $ret; points = 0; size_bytes = [int64]0; chains = @(); note = $null }
    if (-not $ret) { $out.note = 'retention del job non leggibile'; return $out }
    $perVm = Get-Prop $backup @('IsPerVmBackupFiles', 'IsPerVmStorage', 'PerVmFiles')
    $reverse = @($storages | Where-Object { ([string](Get-Prop $_ @('FilePath', 'PartialPath'))) -match '\.vrb$' }).Count -gt 0

    $sorted = @($storages | Sort-Object { [datetime](Get-Prop $_ @('CreationTime')) })
    $n = $sorted.Count
    $beyond = @{}
    for ($i = 0; $i -lt $n; $i++) {
        $s = $sorted[$i]
        $t = [datetime](Get-Prop $s @('CreationTime'))
        $isBeyond = $false
        if ($ret.type -eq 'points') { $isBeyond = ($n - $i) -gt $ret.value }
        else { $isBeyond = $t -lt (Get-Date).AddDays(-$ret.value) }
        if ($isBeyond) {
            $beyond[$i] = $true
            $out.points++
            try { $out.size_bytes += [int64]$s.Stats.BackupSize } catch {}
        }
    }
    if ($out.points -eq 0) { return $out }
    if ($perVm) { $out.note = 'file per macchina: catene non raggruppabili in modo affidabile'; return $out }
    if ($reverse) { $out.note = 'catena reverse incremental: nessuna catena separabile'; return $out }

    # Catene forward incremental: ogni full apre una catena.
    $chains = New-Object System.Collections.ArrayList
    $cur = $null
    for ($i = 0; $i -lt $n; $i++) {
        $s = $sorted[$i]
        if ((Test-FullStorage $s) -or -not $cur) {
            $cur = [ordered]@{ start = $i; end = $i; size_bytes = [int64]0; all_beyond = $true }
            [void]$chains.Add($cur)
        }
        $cur.end = $i
        try { $cur.size_bytes += [int64]$s.Stats.BackupSize } catch {}
        if (-not $beyond.ContainsKey($i)) { $cur.all_beyond = $false }
    }
    $list = New-Object System.Collections.ArrayList
    for ($c = 0; $c -lt $chains.Count - 1; $c++) {  # l'ultima catena non è mai eliminabile
        $ch = $chains[$c]
        if (-not $ch.all_beyond) { continue }
        [void]$list.Add([ordered]@{
            first_point = ConvertTo-IsoDate (Get-Prop $sorted[$ch.start] @('CreationTime'))
            last_point = ConvertTo-IsoDate (Get-Prop $sorted[$ch.end] @('CreationTime'))
            points = $ch.end - $ch.start + 1
            size_bytes = $ch.size_bytes
        })
    }
    $out.chains = $list
    return $out
}

# id repository → nome (anche gli scale-out).
function Get-RepoIndex {
    $idx = @{}
    try { foreach ($r in @(Get-VBRBackupRepository)) { $idx[[string]$r.Id] = [string]$r.Name } } catch {}
    try { foreach ($so in @(Get-VBRBackupRepository -ScaleOut)) { $idx[[string]$so.Id] = [string]$so.Name } } catch {}
    return $idx
}

function Get-RepoSpace {
    $repos = New-Object System.Collections.ArrayList
    $list = @()
    try { $list = @(Get-VBRBackupRepository) } catch { Add-CollectorError 'Get-VBRBackupRepository' $_ }
    foreach ($r in $list) {
        $total = $null; $free = $null
        try {
            $c = $r.GetContainer()
            $total = [int64]$c.CachedTotalSpace.InBytes
            $free = [int64]$c.CachedFreeSpace.InBytes
        } catch {}
        [void]$repos.Add([ordered]@{ name = [string]$r.Name; total_bytes = $total; free_bytes = $free })
    }
    # La virgola impedisce a PowerShell di "srotolare" una lista con un solo elemento.
    return ,$repos
}

function Get-JobState($backup, $jobIndex) {
    $jobId = [string](Get-Prop $backup @('JobId'))
    if ($jobId -and $jobIndex.ContainsKey($jobId)) {
        if ($jobIndex[$jobId].enabled) { return 'active' } else { return 'disabled' }
    }
    # Tipi di job non elencati dai cmdlet: lo chiediamo a Veeam direttamente.
    $job = $null
    $failures = 0
    try { $job = $backup.GetJob() } catch { $failures++ }
    if (-not $job -and $jobId) {
        try { $job = [Veeam.Backup.Core.CBackupJob]::Find([guid]$jobId) } catch { $failures++ }
    }
    if ($job) {
        $enabled = Get-Prop $job @('IsScheduleEnabled', 'IsEnabled')
        if ($enabled -eq $false) { return 'disabled' } else { return 'active' }
    }
    # Nessuna risposta affidabile: meglio non proporre nulla.
    if ($failures -ge 2) { return 'unknown' }
    return 'orphan'
}

function Get-BackupInfo($backup, $jobIndex, $repoIndex, [datetime]$cut) {
    $size = [int64]0; $last = $null; $first = $null; $points = 0
    $storages = @()
    try { $storages = @($backup.GetAllStorages()) } catch {}
    foreach ($s in $storages) {
        $points++
        try { $size += [int64]$s.Stats.BackupSize } catch {}
        $t = Get-Prop $s @('CreationTime')
        if ($t) {
            $t = [datetime]$t
            if (-not $last -or $t -gt $last) { $last = $t }
            if (-not $first -or $t -lt $first) { $first = $t }
        }
    }
    $state = Get-JobState $backup $jobIndex
    $repoId = [string](Get-Prop $backup @('RepositoryId'))
    $repo = $null
    if ($repoId -and $repoIndex.ContainsKey($repoId)) { $repo = $repoIndex[$repoId] }
    else { try { $repo = [string]$backup.GetRepository().Name } catch {} }
    $candidate = ($state -eq 'orphan' -or $state -eq 'disabled') -and ($null -ne $last) -and ($last -lt $cut)
    $beyond = $null
    if ($state -eq 'active') {
        $jobId = [string](Get-Prop $backup @('JobId'))
        $job = $null
        if ($jobIndex.ContainsKey($jobId)) { $job = $jobIndex[$jobId].job }
        try { $beyond = Get-BeyondRetention $backup $job $storages } catch { $beyond = [ordered]@{ note = $_.Exception.Message } }
    }
    return [ordered]@{
        id = [string]$backup.Id
        name = [string]$backup.Name
        job = [string](Get-Prop $backup @('JobName'))
        state = $state
        repository = $repo
        restore_points = $points
        size_bytes = $size
        first_point = ConvertTo-IsoDate $first
        last_point = ConvertTo-IsoDate $last
        candidate = [bool]$candidate
        beyond_retention = $beyond
    }
}
