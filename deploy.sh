#!/usr/bin/env bash
# Deploy della Backup Console sulla VM (da lanciare come utente del gruppo docker).
#
#   ./deploy.sh                    aggiorna i file di configurazione e riavvia la versione in .env
#   ./deploy.sh --version 0.3.0    passa alla versione 0.3.0 (tag console-v0.3.0)
#   ./deploy.sh --version 0.2.0    ...e allo stesso modo si torna a una versione precedente
#   ./deploy.sh --edge             aggiorna anche lo stack edge (nginx + certbot)
#
# Opzioni per casi particolari:
#   --no-git      non esegue git pull
#   --no-pull     usa le immagini già presenti (build locali)
#   --no-backup   salta il backup del database prima del deploy
#
# Sequenza: git pull → validazione configurazione → pull immagini → backup DB →
# riavvio → attesa migrazioni e healthcheck → controllo versione e URL pubblico.
# Se qualcosa fallisce prima del riavvio, .env torna com'era e nulla cambia.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CONSOLE="$ROOT/console/deploy"
EDGE="$ROOT/edge"
LOG="$ROOT/deploy.log"

version="" with_edge=0 do_git=1 do_pull=1 do_backup=1
while [ $# -gt 0 ]; do
  case "$1" in
    --version) version="${2:?--version richiede un valore}"; shift ;;
    --edge) with_edge=1 ;;
    --no-git) do_git=0 ;;
    --no-pull) do_pull=0 ;;
    --no-backup) do_backup=0 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "opzione sconosciuta: $1 (vedi --help)" >&2; exit 2 ;;
  esac
  shift
done

log()  { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }
fail() {
  log "ERRORE: $*"
  # Se i container non sono ancora stati toccati, rimettiamo .env com'era.
  if [ "$touched" = 0 ] && [ "$env_changed" = 1 ]; then
    mv "$CONSOLE/.env.bak" "$CONSOLE/.env"
    log "nessun container modificato: BC_VERSION ripristinata a $previous"
    previous=""
  fi
  rollback_hint
  exit 1
}
env_get() { sed -n "s/^$1=//p" "$CONSOLE/.env" | tail -1; }

previous="" backup_file="" touched=0 env_changed=0
rollback_hint() {
  [ -n "$previous" ] || return 0
  echo
  echo "Per tornare alla versione precedente:  ./deploy.sh --no-git --version $previous"
  [ -n "$backup_file" ] && echo "Backup del database prima del deploy:   $backup_file" \
    && echo "  (ripristino: docker compose exec -T db pg_restore -U bc -d bc --clean < $backup_file)"
  return 0
}

console() { (cd "$CONSOLE" && docker compose "$@"); }
edge()    { (cd "$EDGE" && docker compose "$@"); }

# --- controlli preliminari ---------------------------------------------------
command -v docker >/dev/null || fail "docker non trovato"
[ -f "$CONSOLE/.env" ] || fail "manca $CONSOLE/.env (partire da .env.example)"
[ "$with_edge" = 0 ] || [ -f "$EDGE/.env" ] || fail "manca $EDGE/.env"

log "=== deploy avviato da $(whoami)"
if [ "$do_git" = 1 ]; then
  if ! git -C "$ROOT" diff --quiet || ! git -C "$ROOT" diff --cached --quiet; then
    fail "modifiche locali nel repository: sistemarle (git status) o usare --no-git"
  fi
  git -C "$ROOT" pull --ff-only 2>&1 | tee -a "$LOG" || fail "git pull non riuscito"
fi

previous="$(env_get BC_VERSION)"
[ -n "$previous" ] || fail "BC_VERSION non impostata in $CONSOLE/.env"
target="${version:-$previous}"
if ! [[ "$target" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$|^dev$ ]]; then
  fail "versione non valida: $target"
fi
log "versione: $previous → $target"

if [ "$target" != "$previous" ]; then
  cp "$CONSOLE/.env" "$CONSOLE/.env.bak"
  sed -i "s/^BC_VERSION=.*/BC_VERSION=$target/" "$CONSOLE/.env"
  env_changed=1
fi
console config -q || fail "configurazione della console non valida"
[ "$with_edge" = 0 ] || edge config -q || fail "configurazione edge non valida"

