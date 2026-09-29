package veeam

// psBootstrap legge lo script da stdin (base64 dell'UTF-8) e lo esegue nello scope
// corrente, come se fosse stato passato direttamente.
const psBootstrap = `$__s = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd().Trim()))
. ([scriptblock]::Create($__s))`
