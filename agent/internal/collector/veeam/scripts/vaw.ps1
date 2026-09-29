# Veeam Agent for Microsoft Windows, standalone. Le sessioni si leggono dall'Event Log:
# canale "Veeam Agent" (o Application con sorgente "Veeam Agent"), evento 190 = fine job.
# Da verificare sulle versioni installate: ID e formato dei messaggi possono variare.
$out = [ordered]@{ product = 'vaw'; version = $null; jobs = @(); repositories = @(); license = $null; errors = @() }

$out.version = Get-FileVersion (Join-Path $env:ProgramFiles 'Veeam\Endpoint Backup\Veeam.EndPoint.Service.exe')

# 110 = avvio del job (serve per inizio e durata), 190 = fine del job con l'esito.
$all = @()
foreach ($filter in @(
        @{ LogName = 'Veeam Agent'; Id = 110, 190 },
        @{ LogName = 'Application'; ProviderName = 'Veeam Agent'; Id = 110, 190 })) {
    try {
        $all = @(Get-WinEvent -FilterHashtable $filter -MaxEvents 1000 -ErrorAction Stop)
        if ($all.Count -gt 0) { break }
    } catch {}
}
$events = @($all | Where-Object { $_.Id -eq 190 })
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

# Full o incrementale, se Veeam lo scrive nel messaggio (dipende dalla versione).
function Get-Mode($evs) {
    foreach ($e in $evs) {
        if (-not $e) { continue }
        if ($e.Message -match '(?i)\b(active full|full backup|synthetic full)\b') { return 'full' }
        if ($e.Message -match '(?i)\bincremental\b') { return 'incremental' }
    }
    return $null
}

function Get-Result($ev) {
    if ($ev.Message -match '(?i)finished with (\w+)') { return $Matches[1] }
    switch ($ev.Level) { 2 { 'Failed' } 3 { 'Warning' } default { 'Success' } }
}

$historyCut = (Get-Date).AddDays(-10)
$byJob = [ordered]@{}
foreach ($ev in ($events | Sort-Object TimeCreated -Descending)) {
    $name = Get-JobName $ev
    if (-not $byJob.Contains($name)) {
        $byJob[$name] = [ordered]@{ last = $ev; success = $null; history = New-Object System.Collections.ArrayList }
    }
    # Riuscito anche con avvisi: il restore point è stato creato.
    if ($null -eq $byJob[$name].success -and (Get-Result $ev) -match 'Success|Warning') { $byJob[$name].success = $ev }
    if ($ev.TimeCreated -ge $historyCut -and $byJob[$name].history.Count -lt 150) {
        $st = Find-Start $ev $name
        [void]$byJob[$name].history.Add([ordered]@{
            start = $(if ($st) { ConvertTo-IsoDate $st.TimeCreated } else { $null })
            end = ConvertTo-IsoDate $ev.TimeCreated
            mode = Get-Mode @($ev, $st)
            result = Get-Result $ev
            reason = ($ev.Message -split "`n")[0]
        })
    }
}

$jobs = New-Object System.Collections.ArrayList
foreach ($name in $byJob.Keys) {
    $e = $byJob[$name]
    $st = Find-Start $e.last $name
    [void]$jobs.Add([ordered]@{
        id = "vaw:$name"
        name = $name
        type = 'AgentBackup'
        enabled = $true
        last_result = Get-Result $e.last
        last_state = 'Stopped'
        last_start = $(if ($st) { ConvertTo-IsoDate $st.TimeCreated } else { $null })
        last_end = ConvertTo-IsoDate $e.last.TimeCreated
        last_success = $(if ($e.success) { ConvertTo-IsoDate $e.success.TimeCreated } else { $null })
        details = [ordered]@{ message = ($e.last.Message -split "`n")[0]; history = $e.history }
    })
}
if ($events.Count -eq 0) { [void]$script:errors.Add('nessuna sessione trovata nell''Event Log') }
$out.jobs = $jobs
$out.errors = $script:errors
$out | ConvertTo-Json -Depth 8 -Compress
