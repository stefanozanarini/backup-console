# Veeam Agent for Microsoft Windows, standalone. Le sessioni si leggono dall'Event Log:
# canale "Veeam Agent" (o Application con sorgente "Veeam Agent"): 110 = avvio del job,
# 190 = fine con l'esito, 191 = tentativo fallito che Veeam riproverà (retry).
# Da verificare sulle versioni installate: ID e formato dei messaggi possono variare.
$out = [ordered]@{ product = 'vaw'; version = $null; jobs = @(); repositories = @(); license = $null; errors = @() }

$out.version = Get-FileVersion (Join-Path $env:ProgramFiles 'Veeam\Endpoint Backup\Veeam.EndPoint.Service.exe')

$all = @()
foreach ($filter in @(
        @{ LogName = 'Veeam Agent'; Id = 110, 190, 191 },
        @{ LogName = 'Application'; ProviderName = 'Veeam Agent'; Id = 110, 190, 191 })) {
    try {
        $all = @(Get-WinEvent -FilterHashtable $filter -MaxEvents 1000 -ErrorAction Stop)
        if ($all.Count -gt 0) { break }
    } catch {}
}
$events = @($all | Where-Object { $_.Id -eq 190 -or $_.Id -eq 191 })
$starts = @($all | Where-Object { $_.Id -eq 110 } | Sort-Object TimeCreated -Descending)

function Get-JobName($ev) {
    if ($ev.Message -match "'([^']+)'") { return $Matches[1] }
    return 'Backup'
}

# Avvio corrispondente a una fine: il più recente dello stesso job, prima della fine, entro 24 ore.
function Find-Start($ev, $name) {
    foreach ($s in $starts) {
        if ($s.TimeCreated -gt $ev.TimeCreated) { continue }
        if ($s.TimeCreated -lt $ev.TimeCreated.AddHours(-24)) { return $null }
        if ((Get-JobName $s) -eq $name) { return $s }
    }
    return $null
}

# File di backup citati nel log locale del job (C:\ProgramData\Veeam\Endpoint\<job>\Job.<job>.Backup.log):
# il nome contiene data/ora di creazione e il tipo (.vbk = full, .vib = incrementale).
# Così non serve accedere alla destinazione, spesso una share con credenziali di Veeam.
$logRoot = Join-Path $env:ProgramData 'Veeam\Endpoint'
$storageCache = @{}
function Get-Storages($jobName) {
    if ($storageCache.ContainsKey($jobName)) { return $storageCache[$jobName] }
    $list = New-Object System.Collections.ArrayList
    $seenFiles = @{}
    $re = '(?i)' + [regex]::Escape($jobName) + '(\d{4}-\d{2}-\d{2}T\d{6})\.(vbk|vib)\b'
    try {
        $logs = @(Get-ChildItem $logRoot -Recurse -Filter 'Job.*.log' -ErrorAction Stop |
            Where-Object { $_.Name -like "*$jobName*" -and $_.LastWriteTime -ge $historyCut.AddDays(-1) })
        foreach ($m in @($logs | Select-String -Pattern $re -AllMatches)) {
            foreach ($x in $m.Matches) {
                $key = $x.Value.ToLowerInvariant()
                if ($seenFiles.ContainsKey($key)) { continue }
                $seenFiles[$key] = $true
                try {
                    $t = [datetime]::ParseExact($x.Groups[1].Value, 'yyyy-MM-ddTHHmmss', [Globalization.CultureInfo]::InvariantCulture)
                    [void]$list.Add(@{ time = $t; full = ($x.Groups[2].Value -ieq 'vbk') })
                } catch {}
            }
        }
    } catch {}
    $storageCache[$jobName] = $list
    return $list
}

# Full o incrementale: il file creato durante la sessione (tra avvio e fine).
function Get-Mode($jobName, $ev, $st) {
    $to = $ev.TimeCreated.AddMinutes(1)
    $from = $(if ($st) { $st.TimeCreated.AddMinutes(-1) } else { $ev.TimeCreated.AddHours(-12) })
    $found = @(Get-Storages $jobName | Where-Object { $_.time -ge $from -and $_.time -le $to })
    if ($found.Count -eq 0) { return $null }
    if (@($found | Where-Object { $_.full }).Count -gt 0) { return 'full' }
    return 'incremental'
}

