#!/usr/bin/env bash
# Certificati TLS per lo stack edge.
#
#   ./certs.sh issue <nome-cert> <email> <host> [host...]
#       Let's Encrypt (HTTP-01). Il DNS di ogni host deve già puntare alla VM e la
#       porta 80 deve essere raggiungibile. Al primo avvio crea un certificato
#       temporaneo, così nginx parte, poi lo sostituisce con quello vero.
#
#   ./certs.sh selfsigned <nome-cert> <host> [host...]
#       Solo test: CA locale + certificato, CA esportata in ./dev-ca.crt.
#
# <nome-cert> è quello usato nei template (es. backup-console).
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE=(docker compose --env-file .env)
[ -f .env ] || COMPOSE=(docker compose)

certbot_sh() { "${COMPOSE[@]}" run --rm --entrypoint sh certbot -c "$1"; }

temporary_cert() {
  local name=$1 host=$2
  certbot_sh "
    set -e
    d=/etc/letsencrypt/live/$name
    if [ ! -f \$d/fullchain.pem ]; then
      mkdir -p \$d
      openssl req -x509 -nodes -newkey rsa:2048 -days 1 -subj '/CN=$host' \
        -keyout \$d/privkey.pem -out \$d/fullchain.pem 2>/dev/null
      echo 'certificato temporaneo creato per $name'
    fi"
}

cmd=${1:-}
case "$cmd" in
  issue)
    [ $# -ge 4 ] || { echo "uso: $0 issue <nome-cert> <email> <host> [host...]" >&2; exit 2; }
    name=$2 email=$3; shift 3
    domains=""; for h in "$@"; do domains="$domains -d $h"; done
    temporary_cert "$name" "$1"
    "${COMPOSE[@]}" up -d nginx
    sleep 2
    # Via il temporaneo (certbot rifiuta una directory live/ che non ha creato lui).
    certbot_sh "
      if [ ! -f /etc/letsencrypt/renewal/$name.conf ]; then
        rm -rf /etc/letsencrypt/live/$name /etc/letsencrypt/archive/$name
      fi"
    "${COMPOSE[@]}" run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/certbot \
      --cert-name "$name" $domains --email "$email" --agree-tos --no-eff-email --non-interactive --keep-until-expiring
    "${COMPOSE[@]}" exec nginx nginx -s reload
    echo "certificato $name emesso e caricato"
    ;;
  selfsigned)
    [ $# -ge 3 ] || { echo "uso: $0 selfsigned <nome-cert> <host> [host...]" >&2; exit 2; }
    name=$2; shift 2
    san=""; for h in "$@"; do san="${san:+$san,}DNS:$h"; done
    certbot_sh "
      set -e
      d=/etc/letsencrypt/live/$name; mkdir -p \$d; cd \$d
      openssl req -x509 -nodes -newkey rsa:2048 -days 825 -subj '/CN=Edge dev CA' \
        -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign' \
        -keyout ca.key -out ca.crt 2>/dev/null
      openssl req -nodes -newkey rsa:2048 -subj '/CN=$1' -keyout privkey.pem -out req.csr 2>/dev/null
      printf 'subjectAltName=$san\nextendedKeyUsage=serverAuth\n' > ext.cnf
      openssl x509 -req -in req.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 \
        -extfile ext.cnf -out cert.pem 2>/dev/null
      cat cert.pem > fullchain.pem
      rm -f req.csr ext.cnf ca.key"
    certbot_sh "cat /etc/letsencrypt/live/$name/ca.crt" > dev-ca.crt
    echo "certificato di test $name per $* (CA in $(pwd)/dev-ca.crt)"
    ;;
  *)
    sed -n '2,15p' "$0"; exit 2
    ;;
esac
