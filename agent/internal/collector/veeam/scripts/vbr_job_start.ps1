# Avvio di un job Veeam B&R dalla console. Variabili impostate dall'agente:
#   $JobId   id (GUID) del job
#   $Mode    'retry' = solo gli oggetti falliti dell'ultima sessione (come "Retry" in Veeam),
#            'start' = esecuzione completa del job
# Il job parte in background (-RunAsync): l'esito arriva con i report successivi.
try {
    Import-Module Veeam.Backup.PowerShell -DisableNameChecking -ErrorAction Stop | Out-Null
} catch {
    try { Add-PSSnapin VeeamPSSnapin -ErrorAction Stop } catch {
        throw "modulo PowerShell Veeam non disponibile: $($_.Exception.Message)"
    }
}

$result = [ordered]@{ job = $null; type = $null; mode = $Mode; started = $false; note = $null }

$job = @(Get-VBRJob | Where-Object { [string]$_.Id -eq $JobId }) | Select-Object -First 1
if ($job) {
    $result.job = [string]$job.Name; $result.type = [string]$job.JobType
    $running = $false
    try { $running = [bool]$job.IsRunning } catch {}
    if ($running) { throw "il job $($job.Name) è già in esecuzione" }
    if ($Mode -eq 'retry') {
        try {
            Start-VBRJob -Job $job -RetryBackup -RunAsync -ErrorAction Stop | Out-Null
        } catch {
            # Tipi di job senza retry (o nessun oggetto fallito): esecuzione normale.
            $result.note = "retry non disponibile ($($_.Exception.Message)): avviata l'esecuzione completa"
            $result.mode = 'start'
            Start-VBRJob -Job $job -RunAsync -ErrorAction Stop | Out-Null
        }
    } else {
        Start-VBRJob -Job $job -RunAsync -ErrorAction Stop | Out-Null
    }
    $result.started = $true
} elseif (Get-Command Get-VBRComputerBackupJob -ErrorAction SilentlyContinue) {
    $cjob = @(Get-VBRComputerBackupJob | Where-Object { [string]$_.Id -eq $JobId }) | Select-Object -First 1
    if ($cjob) {
        $result.job = [string]$cjob.Name; $result.type = 'ComputerBackup'; $result.mode = 'start'
        Start-VBRComputerBackupJob -Job $cjob -RunAsync -ErrorAction Stop | Out-Null
        $result.started = $true
    }
}
if (-not $result.started) {
    if (Get-Command Get-VBRBackupCopyJob -ErrorAction SilentlyContinue) {
        $copy = @(Get-VBRBackupCopyJob | Where-Object { [string]$_.Id -eq $JobId }) | Select-Object -First 1
        if ($copy) { throw "il job di copia $($copy.Name) gira in modo continuo o secondo la sua pianificazione: non si avvia da qui" }
    }
    throw "job $JobId non trovato su questo server"
}
$result | ConvertTo-Json -Compress
