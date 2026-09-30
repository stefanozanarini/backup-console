# Uranium Backup (NanoSystems). Legge solo i riepiloghi dei log, non le configurazioni
# dei backup set (.bks, #Uranium.ini), che possono contenere credenziali.
#   Log\LogInfo.ini   <set>.bks\Html-LogNNN.zip=<esito>÷<hh : mm : ss>÷<byte copiati>
#   Log\LogInfo2.ini  <set>.bks\Html-LogNNN.zip=<nome del backup set>
#   Log\<set>.bks\Html-LogNNN.zip  report di ogni esecuzione (la data del file = fine)
#   Log\<set>.bks\Html-Log.htm     report dell'ultima esecuzione, elemento per elemento
$out = [ordered]@{ product = 'uranium'; version = $null; jobs = @(); repositories = @(); license = $null; errors = @(); service = $null }

$root = Join-Path $env:ProgramData 'UraniumBackup'
$logDir = Join-Path $root 'Log'
foreach ($p in @(${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
    if (-not $p) { continue }
    $exe = Join-Path $p 'Uranium Backup\Uranium.exe'
    if (Test-Path $exe) { $out.version = Get-FileVersion $exe; break }
}

# Servizio: senza, i backup pianificati partono solo con un utente collegato e Uranium aperto.
$svc = $null
try { $svc = @(Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'uranium' -or $_.DisplayName -match 'uranium' }) | Select-Object -First 1 } catch { Add-CollectorError 'servizio Uranium' $_ }
$out.service = [ordered]@{ installed = [bool]$svc; status = $(if ($svc) { [string]$svc.Status } else { $null }) }

# Gli .ini sono in ANSI (1252) o, nelle versioni recenti, con BOM: lettura con rilevamento.
function Read-Ini([string]$path) {
    $map = [ordered]@{}
    if (-not (Test-Path $path)) { return $map }
    $sr = New-Object System.IO.StreamReader($path, [Text.Encoding]::GetEncoding(1252), $true)
    try { $text = $sr.ReadToEnd() } finally { $sr.Close() }
    foreach ($line in ($text -split "`r?`n")) {
        $i = $line.IndexOf('=')
        if ($i -le 0 -or $line.StartsWith('[')) { continue }
        $map[$line.Substring(0, $i).Trim()] = $line.Substring($i + 1).Trim()
    }
    return $map
}

function Get-Result([string]$esito) {
    switch -Regex ($esito) {
        'success|riusc|completat' { if ($esito -match 'avvis|warning') { return 'Warning' } else { return 'Success' } }
        'interrott|abort|nessun|no backup|annullat|cancel' { return 'Warning' }
        'error|fail|fallit' { return 'Failed' }
    }
    return 'None'
}

function Get-Seconds([string]$d) {
    $n = @($d -split '[^\d]+' | Where-Object { $_ -ne '' } | ForEach-Object { [int]$_ })
    if ($n.Count -ge 3) { return $n[0] * 3600 + $n[1] * 60 + $n[2] }
    return $null
}

# Report dell'ultima esecuzione di un set: esito, avvisi ed elementi non riusciti.
function Read-LastReport([string]$path) {
    $r = [ordered]@{ esito = $null; warnings = 0; start = $null; end = $null; manual = $null; messages = @() }
    if (-not (Test-Path $path)) { return $r }
    $sr = New-Object System.IO.StreamReader($path, [Text.Encoding]::UTF8, $true)
    try { $h = $sr.ReadToEnd() } finally { $sr.Close() }
    $t = $h -replace '(?is)<(script|style).*?</\1>', '' -replace '(?i)<br\s*/?>|</(p|div|tr|li|h\d)>', "`n" -replace '<[^>]+>', ' ' -replace '&nbsp;', ' ' -replace '[ \t]+', ' '
    $lines = @($t -split "`n" | ForEach-Object { [Net.WebUtility]::HtmlDecode($_).Trim() } | Where-Object { $_ })
    $msgs = New-Object System.Collections.ArrayList
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $l = $lines[$i]
        if ($l -match '^(Esito|Result)\s*:\s*(.+)$') { $r.esito = $Matches[2] }
        elseif ($l -match '^(Numero avvisi|Warnings)\s*:\s*(\d+)') { $r.warnings = [int]$Matches[2] }
        elseif ($l -match '^Tipo esecuzione\s*:\s*(.+)$') { $r.manual = $Matches[1] -match 'manual' }
        elseif ($l -match '^(ATTENZIONE|AVVISO|WARNING|ERRORE|ERROR)$' -and $msgs.Count -lt 30) {
            $status = $(if ($Matches[1] -match 'ERR') { 'error' } else { 'warning' })
            $obj = $null; $det = $null
            for ($j = $i + 1; $j -lt [Math]::Min($i + 6, $lines.Count); $j++) {
                if ($lines[$j] -match '^(Elemento|Item)\s*:\s*(.+)$') { $obj = $Matches[2] }
                elseif ($lines[$j] -match '^(Dettagli|Details)\s*:\s*(.+)$') { $det = $Matches[2] }
                elseif ($lines[$j] -match '^(OK|ATTENZIONE|AVVISO|WARNING|ERRORE|ERROR)$') { break }
            }
            if ($det) { [void]$msgs.Add([ordered]@{ object = $obj; status = $status; text = $det }) }
        }
    }
    $r.messages = $msgs
    return $r
}

