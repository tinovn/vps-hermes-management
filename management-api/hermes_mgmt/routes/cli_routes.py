from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from hermes_mgmt.cli_runner import HERMES_WHITELIST, run_hermes
from hermes_mgmt.deps import require_auth
from hermes_mgmt.models import ApiResponse, CliRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cli"], dependencies=[Depends(require_auth)])


@router.post("/api/cli", response_model=ApiResponse)
async def run_cli(body: CliRequest) -> ApiResponse:
    if body.subcommand not in HERMES_WHITELIST:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Subcommand '{body.subcommand}' is not permitted. "
                f"Allowed: {sorted(HERMES_WHITELIST)}"
            ),
        )
    result = await run_hermes(body.subcommand, body.args)
    return ApiResponse(ok=result.exit_code == 0, data=result.model_dump())
