# Firma digitale delle app su QTS: se QTS la impone, il controllo periodico
# (qpkg_cli --check_license, all'apertura di App Center) disattiva il nostro QPKG
# perché non è firmato da QNAP. Output grezzo: lo interpreta la console.
CONF=/etc/config/qpkg.conf
echo "now=$(date '+%Y-%m-%d %H:%M:%S')"
echo "force_enable_code_signing=$(/sbin/getcfg 'QPKG Management' force_enable_code_signing -f /etc/config/uLinux.conf)"
echo "enable=$(/sbin/getcfg BackupConsoleAgent Enable -f $CONF)"
echo "qpkg_version=$(/sbin/getcfg BackupConsoleAgent Version -f $CONF)"
echo "--log"
# Verdetti di QTS sul nostro pacchetto: controllo della firma ed eventuale disattivazione.
for f in /var/log/log.qpkg.1 /var/log/log.qpkg; do
	[ -f "$f" ] && grep -E 'qpkgd_check_code_signing.*"BackupConsoleAgent"|\[CLI\] disable \(BackupConsoleAgent\)' "$f"
done | tail -n 50
echo "--conf"
# Possibili altre chiavi dell'impostazione (per capire dove la salva la GUI).
grep -iE '^[^#]*(code_sign|unsigned|allow_.*sign)' /etc/config/uLinux.conf $CONF 2>/dev/null | head -n 20
exit 0
