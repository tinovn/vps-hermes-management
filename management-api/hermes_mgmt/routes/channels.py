from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.env_file import delete_env, mask_value, read_env, set_env
from hermes_mgmt.models import ApiResponse, ChannelTokenRequest
from hermes_mgmt.systemd_ctl import restart

logger = logging.getLogger(__name__)

router = APIRouter(tags=["channels"], dependencies=[Depends(require_auth)])

# Maps channel slug -> primary env var + optional extra vars
_CHANNEL_MAP: dict[str, dict] = {
    "telegram": {
        "primary": "TELEGRAM_BOT_TOKEN",
        "extras": [],
    },
    "discord": {
        "primary": "DISCORD_BOT_TOKEN",
        "extras": [],
    },
    "slack": {
        "primary": "SLACK_BOT_TOKEN",
        "extras": ["SLACK_APP_TOKEN"],
    },
    "slack_app": {
        "primary": "SLACK_APP_TOKEN",
        "extras": [],
    },
    "signal": {
        "primary": "SIGNAL_ACCOUNT",
        "extras": [],
    },
    "whatsapp": {
        "primary": "WHATSAPP_MODE",
        "extras": [],
    },
}

# All vars tracked across all channels for listing
_ALL_CHANNEL_VARS = {
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SIGNAL_ACCOUNT",
    "WHATSAPP_MODE",
}


@router.get("/api/channels", response_model=ApiResponse)
async def list_channels(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    env = read_env(settings.env_file)
    channels: list[dict] = []
    for slug, cfg in _CHANNEL_MAP.items():
        primary_key = cfg["primary"]
        raw_value = env.get(primary_key, "")
        enabled = bool(raw_value)
        channels.append(
            {
                "channel": slug,
                "enabled": enabled,
                "env_var": primary_key,
                "value": mask_value(primary_key, raw_value) if enabled else "",
            }
        )
    return ApiResponse(ok=True, data=channels)


@router.put("/api/channels/{channel}", response_model=ApiResponse)
async def set_channel(
    channel: str,
    body: ChannelTokenRequest,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    cfg = _CHANNEL_MAP.get(channel)
    if not cfg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown channel '{channel}'. Valid: {sorted(_CHANNEL_MAP)}",
        )
    set_env(settings.env_file, cfg["primary"], body.token)

    # Write any extra key-value pairs from the body
    if body.extra:
        for env_key, env_val in body.extra.items():
            # Only allow known channel env vars as extras for safety
            env_key_upper = env_key.upper()
            if env_key_upper in _ALL_CHANNEL_VARS:
                set_env(settings.env_file, env_key_upper, env_val)
            else:
                logger.warning("Skipping unknown extra env key: %s", env_key)

    async def do_restart() -> None:
        try:
            await restart("hermes-gateway", settings.allowed_services)
        except Exception as exc:
            logger.error("Failed to restart hermes-gateway after channel update: %s", exc)

    background_tasks.add_task(do_restart)
    return ApiResponse(ok=True, data={"channel": channel, "enabled": True})


@router.delete("/api/channels/{channel}", response_model=ApiResponse)
async def delete_channel(
    channel: str,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    cfg = _CHANNEL_MAP.get(channel)
    if not cfg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown channel '{channel}'. Valid: {sorted(_CHANNEL_MAP)}",
        )
    removed_keys: list[str] = []
    for key in [cfg["primary"], *cfg["extras"]]:
        if delete_env(settings.env_file, key):
            removed_keys.append(key)

    async def do_restart() -> None:
        try:
            await restart("hermes-gateway", settings.allowed_services)
        except Exception as exc:
            logger.error("Failed to restart hermes-gateway after channel delete: %s", exc)

    if removed_keys:
        background_tasks.add_task(do_restart)

    return ApiResponse(ok=True, data={"channel": channel, "removed_keys": removed_keys})
