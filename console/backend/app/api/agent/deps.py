import hmac
from typing import Annotated
from urllib.parse import unquote

from cryptography import x509
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Agent, AgentStatus
from app.services.ca import AgentCA, fingerprint

# Impostati da nginx sulle location /agent/v1/ (proxy_set_header, quindi sempre
# sovrascritti: un client non può iniettarli) dopo la verifica del certificato
# client contro la CA degli agenti:
#   X-Client-Verify = $ssl_client_verify        (SUCCESS / NONE / FAILED:...)
#   X-Client-Cert   = $ssl_client_escaped_cert  (PEM url-encoded)
# L'impronta SHA-256 la calcoliamo qui: nginx espone solo SHA-1.
# Il container api non è esposto: si raggiunge solo passando da nginx.
VERIFY_HEADER = "x-client-verify"
CERT_HEADER = "x-client-cert"
PROXY_SECRET_HEADER = "x-proxy-secret"


def _from_proxy(request: Request) -> bool:
    if not settings.proxy_secret:
        return True
    return hmac.compare_digest(request.headers.get(PROXY_SECRET_HEADER, ""), settings.proxy_secret)


def get_ca(request: Request) -> AgentCA:
    return request.app.state.ca


async def get_agent(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> Agent:
    pem = unquote(request.headers.get(CERT_HEADER, ""))
    if not _from_proxy(request) or request.headers.get(VERIFY_HEADER) != "SUCCESS" or "BEGIN CERTIFICATE" not in pem:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "certificato client mancante")
    try:
        fp = fingerprint(x509.load_pem_x509_certificate(pem.encode()))
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "certificato client non valido") from e
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