$info = Read-Ini (Join-Path $logDir 'LogInfo.ini')
$names = Read-Ini (Join-Path $logDir 'LogInfo2.ini')

# Esecuzioni per set, con la data dal file del report (quelli cancellati da Uranium non hanno data).
$bySet = [ordered]@{}
$sets = @(Get-ChildItem (Join-Path $root 'BkpSet') -Filter *.bks -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
foreach ($s in $sets) { $bySet[$s] = New-Object System.Collections.ArrayList }
foreach ($key in $info.Keys) {
    if ($key -notmatch '^(?<set>[^\\]+\.bks)\\(?<file>Html-Log(?<n>\d+)\.zip)$') { continue }
    $set = $Matches['set']; $file = $Matches['file']; $n = [int]$Matches['n']
    $f = Get-Item (Join-Path $logDir "$set\$file") -ErrorAction SilentlyContinue
    if (-not $f) { continue }
    $parts = $info[$key] -split '÷|Ã·|÷'
    $esito = $parts[0].Trim()
    $secs = $(if ($parts.Count -gt 1) { Get-Seconds $parts[1] } else { $null })
    $bytes = $null
    if ($parts.Count -gt 2 -and $parts[2].Trim() -match '^\d+$') { $bytes = [int64]$parts[2].Trim() }
    $end = $f.LastWriteTime
    if (-not $bySet.Contains($set)) { $bySet[$set] = New-Object System.Collections.ArrayList }
    [void]$bySet[$set].Add([ordered]@{
        n = $n; name = $names[$key]; esito = $esito; result = Get-Result $esito; end = $end
        start = $(if ($null -ne $secs) { $end.AddSeconds(-$secs) } else { $null }); bytes = $bytes
    })
}

$jobs = New-Object System.Collections.ArrayList
foreach ($set in $bySet.Keys) {
    $runs = @($bySet[$set] | Sort-Object { $_.n } -Descending)
    $name = ($runs | Where-Object { $_.name } | Select-Object -First 1).name
    if (-not $name) { $name = [IO.Path]::GetFileNameWithoutExtension($set) }
    $last = $runs | Select-Object -First 1
    $ok = $runs | Where-Object { $_.result -in 'Success', 'Warning' } | Select-Object -First 1
    $details = [ordered]@{ set_file = $set; service_installed = $out.service.installed }

    # Ultima esecuzione: dal report HTML anche gli avvisi (esito "riuscito" con avvisi = Warning).
    $result = $(if ($last) { $last.result } else { $null })
    try {
        $rep = Read-LastReport (Join-Path $logDir "$set\Html-Log.htm")
        if ($last -and $rep.esito -and $rep.esito -eq $last.esito) {
            if ($result -eq 'Success' -and $rep.warnings -gt 0) { $result = 'Warning' }
            if ($rep.messages.Count -gt 0) { $details.messages = $rep.messages }
            if ($null -ne $rep.manual) { $details.last_manual = $rep.manual }
        }
    } catch { Add-CollectorError "report $set" $_ }
    if ($last -and $result -ne 'Success' -and -not $details.Contains('messages')) {
        $details.messages = @([ordered]@{ object = $null; status = $(if ($result -eq 'Failed') { 'error' } else { 'warning' }); text = $last.esito })
    }

    # Intervallo abituale tra le esecuzioni (mediana): la console adatta la soglia
    # "nessun backup riuscito" ai job settimanali.
    $ends = @($runs | Select-Object -First 11 | ForEach-Object { $_.end } | Sort-Object)
    if ($ends.Count -ge 3) {
        $gaps = @(for ($i = 1; $i -lt $ends.Count; $i++) { ($ends[$i] - $ends[$i - 1]).TotalHours }) | Sort-Object
        $details.expected_interval_h = [Math]::Round($gaps[[int][Math]::Floor($gaps.Count / 2)], 1)
    }

    $history = New-Object System.Collections.ArrayList
    foreach ($r in ($runs | Select-Object -First 60)) {
        [void]$history.Add([ordered]@{
            id = "$set\$($r.n)"
            start = ConvertTo-IsoDate $r.start
            end = ConvertTo-IsoDate $r.end
            result = $(if ($r -eq $last) { $result } else { $r.result })
            state = 'Stopped'
            retry = $false
            backup_bytes = $r.bytes
            reason = $r.esito
        })
    }
    $details.history = $history

    [void]$jobs.Add([ordered]@{
        id = "uranium:$set"
        name = [string]$name
        type = 'UraniumBackupSet'
        enabled = $true
        last_result = $result
        last_state = 'Stopped'
        last_start = $(if ($last) { ConvertTo-IsoDate $last.start } else { $null })
        last_end = $(if ($last) { ConvertTo-IsoDate $last.end } else { $null })
        last_success = $(if ($ok) { ConvertTo-IsoDate $ok.end } else { $null })
        transferred_bytes = $(if ($last) { $last.bytes } else { $null })
        details = $details
    })
}
if (-not (Test-Path $logDir)) { [void]$script:errors.Add("cartella dei log non trovata: $logDir") }
$out.jobs = $jobs
$out.errors = $script:errors
$out | ConvertTo-Json -Depth 8 -Compress
