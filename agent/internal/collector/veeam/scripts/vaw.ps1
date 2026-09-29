# Veeam Agent for Microsoft Windows, standalone. Le sessioni si leggono dall'Event Log:
# canale "Veeam Agent" (o Application con sorgente "Veeam Agent"), evento 190 = fine job.
# Da verificare sulle versioni installate: ID e formato dei messaggi possono variare.
$out = [ordered]@{ product = 'vaw'; version = $null; jobs = @(); repositories = @(); license = $null; errors = @() }

$out.version = Get-FileVersion (Join-Path $env:ProgramFiles 'Veeam\Endpoint Backup\Veeam.EndPoint.Service.exe')

$events = @()
foreach ($filter in @(
        @{ LogName = 'Veeam Agent'; Id = 190 },
        @{ LogName = 'Application'; ProviderName = 'Veeam Agent'; Id = 190 })) {
    try {
        $events = @(Get-WinEvent -FilterHashtable $filter -MaxEvents 500 -ErrorAction Stop)
        if ($events.Count -gt 0) { break }
    } catch {}
}

function Get-Result($ev) {
    if ($ev.Message -match '(?i)finished with (\w+)') { return $Matches[1] }
    switch ($ev.Level) { 2 { 'Failed' } 3 { 'Warning' } default { 'Success' } }
}

$historyCut = (Get-Date).AddDays(-10)
$byJob = [ordered]@{}
foreach ($ev in ($events | Sort-Object TimeCreated -Descending)) {
    $name = 'Backup'
    if ($ev.Message -match "'([^']+)'") { $name = $Matches[1] }
    if (-not $byJob.Contains($name)) {
        $byJob[$name] = [ordered]@{ last = $ev; success = $null; history = New-Object System.Collections.ArrayList }
    }
    # Riuscito anche con avvisi: il restore point è stato creato.
    if ($null -eq $byJob[$name].success -and (Get-Result $ev) -match 'Success|Warning') { $byJob[$name].success = $ev }
    if ($ev.TimeCreated -ge $historyCut -and $byJob[$name].history.Count -lt 150) {
        [void]$byJob[$name].history.Add([ordered]@{
            start = $null
            end = ConvertTo-IsoDate $ev.TimeCreated
            result = Get-Result $ev
            reason = ($ev.Message -split "`n")[0]
        })
    }
}

$jobs = New-Object System.Collections.ArrayList
foreach ($name in $byJob.Keys) {
    $e = $byJob[$name]
    [void]$jobs.Add([ordered]@{
        id = "vaw:$name"
        name = $name
        type = 'AgentBackup'
        enabled = $true
        last_result = Get-Result $e.last
        last_state = 'Stopped'
        last_end = ConvertTo-IsoDate $e.last.TimeCreated
        last_success = $(if ($e.success) { ConvertTo-IsoDate $e.success.TimeCreated } else { $null })
        details = [ordered]@{ message = ($e.last.Message -split "`n")[0]; history = $e.history }
    })
}
if ($events.Count -eq 0) { [void]$script:errors.Add('nessuna sessione trovata nell''Event Log') }
$out.jobs = $jobs
$out.errors = $script:errors
$out | ConvertTo-Json -Depth 8 -Compress
