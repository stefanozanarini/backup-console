"""Retention dei dati raccolti. Gira nel worker ogni retention_interval_h ore.

- metrics: dettaglio completo per retention_metrics_raw_days, poi un valore medio
  all'ora per serie; oltre retention_metrics_days cancellate;
- inventory_snapshots: tutte per retention_inventory_full_days, poi l'ultima di ogni
  giorno; oltre retention_inventory_days cancellate (tranne l'ultima di ogni dispositivo);
- events, agent_messages: retention_events_days;
- problemi risolti, comandi conclusi, audit, sessioni Veeam: retention_history_days.

Le operazioni lavorano a blocchi di un giorno per non tenere lock lunghi su tabelle grandi.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.models.base import utcnow

log = logging.getLogger("bc.retention")

# Giorni riassunti al massimo per giro: il recupero di un arretrato si distribuisce.
DOWNSAMPLE_MAX_DAYS = 60

# Primo campione non ancora riassunto: i valori orari cadono esattamente sull'ora.
FIRST_RAW = text("SELECT min(ts) FROM metrics WHERE ts < :cut AND ts <> date_trunc('hour', ts)")

DOWNSAMPLE_DAY = text("""
WITH hours AS (
    SELECT device_id, name, labels, date_trunc('hour', ts) AS h
    FROM metrics
    WHERE ts >= :start AND ts < :end
    GROUP BY 1, 2, 3, 4
    HAVING count(*) > 1
), del AS (
    DELETE FROM metrics m
    USING hours
    WHERE m.ts >= :start AND m.ts < :end
      AND m.device_id = hours.device_id AND m.name = hours.name AND m.labels = hours.labels
      AND date_trunc('hour', m.ts) = hours.h
    RETURNING m.tenant_id, m.device_id, m.name, m.labels, date_trunc('hour', m.ts) AS h, m.value
)
INSERT INTO metrics (tenant_id, device_id, ts, name, labels, value)
SELECT tenant_id, device_id, h, name, labels, avg(value)
FROM del
GROUP BY tenant_id, device_id, h, name, labels
""")

THIN_INVENTORY = text("""
DELETE FROM inventory_snapshots s
WHERE s.collected_at < :cut
  AND EXISTS (
    SELECT 1 FROM inventory_snapshots n
    WHERE n.device_id = s.device_id
      AND date_trunc('day', n.collected_at) = date_trunc('day', s.collected_at)
      AND n.collected_at > s.collected_at
  )
""")

OLD_INVENTORY = text("""
DELETE FROM inventory_snapshots s
WHERE s.collected_at < :cut
  AND s.id <> (SELECT n.id FROM inventory_snapshots n WHERE n.device_id = s.device_id
               ORDER BY n.collected_at DESC, n.id DESC LIMIT 1)
""")

SIMPLE = [
    # (tabella, condizione, parametro di retention)
    ("metrics", "ts < :cut", "retention_metrics_days"),
    ("events", "ts < :cut", "retention_events_days"),
    ("agent_messages", "received_at < :cut", "retention_events_days"),
    ("problems", "state = 'resolved' AND resolved_at < :cut", "retention_history_days"),
    ("commands", "finished_at IS NOT NULL AND finished_at < :cut", "retention_history_days"),
    ("audit_log", "ts < :cut", "retention_history_days"),
    ("veeam_sessions", "coalesce(ended_at, started_at) < :cut", "retention_history_days"),
]


def _cut(now: datetime, days: int) -> datetime | None:
    return now - timedelta(days=days) if days > 0 else None


async def run(now: datetime | None = None) -> dict[str, int]:
    now = now or utcnow()
    done: dict[str, int] = {}
    async with SessionLocal() as session:
        # Prima si cancella ciò che è oltre la retention, così il riassunto non ci lavora.
        for table, where, param in SIMPLE:
            if (cut := _cut(now, getattr(settings, param))) is None:
                continue
            r = await session.execute(text(f"DELETE FROM {table} WHERE {where}"), {"cut": cut})
            done[table] = r.rowcount or 0
            await session.commit()

        # Metriche: riassunto orario dei giorni appena usciti dal dettaglio completo.
        if (raw_cut := _cut(now, settings.retention_metrics_raw_days)) is not None:
            # Solo giorni interi, fino all'inizio del giorno in cui cade la soglia.
            limit = raw_cut.replace(hour=0, minute=0, second=0, microsecond=0)
            first = await session.scalar(FIRST_RAW, {"cut": limit})
            n = 0
            if first is not None:
                start = first.replace(hour=0, minute=0, second=0, microsecond=0)
                for _ in range(DOWNSAMPLE_MAX_DAYS):
                    if start >= limit:
                        break
                    r = await session.execute(DOWNSAMPLE_DAY, {"start": start, "end": start + timedelta(days=1)})
                    n += r.rowcount or 0
                    await session.commit()
                    start += timedelta(days=1)
            done["metrics_hourly"] = n

        if (cut := _cut(now, settings.retention_inventory_full_days)) is not None:
            r = await session.execute(THIN_INVENTORY, {"cut": cut})
            done["inventory_thinned"] = r.rowcount or 0
        if (cut := _cut(now, settings.retention_inventory_days)) is not None:
            r = await session.execute(OLD_INVENTORY, {"cut": cut})
            done["inventory_deleted"] = r.rowcount or 0
        await session.commit()
    if any(done.values()):
        log.info("retention: %s", ", ".join(f"{k}={v}" for k, v in done.items() if v))
    return done
