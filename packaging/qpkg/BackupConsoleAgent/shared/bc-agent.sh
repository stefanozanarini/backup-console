#!/bin/sh
# Script di servizio del QPKG BackupConsoleAgent (busybox sh).
#
#   bc-agent.sh start|stop|restart|status
#   bc-agent.sh enroll -server https://agents.<dominio> -token <TOKEN> [-allow-shell]
#   bc-agent.sh collect      stampa i dati raccolti, senza inviarli
#
# Un piccolo supervisore riavvia l'agente se termina e ruota il log.

CONF=/etc/config/qpkg.conf
QPKG_NAME="BackupConsoleAgent"
QPKG_ROOT="${QPKG_ROOT:-$(/sbin/getcfg $QPKG_NAME Install_Path -f $CONF)}"
BIN="$QPKG_ROOT/bc-agent"
CFG="$QPKG_ROOT/etc/agent.json"
LOG_DIR="$QPKG_ROOT/log"
LOG="$LOG_DIR/agent.log"
RUN_DIR="${RUN_DIR:-/var/run}"
SUP_PID="$RUN_DIR/bc-agent-supervisor.pid"
AGENT_PID="$RUN_DIR/bc-agent.pid"
LOG_MAX_BYTES=5242880
RESTART_DELAY="${RESTART_DELAY:-15}"
export QNAP_QPKG=$QPKG_NAME

log() {
	echo "$(date '+%Y-%m-%d %H:%M:%S') [bc-agent.sh] $*" >>"$LOG"
}

rotate_log() {
	[ -f "$LOG" ] || return 0
	size=$(wc -c <"$LOG" 2>/dev/null || echo 0)
	if [ "$size" -gt "$LOG_MAX_BYTES" ]; then
		mv -f "$LOG" "$LOG.1"
	fi
}

is_running() {
	[ -f "$SUP_PID" ] && kill -0 "$(cat "$SUP_PID")" 2>/dev/null
}

supervise() {
	trap 'kill "$(cat "$AGENT_PID" 2>/dev/null)" 2>/dev/null; rm -f "$AGENT_PID"; exit 0' TERM INT
	while :; do
		rotate_log
		"$BIN" run -config "$CFG" >>"$LOG" 2>&1 &
		echo $! >"$AGENT_PID"
		wait $!
		code=$?
		rm -f "$AGENT_PID"
		log "agente terminato (codice $code), riavvio tra ${RESTART_DELAY}s"
		sleep "$RESTART_DELAY"
	done
}

start() {
	mkdir -p "$LOG_DIR" "$QPKG_ROOT/etc"
	if is_running; then
		echo "$QPKG_NAME: già in esecuzione"
		return 0
	fi
	if [ ! -f "$CFG" ]; then
		# Non è un errore: il pacchetto è installato ma non ancora registrato.
		echo "$QPKG_NAME: non registrato. Eseguire: $0 enroll -server <url> -token <token>"
		log "avvio saltato: agente non registrato"
		return 0
	fi
	# Processo separato: così in ps non compaiono gli argomenti del comando chiamante
	# (es. il token di enroll).
	"$0" _supervise </dev/null >/dev/null 2>&1 &
	echo $! >"$SUP_PID"
	log "avviato (supervisore pid $!)"
	echo "$QPKG_NAME: avviato"
}

stop() {
	if [ -f "$SUP_PID" ]; then
		kill "$(cat "$SUP_PID")" 2>/dev/null
		rm -f "$SUP_PID"
	fi
	if [ -f "$AGENT_PID" ]; then
		kill "$(cat "$AGENT_PID")" 2>/dev/null
		rm -f "$AGENT_PID"
	fi
	log "fermato"
	echo "$QPKG_NAME: fermato"
}

case "$1" in
	start)
		ENABLED=$(/sbin/getcfg $QPKG_NAME Enable -u -d FALSE -f $CONF)
		if [ "$ENABLED" != "TRUE" ]; then
			echo "$QPKG_NAME è disabilitato."
			exit 1
		fi
		start
		;;
	stop)
		stop
		;;
	restart)
		stop
		sleep 1
		start
		;;
	status)
		if is_running; then
			echo "$QPKG_NAME: in esecuzione ($("$BIN" version))"
		else
			echo "$QPKG_NAME: fermo"
			exit 1
		fi
		;;
	enroll)
		shift
		mkdir -p "$QPKG_ROOT/etc" "$LOG_DIR"
		chmod 700 "$QPKG_ROOT/etc"
		if [ -f "$CFG" ]; then
			echo "Attenzione: esiste già una registrazione ($CFG), verrà sostituita."
		fi
		"$BIN" enroll -config "$CFG" "$@" || exit $?
		stop >/dev/null
		start
		;;
	collect)
		"$BIN" collect
		;;
	_supervise)
		supervise
		;;
	remove)
		;;
	*)
		echo "Uso: $0 {start|stop|restart|status|enroll|collect}"
		exit 1
		;;
esac

exit 0
