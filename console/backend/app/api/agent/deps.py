import re
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Agent, AgentStatus
from app.services.ca import AgentCA

# Impostato da Caddy dopo aver verificato il certificato client contro la CA agenti.
# Caddy sovrascrive sempre questo header, e il container api non è esposto:
# un client non può quindi iniettarlo.
FINGERPRINT_HEADER = "x-client-cert-fingerprint"
FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")


def get_ca(request: Request) -> AgentCA:
    return request.app.state.ca


async def get_agent(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> Agent:
    fp = request.headers.get(FINGERPRINT_HEADER, "").strip().lower().replace(":", "")
    # Senza certificato client il valore non è un hash valido: lo trattiamo come assente.
    if not FINGERPRINT_RE.fullmatch(fp):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "certificato client mancante")
    agent = await session.scalar(
        select(Agent).where(Agent.cert_fingerprint == fp).options(selectinload(Agent.device))
    )
    if agent is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "agente sconosciuto")
    if agent.status == AgentStatus.REVOKED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "agente revocato")
    return agent


async def get_approved_agent(agent: Annotated[Agent, Depends(get_agent)]) -> Agent:
    if agent.status != AgentStatus.APPROVED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "agente in attesa di approvazione")
    return agent


AgentDep = Annotated[Agent, Depends(get_agent)]
ApprovedAgentDep = Annotated[Agent, Depends(get_approved_agent)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CADep = Annotated[AgentCA, Depends(get_ca)]
