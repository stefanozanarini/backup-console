# Eliminazione, tramite Veeam, dei backup scelti in console. Variabili impostate
# dall'agente:
#   $OlderThanDays   stessa soglia usata nell'analisi
#   $BackupIds       id (GUID) dei backup da eliminare
# Ogni backup viene ricontrollato qui prima di cancellarlo: se nel frattempo il job è
# tornato attivo o ci sono restore point recenti, viene saltato.
Import-VeeamModule
$cut = (Get-Date).AddDays(-$OlderThanDays)
$jobIndex = Get-JobIndex
$repoIndex = Get-RepoIndex

$byId = @{}
try { foreach ($b in @(Get-VBRBackup)) { $byId[[string]$b.Id] = $b } } catch { Add-CollectorError 'Get-VBRBackup' $_ }

$results = New-Object System.Collections.ArrayList
foreach ($id in $BackupIds) {
    $b = $byId[$id]
    if (-not $b) {
        [void]$results.Add([ordered]@{ id = $id; status = 'missing'; message = 'backup non più presente' })
        continue
    }
    $info = Get-BackupInfo $b $jobIndex $repoIndex $cut
    if (-not $info.candidate) {
        [void]$results.Add([ordered]@{ id = $id; name = $info.name; status = 'skipped'
            message = "non più eliminabile (stato job: $($info.state), ultimo punto: $($info.last_point))" })
        continue
    }
    try {
        Remove-VBRBackup -Backup $b -FromDisk -Confirm:$false -ErrorAction Stop | Out-Null
        [void]$results.Add([ordered]@{ id = $id; name = $info.name; status = 'deleted'; size_bytes = $info.size_bytes })
    } catch {
        [void]$results.Add([ordered]@{ id = $id; name = $info.name; status = 'error'; message = $_.Exception.Message })
    }
}

[ordered]@{
    results = $results
    repositories = Get-RepoSpace
    errors = $script:errors
} | ConvertTo-Json -Depth 6 -Compress
