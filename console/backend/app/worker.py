"""Job periodici: rilevamento agenti offline (poi: notifiche, retention, scadenza certificati)."""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import SessionLocal
from app.models import Agent, AgentStatus, DeviceKind, Severity
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


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.info("worker avviato, intervallo %ss", settings.worker_interval_s)
    while True:
        try:
            n = await check_offline_agents()
            if n:
                log.info("agenti offline: %d", n)
        except Exception:
            log.exception("errore nel ciclo del worker")
        await asyncio.sleep(settings.worker_interval_s)


if __name__ == "__main__":
    asyncio.run(main())
