"""v2 tools endpoint — wraps `hermes tools --summary`.

Interactive per-platform `hermes tools` wizard cannot run over HTTP, so
only the summary read-back is exposed.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.routes.v2._base import cli_payload, raise_for_exit_code, run_for
from hermes_mgmt.routes.v2._parsers import parse_tools_summary

router = APIRouter(
    prefix="/api/v2/tools",
    tags=["v2:tools"],
    dependencies=[Depends(require_auth)],
)


@router.get("/summary", response_model=ApiResponse)
async def summary(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "tools", ["--summary"])
    raise_for_exit_code(result, "hermes tools --summary failed")
    return ApiResponse(ok=True, data=cli_payload(result, parse_tools_summary))
