"""Zalo maintenance mode — one shared state file, two control surfaces.

The bot plugin re-reads this file on every inbound message, so toggling from
the dashboard (this router) or from the Zalo owner command (``/bot baotri``)
both land in the same place and stay in sync with zero coupling — no gateway
restart, no IPC.
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from hermes_mgmt.deps import require_auth
from hermes_mgmt.models import ApiResponse

logger = logging.getLogger(__name__)

# Shared maintenance state — the SAME file the zalo-personal bot plugin reads
# live on every inbound message. Toggling here (mgmt API) and toggling from the
# Zalo owner command (/bot baotri) both write this one file, so the two control
# surfaces stay in sync automatically with zero coupling.
_MAINT_FILE = Path("/opt/data/zalo/maintenance.json")

# Generic placeholder the panel can show as a hint for the "empty message"
# case. NOT persona-specific on purpose — each deployment's real default notice
# lives in its own bot plugin (e.g. the zalo-personal adapter's
# _MAINT_DEFAULT_MSG). When `message` is empty the plugin sends ITS OWN default,
# not this string.
_DEFAULT_MESSAGE = (
    "Hệ thống đang được bảo trì, tạm thời chưa hỗ trợ được. "
    "Quý khách vui lòng nhắn lại sau ít phút. Xin lỗi vì sự bất tiện."
)

router = APIRouter(tags=["zalo-maintenance"], dependencies=[Depends(require_auth)])


class MaintenanceUpdate(BaseModel):
    enabled: bool
    message: str = ""


def _read_state() -> dict:
    try:
        if _MAINT_FILE.exists():
            data = json.loads(_MAINT_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    "enabled": bool(data.get("enabled", False)),
                    "message": data.get("message") or "",
                    "set_at": data.get("set_at"),
                }
    except Exception as exc:  # noqa: BLE001 — never fail a read on bad file
        logger.warning("read maintenance state failed: %s", exc)
    return {"enabled": False, "message": "", "set_at": None}


def _write_state(enabled: bool, message: str) -> dict:
    _MAINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "enabled": bool(enabled),
        "message": message or "",
        "set_at": datetime.datetime.now().isoformat(),
    }
    _MAINT_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return state


@router.get("/api/zalo/maintenance", response_model=ApiResponse)
async def get_maintenance() -> ApiResponse:
    """Return the current Zalo maintenance-mode state.

    Shape: {enabled, message, set_at, default_message}. When `message` is empty
    and `enabled` is true, the bot sends `default_message` to customers.
    """
    data = _read_state()
    data["default_message"] = _DEFAULT_MESSAGE
    return ApiResponse(ok=True, data=data)


@router.post("/api/zalo/maintenance", response_model=ApiResponse)
async def set_maintenance(body: MaintenanceUpdate) -> ApiResponse:
    """Enable/disable Zalo maintenance mode.

    - `enabled=true` + `message` non-empty  → customers get that exact notice.
    - `enabled=true` + `message` empty       → customers get the plugin default.
    - `enabled=false`                        → normal operation.

    Takes effect immediately — the bot plugin reads the state file live, so no
    gateway restart is needed.
    """
    state = _write_state(body.enabled, body.message)
    return ApiResponse(ok=True, data=state)
