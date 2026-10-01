# Ricerca di altri NAS (QNAP, Synology) nelle sottoreti del dispositivo, una volta al
# giorno. Per ogni indirizzo una richiesta HTTP alle pagine che i NAS espongono senza
# login; la console confronta il risultato con i dispositivi del tenant.
# Output grezzo: lo interpreta la console.
PAR=32

echo "now=$(date '+%Y-%m-%d %H:%M:%S')"

# Interfacce IPv4 private, escluse quelle virtuali di container e VPN.
nets=$(ip -o -4 addr show 2>/dev/null | awk '
	{ dev = $2; sub(/:$/, "", dev); split($4, a, "/"); ip = a[1]; pfx = a[2] }
	dev ~ /^(lo|docker|lxc|lxd|veth|br-|tun|tap|wg)/ { next }
	ip ~ /^(127\.|169\.254\.|172\.17\.|10\.0\.3\.)/ { next }
	ip ~ /^(10\.|192\.168\.)/ || ip ~ /^172\.(1[6-9]|2[0-9]|3[01])\./ { print dev, ip, pfx }')

echo "--self"
echo "$nets"

# Indirizzi da provare: la rete dell'interfaccia, al massimo una /24 (quella del NAS).
hosts=$(echo "$nets" | awk '
	function toint(s,  p) { split(s, p, "."); return ((p[1] * 256 + p[2]) * 256 + p[3]) * 256 + p[4] }
	function tostr(n) { return int(n / 16777216) "." int(n / 65536) % 256 "." int(n / 256) % 256 "." n % 256 }
	NF == 3 {
		self[$2] = 1; pfx = $3 + 0; if (pfx < 24) pfx = 24
		size = 2 ^ (32 - pfx); base = toint($2) - toint($2) % size
		for (i = 1; i < size - 1; i++) { h = tostr(base + i); if (!(h in seen)) { seen[h] = 1; list[++n] = h } }
	}
	END { for (i = 1; i <= n; i++) if (!(list[i] in self)) print list[i] }')

tag() { echo "$2" | sed -n "s/.*<$1><!\[CDATA\[\([^]]*\)\]\]><\/$1>.*/\1/p" | head -n 1; }

get() { curl -s -k -m 3 --connect-timeout 1 "$1" 2>/dev/null | tr -d '\r\n'; }

check() {
	b=$(get "http://$1:8080/cgi-bin/authLogin.cgi")
	case "$b" in *QDocRoot*) ;; *) b=$(get "https://$1/cgi-bin/authLogin.cgi") ;; esac
	case "$b" in *QDocRoot*)
		printf 'qnap|%s|%s|%s|%s\n' "$1" "$(tag modelName "$b")" "$(tag hostname "$b")" "$(tag version "$b")"
		return ;;
	esac
	q="webapi/query.cgi?api=SYNO.API.Info&version=1&method=query&query=SYNO.API.Auth"
	b=$(get "http://$1:5000/$q")
	case "$b" in *SYNO.API.Auth*) ;; *) b=$(get "https://$1:5001/$q") ;; esac
	case "$b" in *SYNO.API.Auth*) printf 'synology|%s|||\n' "$1" ;; esac
}

echo "--found"
n=0
for h in $hosts; do
	check "$h" &
	n=$((n + 1))
	[ $((n % PAR)) -eq 0 ] && wait
done
wait

echo "--arp"
cat /proc/net/arp 2>/dev/null
exit 0
