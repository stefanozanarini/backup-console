"""Comandi di amministrazione: `python -m app.cli <comando>`.

  init                         migrazioni DB + creazione CA agenti (servizio "init" del compose)
  create-user EMAIL [--role superadmin|operator] [--tenant SLUG]
  create-tenant NOME SLUG
  create-site SLUG NOME
  create-token SLUG NOME_SITO [--note TESTO] [--uses N] [--hours H]
  reset-totp EMAIL             disattiva il TOTP di un utente (che dovrà riconfigurarlo)
  set-password EMAIL           password per il login locale (BC_LOCAL_LOGIN); configura
                               anche il TOTP se l'utente non l'ha ancora
  clear-password EMAIL         rimuove la password (niente più login locale per l'utente)
"""

import argparse
import asyncio
import getpass
import secrets
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.api.agent.routes import hash_token
from app.config import settings
from app.db import SessionLocal
from app.models import EnrollmentToken, Role, RoleAssignment, Site, Tenant, User
from app.models.base import utcnow
from app.services.audit import audit
from app.services import secrets as secret_store
from app.services import totp
from app.services.passwords import MIN_LENGTH, hash_password
from app.services.ca import AgentCA


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    command.upgrade(cfg, "head")


async def tenant_by_slug(session, slug: str) -> Tenant:
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        sys.exit(f"tenant '{slug}' non trovato")
    return tenant


async def create_user(email: str, role: str, tenant_slug: str | None) -> None:
    async with SessionLocal() as session:
        tenant_id = (await tenant_by_slug(session, tenant_slug)).id if tenant_slug else None
        user = await session.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            user = User(email=email.lower())
            session.add(user)
            await session.flush()
        exists = await session.scalar(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user.id,
                RoleAssignment.role == role,
                RoleAssignment.tenant_id.is_(None) if tenant_id is None else RoleAssignment.tenant_id == tenant_id,
            )
        )
        if exists is None:
            session.add(RoleAssignment(user_id=user.id, role=role, tenant_id=tenant_id))
        audit(session, "user.create", target=user.email, tenant_id=tenant_id, role=role, via="cli")
        await session.commit()
        print(f"utente {user.email}: ruolo {role} su {tenant_slug or 'tutti i tenant'}")


async def create_tenant(name: str, slug: str) -> None:
    async with SessionLocal() as session:
        tenant = Tenant(name=name, slug=slug)
        session.add(tenant)
        await session.commit()
        print(f"tenant {slug}: {tenant.id}")


async def create_site(slug: str, name: str) -> None:
    async with SessionLocal() as session:
        tenant = await tenant_by_slug(session, slug)
        site = Site(tenant_id=tenant.id, name=name)
        session.add(site)
        await session.commit()
        print(f"sito {name}: {site.id}")


async def create_token(slug: str, site_name: str, note: str | None, uses: int, hours: int | None) -> None:
    async with SessionLocal() as session:
        tenant = await tenant_by_slug(session, slug)
        site = await session.scalar(select(Site).where(Site.tenant_id == tenant.id, Site.name == site_name))
        if site is None:
            sys.exit(f"sito '{site_name}' non trovato")
        plain = secrets.token_urlsafe(32)
        expires = utcnow() + timedelta(hours=hours or settings.enroll_token_ttl_hours)
        session.add(EnrollmentToken(
            tenant_id=tenant.id, site_id=site.id, token_hash=hash_token(plain), note=note, expires_at=expires,
            max_uses=uses,
        ))
        audit(session, "enrollment_token.create", tenant_id=tenant.id, target=str(site.id), via="cli")
        await session.commit()
        print(f"token per {uses} dispositivi (valido fino a {expires:%Y-%m-%d %H:%M} UTC, mostrato una sola volta):\n{plain}")


async def reset_totp(email: str) -> None:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            sys.exit(f"utente {email} non trovato")
        user.totp_enabled, user.totp_secret_enc, user.totp_last_counter = False, None, None
        audit(session, "totp.reset", target=user.email, via="cli")
        await session.commit()
        print(f"TOTP disattivato per {user.email}")


async def set_password(email: str) -> None:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            sys.exit(f"utente {email} non trovato (crearlo prima con create-user)")
        pw = getpass.getpass(f"Nuova password per {user.email} (min {MIN_LENGTH} caratteri): ")
        if len(pw) < MIN_LENGTH:
            sys.exit(f"password troppo corta: servono almeno {MIN_LENGTH} caratteri")
        if getpass.getpass("Ripeti la password: ") != pw:
            sys.exit("le password non coincidono")
        user.password_hash, user.failed_logins, user.locked_until = hash_password(pw), 0, None
        new_totp = None
        if not user.totp_enabled:
            new_totp = totp.new_secret()
            user.totp_secret_enc, user.totp_enabled, user.totp_last_counter = secret_store.encrypt(new_totp), True, None
        audit(session, "password.set", target=user.email, via="cli", totp_created=new_totp is not None)
        await session.commit()
        print(f"password impostata per {user.email}")
        if new_totp:
            print("\nCodice di sicurezza (TOTP) creato: aggiungilo ORA all'app di autenticazione")
            print("(Microsoft/Google Authenticator → aggiungi account → inserisci chiave manualmente)")
            print(f"  Account: Backup Console ({user.email})")
            print(f"  Chiave:  {' '.join(new_totp[i:i + 4] for i in range(0, len(new_totp), 4))}")
            print(f"  URI:     {totp.provisioning_uri(new_totp, user.email)}")
            print("La chiave non verrà mostrata di nuovo (per rigenerarla: reset-totp, poi set-password).")


async def clear_password(email: str) -> None:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            sys.exit(f"utente {email} non trovato")
        user.password_hash = None
        audit(session, "password.clear", target=user.email, via="cli")
        await session.commit()
        print(f"password rimossa per {user.email}")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    u = sub.add_parser("create-user")
    u.add_argument("email")
    u.add_argument("--role", default=Role.SUPERADMIN, choices=[r.value for r in Role])
    u.add_argument("--tenant")
    t = sub.add_parser("create-tenant")
    t.add_argument("name")
    t.add_argument("slug")
    s = sub.add_parser("create-site")
    s.add_argument("tenant_slug")
    s.add_argument("name")
    k = sub.add_parser("create-token")
    k.add_argument("tenant_slug")
    k.add_argument("site_name")
    k.add_argument("--note")
    k.add_argument("--uses", type=int, default=1)
    k.add_argument("--hours", type=int)
    r = sub.add_parser("reset-totp")
    r.add_argument("email")
    for name in ("set-password", "clear-password"):
        sub.add_parser(name).add_argument("email")
    args = p.parse_args()

    match args.cmd:
        case "init":
            run_migrations()
            created = AgentCA(settings.ca_dir).ensure()
            print("CA agenti creata" if created else "CA agenti già presente")
            if secret_store.ensure_key():
                print("chiave locale dei segreti creata")
        case "create-user":
            asyncio.run(create_user(args.email, args.role, args.tenant))
        case "create-tenant":
            asyncio.run(create_tenant(args.name, args.slug))
        case "create-site":
            asyncio.run(create_site(args.tenant_slug, args.name))
        case "create-token":
            asyncio.run(create_token(args.tenant_slug, args.site_name, args.note, args.uses, args.hours))
        case "reset-totp":
            asyncio.run(reset_totp(args.email))
        case "set-password":
            asyncio.run(set_password(args.email))
        case "clear-password":
            asyncio.run(clear_password(args.email))


if __name__ == "__main__":
    main()
