"""Job periodici: rilevamento agenti offline (poi: notifiche, retention, scadenza certificati)."""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import SessionLocal
from app.models import Agent, AgentStatus, Command, CommandStatus, DeviceKind, Severity
from app.models.base import utcnow
from app.services.problems import raise_problem, resolve_problem

log = logging.getLogger("bc.worker")


async def check_offline_agents() -> int:
    """Apre agent.offline per NAS e server silenziosi. I PC sono esclusi: spenti o fuori sede
    è la norma, per loro conta il check "nessun backup riuscito da N giorni"."""
    cutoff = utcnow() - timedelta(seconds=settings.agent_offline_after_s)
    raised = 0
    async with SessionLocal() as session:
        agents = (
            await session.scalars(
                select(Agent).where(Agent.status == AgentStatus.APPROVED).options(selectinload(Agent.device))
            )
        ).all()
        for agent in agents:
            if agent.device.kind == DeviceKind.PC:
                continue
            if agent.last_seen_at is None or agent.last_seen_at < cutoff:
                seen = agent.last_seen_at.isoformat() if agent.last_seen_at else None
                await raise_problem(
                    session, agent.device, "agent.offline", Severity.CRITICAL,
                    f"Agente offline: {agent.device.name}", details={"last_seen_at": seen},
                )
                raised += 1
            else:
                await resolve_problem(session, agent.device_id, "agent.offline")
        await session.commit()
    return raised


QUEUE_TTL = timedelta(hours=1)
RESULT_GRACE = timedelta(minutes=2)


async def expire_commands() -> int:
    """Comandi mai ritirati (agente spento) → expired; avviati ma senza esito → timeout."""
    now = utcnow()
    async with SessionLocal() as session:
        r1 = await session.execute(
            update(Command)
            .where(Command.status == CommandStatus.QUEUED, Command.created_at < now - QUEUE_TTL)
            .values(status=CommandStatus.EXPIRED, finished_at=now, error="non ritirato dall'agente entro 1 ora")
        )
        running = (
            await session.scalars(
                select(Command).where(Command.status.in_([CommandStatus.DISPATCHED, CommandStatus.RUNNING]))
            )
        ).all()
        n = r1.rowcount or 0
        for c in running:
            if c.dispatched_at and c.dispatched_at + timedelta(seconds=c.timeout_s) + RESULT_GRACE < now:
                c.status, c.finished_at = CommandStatus.TIMEOUT, now
                c.error = c.error or "nessun esito dall'agente entro il timeout"
                n += 1
        await session.commit()
    return n


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.info("worker avviato, intervallo %ss", settings.worker_interval_s)
    while True:
        try:
            n = await check_offline_agents()
            if n:
                log.info("agenti offline: %d", n)
            if m := await expire_commands():
                log.info("comandi scaduti: %d", m)
        except Exception:
            log.exception("errore nel ciclo del worker")
        await asyncio.sleep(settings.worker_interval_s)


if __name__ == "__main__":
    asyncio.run(main())
