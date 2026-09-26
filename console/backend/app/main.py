from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.agent.routes import router as agent_router
from app.api.ui.ops import router as ops_router
from app.api.ui.routes import router as ui_router
from app.config import settings
from app.services import secrets
from app.services.ca import AgentCA


@asynccontextmanager
async def lifespan(app: FastAPI):
    ca = AgentCA(settings.ca_dir)
    # In produzione la CA è creata dal servizio "init", prima che parta nginx.
    ca.ensure()
    secrets.ensure_key()
    app.state.ca = ca
    yield


app = FastAPI(title="Backup Console", version="0.1.0", lifespan=lifespan)
app.include_router(agent_router)
app.include_router(ui_router)
app.include_router(ops_router)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True}
