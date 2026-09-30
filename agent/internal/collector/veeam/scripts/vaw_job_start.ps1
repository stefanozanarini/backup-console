# Avvio del backup di Veeam Agent for Windows (standalone ha un solo job configurato):
# Veeam.Agent.Configurator.exe -backup lo fa partire in background.
$exe = Join-Path $env:ProgramFiles 'Veeam\Endpoint Backup\Veeam.Agent.Configurator.exe'
if (-not (Test-Path $exe)) { throw "Veeam Agent non trovato ($exe)" }
$p = Start-Process -FilePath $exe -ArgumentList '-backup' -Wait -PassThru -WindowStyle Hidden
if ($p.ExitCode -ne 0) { throw "Veeam.Agent.Configurator -backup: codice di uscita $($p.ExitCode)" }
[ordered]@{ job = $JobName; type = 'AgentBackup'; mode = 'start'; started = $true; note = $null } | ConvertTo-Json -Compress
