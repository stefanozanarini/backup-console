# Funzioni comuni agli script Veeam. Compatibile con Windows PowerShell 5.1 e PowerShell 7.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$script:errors = New-Object System.Collections.ArrayList

function Add-CollectorError([string]$where, $err) {
    [void]$script:errors.Add(("{0}: {1}" -f $where, $err.Exception.Message))
}

# Data in ISO 8601 UTC; $null per valori vuoti o "mai" (MinValue, anni < 2000).
function ConvertTo-IsoDate($d) {
    if ($null -eq $d) { return $null }
    try {
        if ($d -is [string]) {
            if ([string]::IsNullOrWhiteSpace($d)) { return $null }
            $d = [datetime]::Parse($d, [Globalization.CultureInfo]::CurrentCulture)
        }
        $dt = [datetime]$d
        if ($dt.Year -lt 2000) { return $null }
        return $dt.ToUniversalTime().ToString('o')
    } catch { return $null }
}

# Primo valore non nullo tra le proprietà indicate: i nomi cambiano tra le versioni di Veeam.
function Get-Prop($obj, [string[]]$names) {
    if ($null -eq $obj) { return $null }
    foreach ($n in $names) {
        # Alcune proprietà degli oggetti Veeam leggono dal database e possono fallire.
        try {
            $p = $obj.PSObject.Properties[$n]
            if ($p -and $null -ne $p.Value) { return $p.Value }
        } catch {}
    }
    return $null
}

function Get-FileVersion([string]$path) {
    if (Test-Path $path) { return (Get-Item $path).VersionInfo.ProductVersion }
    return $null
}
