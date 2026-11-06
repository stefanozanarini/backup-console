"""Tenant "Da assegnare": raccoglie i dispositivi installati con un token generico, finché un
operatore non li sposta sul tenant e sulla sede giusti."""


from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, Site, Tenant
from app.models.base import Base

HOLDING_SLUG = "da-assegnare"
HOLDING_NAME = "Da assegnare"
HOLDING_SITE = "In attesa di assegnazione"


async def get_or_create(session: AsyncSession) -> tuple[Tenant, Site, bool]:
    created = False
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == HOLDING_SLUG))
    if tenant is None:
        tenant = Tenant(name=HOLDING_NAME, slug=HOLDING_SLUG)
        session.add(tenant)
        await session.flush()
        created = True
    site = await session.scalar(select(Site).where(Site.tenant_id == tenant.id).order_by(Site.created_at))
    if site is None:
        site = Site(tenant_id=tenant.id, name=HOLDING_SITE)
        session.add(site)
        await session.flush()
        created = True
    return tenant, site, created


def _device_tables():
    """Tabelle con dati del dispositivo che riportano anche il tenant (per i filtri per tenant)."""
    return [t for t in Base.metadata.sorted_tables
            if t.name != "devices" and "device_id" in t.c and "tenant_id" in t.c]


async def move_device(session: AsyncSession, device: Device, site: Site) -> None:
    """Sposta il dispositivo con tutto il suo storico (problemi, job, metriche, comandi...)."""
    device.tenant_id = site.tenant_id
    device.site_id = site.id
    for table in _device_tables():
        await session.execute(
            update(table).where(table.c.device_id == device.id).values(tenant_id=site.tenant_id)
        )


def is_holding(tenant_slug: str) -> bool:
    return tenant_slug == HOLDING_SLUG


