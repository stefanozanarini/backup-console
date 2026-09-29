# Analisi (solo lettura) dei backup eliminabili. Variabile impostata dall'agente:
#   $OlderThanDays   soglia in giorni sull'ultimo restore point
Import-VeeamModule
$cut = (Get-Date).AddDays(-$OlderThanDays)
$jobIndex = Get-JobIndex
$repoIndex = Get-RepoIndex

$items = New-Object System.Collections.ArrayList
$backups = @()
try { $backups = @(Get-VBRBackup) } catch { Add-CollectorError 'Get-VBRBackup' $_ }
foreach ($b in $backups) {
    try { [void]$items.Add((Get-BackupInfo $b $jobIndex $repoIndex $cut)) }
    catch { Add-CollectorError ("backup " + $b.Name) $_ }
}

[ordered]@{
    older_than_days = $OlderThanDays
    scanned_at = (Get-Date).ToUniversalTime().ToString('o')
    items = $items
    repositories = Get-RepoSpace
    errors = $script:errors
} | ConvertTo-Json -Depth 6 -Compress