# --- immagini ---------------------------------------------------------------
if [ "$do_pull" = 1 ]; then
  log "download immagini $target"
  console pull 2>&1 | tail -3 | tee -a "$LOG" || fail "download delle immagini non riuscito (versione $target esiste? docker login ghcr.io?)"
fi
# Le immagini devono esserci già: sulla VM non si compila mai dai sorgenti.
for img in $(console config --images); do
  docker image inspect "$img" >/dev/null 2>&1 || fail "immagine $img non disponibile (versione $target pubblicata?)"
done
# --- backup del database (le migrazioni partono al riavvio) -------------------
if [ "$do_backup" = 1 ] && [ -n "$(console ps -q db 2>/dev/null)" ]; then
  dir="$(env_get BC_DATA_DIR)"; dir="${dir:-/srv/backup-console}/backup"
  [ -w "$dir" ] || fail "$dir non esiste o non è scrivibile (sudo install -d -m 700 -o \$USER $dir), oppure --no-backup"
  backup_file="$dir/pre-deploy-$previous-to-$target-$(date +%Y%m%d-%H%M%S).dump"
  console exec -T db pg_dump -U bc -Fc bc > "$backup_file" || fail "backup del database non riuscito"
  log "backup database: $backup_file ($(du -h "$backup_file" | cut -f1))"
  # Teniamo gli ultimi 10 backup pre-deploy.
  ls -1t "$dir"/pre-deploy-*.dump 2>/dev/null | tail -n +11 | xargs -r rm -f
fi

# --- riavvio ----------------------------------------------------------------
touched=1
console up -d --remove-orphans --no-build --pull never 2>&1 | tail -3 | tee -a "$LOG" || fail "avvio dello stack non riuscito"

# Migrazioni: il servizio init deve terminare con 0.
init_id="$(console ps -a -q init)"
code="$(docker wait "$init_id" 2>/dev/null || echo 1)"
if [ "$code" != 0 ]; then
  console logs --tail 30 init | tee -a "$LOG"
  fail "migrazioni/init terminate con codice $code"
fi

# Healthcheck di api e frontend.
for svc in api frontend; do
  id="$(console ps -q "$svc")"
  [ -n "$id" ] || fail "$svc non in esecuzione"
  for _ in $(seq 1 45); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo unknown)"
    [ "$status" = healthy ] && break
    sleep 2
  done
  [ "$status" = healthy ] || { console logs --tail 30 "$svc" | tee -a "$LOG"; fail "$svc non è healthy ($status)"; }
done

running="$(console exec -T api python -c "import json,urllib.request;print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz'))['version'])")"
if [ "$running" != "$target" ]; then
  if [ -n "$(env_get BACKEND_IMAGE)" ]; then
    log "attenzione: immagine locale (BACKEND_IMAGE), versione in esecuzione '$running'"
  else
    fail "in esecuzione la versione '$running' invece di '$target'"
  fi
fi

# --- edge -------------------------------------------------------------------
if [ "$with_edge" = 1 ]; then
  log "aggiornamento stack edge"
  [ "$do_pull" = 0 ] || edge pull 2>&1 | tail -2 | tee -a "$LOG"
  edge up -d 2>&1 | tail -2 | tee -a "$LOG" || fail "avvio edge non riuscito"
  sleep 2
  edge exec -T nginx nginx -t 2>&1 | tee -a "$LOG" || fail "configurazione nginx non valida"
fi

# --- controllo dall'esterno, attraverso nginx ---------------------------------
url="$(env_get BC_PUBLIC_URL)"
# DEPLOY_CURL_OPTS serve solo nei test locali (es. --cacert edge/dev-ca.crt).
# shellcheck disable=SC2086
if ! curl -fsS --max-time 10 ${DEPLOY_CURL_OPTS:-} "$url/api/v1/auth-config" >/dev/null; then
  fail "$url non risponde attraverso nginx (stack edge avviato? DNS/certificato?)"
fi

log "=== deploy completato: versione $running attiva su $url"
[ "$target" = "$previous" ] || echo "(precedente: $previous — per tornare indietro: ./deploy.sh --no-git --version $previous)"
