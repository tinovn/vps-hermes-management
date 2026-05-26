"""v2 model endpoint — wraps `hermes model <model>`.

The interactive `hermes model` wizard cannot be driven from HTTP, so we
only expose the non-interactive form that switches to an explicit model.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.routes.v2._base import cli_payload, raise_for_exit_code, run_for

router = APIRouter(
    prefix="/api/v2/model",
    tags=["v2:model"],
    dependencies=[Depends(require_auth)],
)


class ModelSwitchRequest(BaseModel):
    model: str = Field(min_length=1, max_length=256)


@router.post("/switch", response_model=ApiResponse)
async def switch_model(
    body: ModelSwitchRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "model", [body.model])
    raise_for_exit_code(result, f"hermes model {body.model} failed")
    return ApiResponse(ok=True, data={"model": body.model, **cli_payload(result)})