function Get-Result($ev) {
    if ($ev.Id -eq 191) { return 'Failed' }
    if ($ev.Message -match '(?i)finished with (\w+)') {
        if ($Matches[1] -match '(?i)^error') { return 'Failed' }
        return $Matches[1]
    }
    switch ($ev.Level) { 2 { 'Failed' } 3 { 'Warning' } default { 'Success' } }
}

# Testo dell'evento su una riga: prima riga + "Job details: ..." (dove Veeam mette l'errore).
function Get-EventText($ev, [int]$max = 500) {
    $t = (($ev.Message -split "`r?`n") | Where-Object { $_.Trim() }) -join ' '
    if ($t.Length -gt $max) { $t = $t.Substring(0, $max) + '…' }
    return $t
}

# Avvisi/errori per il dettaglio del problema: la parte "Job details" dell'evento.
function Get-EventMessages($ev) {
    $status = $(if ((Get-Result $ev) -eq 'Warning') { 'warning' } else { 'error' })
    $lines = @(($ev.Message -split "`r?`n") | Select-Object -Skip 1 | ForEach-Object { ($_ -replace '^\s*Job details:\s*', '').Trim() } | Where-Object { $_ })
    if ($lines.Count -eq 0) { $lines = @(($ev.Message -split "`r?`n")[0]) }
    return @($lines | Select-Object -First 20 | ForEach-Object { [ordered]@{ object = $null; status = $status; text = $_ } })
}

$now = Get-Date
$historyCut = $now.AddDays(-10)
$recentCut = $now.AddHours(-24)
$byJob = [ordered]@{}
foreach ($ev in ($events | Sort-Object TimeCreated -Descending)) {
    $name = Get-JobName $ev
    if (-not $byJob.Contains($name)) {
        $byJob[$name] = [ordered]@{ last = $null; success = $null; history = New-Object System.Collections.ArrayList; recent = New-Object System.Collections.ArrayList }
    }
    $j = $byJob[$name]
    $retry = $ev.Id -eq 191
    # L'esito del job è quello dell'ultima sessione conclusa (190): un 191 sarà riprovato.
    if (-not $retry -and -not $j.last) { $j.last = $ev }
    # Riuscito anche con avvisi: il restore point è stato creato.
    if (-not $retry -and -not $j.success -and (Get-Result $ev) -match 'Success|Warning') { $j.success = $ev }
    if ($ev.TimeCreated -lt $historyCut) { continue }
    $st = Find-Start $ev $name
    $entry = [ordered]@{
        start = $(if ($st) { ConvertTo-IsoDate $st.TimeCreated } else { $null })
        end = ConvertTo-IsoDate $ev.TimeCreated
        mode = Get-Mode $name $ev $st
        result = Get-Result $ev
        state = 'Stopped'
        retry = $retry
        reason = Get-EventText $ev
    }
    if ($j.history.Count -lt 150) { [void]$j.history.Add($entry) }
    if ($ev.TimeCreated -ge $recentCut) { [void]$j.recent.Insert(0, $entry) }  # dal più vecchio
}

$jobs = New-Object System.Collections.ArrayList
foreach ($name in $byJob.Keys) {
    $e = $byJob[$name]
    $details = [ordered]@{
        history = $e.history
        sessions_24h = $e.recent
        retries_24h = @($e.recent | Where-Object { $_.retry }).Count
    }
    $st = $null
    if ($e.last) {
        $st = Find-Start $e.last $name
        $details.message = Get-EventText $e.last
        if ((Get-Result $e.last) -ne 'Success') { $details.messages = @(Get-EventMessages $e.last) }
    }
    [void]$jobs.Add([ordered]@{
        id = "vaw:$name"
        name = $name
        type = 'AgentBackup'
        enabled = $true
        last_result = $(if ($e.last) { Get-Result $e.last } else { 'Failed' })
        last_state = 'Stopped'
        last_start = $(if ($st) { ConvertTo-IsoDate $st.TimeCreated } else { $null })
        last_end = $(if ($e.last) { ConvertTo-IsoDate $e.last.TimeCreated } else { $null })
        last_success = $(if ($e.success) { ConvertTo-IsoDate $e.success.TimeCreated } else { $null })
        details = $details
    })
}
if ($events.Count -eq 0) { [void]$script:errors.Add('nessuna sessione trovata nell''Event Log') }
$out.jobs = $jobs
$out.errors = $script:errors
$out | ConvertTo-Json -Depth 8 -Compress
