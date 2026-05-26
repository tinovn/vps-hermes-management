from __future__ import annotations

from fastapi import APIRouter

from hermes_mgmt.routes.auth_routes import router as auth_router
from hermes_mgmt.routes.channels import router as channels_router
from hermes_mgmt.routes.cli_routes import router as cli_router
from hermes_mgmt.routes.config_routes import router as config_router
from hermes_mgmt.routes.control import router as control_router
from hermes_mgmt.routes.cron_routes import router as cron_router
from hermes_mgmt.routes.env_routes import router as env_router
from hermes_mgmt.routes.logs import router as logs_router
from hermes_mgmt.routes.status import router as status_router
from hermes_mgmt.routes.v2 import all_v2_routers

all_routers: list[APIRouter] = [
    auth_router,
    status_router,
    control_router,
    config_router,
    channels_router,
    cron_router,
    logs_router,
    env_router,
    cli_router,
    *all_v2_routers,
]
