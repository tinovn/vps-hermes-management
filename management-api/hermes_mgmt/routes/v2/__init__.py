"""v2 routers — thin wrappers over Hermes CLI subcommands.

Every endpoint here proxies to `hermes <subcommand> ...` via run_hermes(),
with HERMES_HOME forced to settings.hermes_home so the CLI always targets
the server-wide store (not /root/.hermes or similar).

Mounted under /api/v2/<namespace>. v1 routes stay intact for back-compat.
"""
from __future__ import annotations

from fastapi import APIRouter

from hermes_mgmt.routes.v2.auth import router as auth_router
from hermes_mgmt.routes.v2.backup import router as backup_router
from hermes_mgmt.routes.v2.bundles import router as bundles_router
from hermes_mgmt.routes.v2.config import router as config_router
from hermes_mgmt.routes.v2.cron import router as cron_router
from hermes_mgmt.routes.v2.curator import router as curator_router
from hermes_mgmt.routes.v2.diagnostics import router as diagnostics_router
from hermes_mgmt.routes.v2.fallback import router as fallback_router
from hermes_mgmt.routes.v2.gateway import router as gateway_router
from hermes_mgmt.routes.v2.kanban import router as kanban_router
from hermes_mgmt.routes.v2.memory import router as memory_router
from hermes_mgmt.routes.v2.model import router as model_router
from hermes_mgmt.routes.v2.profile import router as profile_router
from hermes_mgmt.routes.v2.sessions import router as sessions_router
from hermes_mgmt.routes.v2.skills import router as skills_router
from hermes_mgmt.routes.v2.tools import router as tools_router
from hermes_mgmt.routes.v2.webhook import router as webhook_router

all_v2_routers: list[APIRouter] = [
    config_router,
    model_router,
    fallback_router,
    auth_router,
    sessions_router,
    memory_router,
    skills_router,
    bundles_router,
    tools_router,
    webhook_router,
    gateway_router,
    cron_router,
    kanban_router,
    curator_router,
    profile_router,
    backup_router,
    diagnostics_router,
]
