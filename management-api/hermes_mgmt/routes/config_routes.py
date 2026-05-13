from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from hermes_mgmt.cli_runner import run_hermes
from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.env_file import delete_env, read_env, set_env
from hermes_mgmt.hermes_fs import read_config_yaml
from hermes_mgmt.models import ApiKeyRequest, ApiResponse, ProviderConfigRequest
from hermes_mgmt.systemd_ctl import restart

logger = logging.getLogger(__name__)

router = APIRouter(tags=["config"], dependencies=[Depends(require_auth)])

# Endpoints used by POST /api/config/test-key. Keep aligned with config/*.json.
# Test endpoint is "<base_url>/v1/models" except where the provider differs
# (e.g. Google Gemini at /v1beta/models, HuggingFace at /models).
_PROVIDER_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
    "google": "https://generativelanguage.googleapis.com",
    "xai": "https://api.x.ai",
    "groq": "https://api.groq.com/openai",
    "mistral": "https://api.mistral.ai",
    "deepseek": "https://api.deepseek.com",
    "openrouter": "https://openrouter.ai/api",
    "together": "https://api.together.xyz",
    "nous-portal": "https://portal.nousresearch.com/api",
    "huggingface": "https://api-inference.huggingface.co",
}

# Override default ``/v1/models`` test path for providers with non-standard APIs.
_PROVIDER_TEST_PATHS: dict[str, str] = {
    "google": "/v1beta/models",
    "huggingface": "/models",
}

_SENSITIVE_PATTERN = re.compile(r"(?i)(api_key|token|secret|password)")


def _mask_dict(data: dict) -> dict:
    """Recursively mask sensitive string values in a dict."""
    result: dict = {}
    for k, v in data.items():
        if isinstance(v, dict):
            result[k] = _mask_dict(v)
        elif isinstance(v, str) and _SENSITIVE_PATTERN.search(str(k)):
            result[k] = "sk-****" + v[-4:] if len(v) > 4 else "****"
        else:
            result[k] = v
    return result


@router.get("/api/config", response_model=ApiResponse)
async def get_config(settings: Annotated[Settings, Depends(get_settings_dep)]) -> ApiResponse:
    config = read_config_yaml(settings.hermes_home)
    masked = _mask_dict(config)
    return ApiResponse(ok=True, data=masked)


def _normalize_model_string(provider: str, model: str) -> str:
    """Build the canonical `<provider>/<bare-model>` string Hermes expects.

    Accepts either a bare model id (`deepseek-v4-flash`) or one already
    prefixed (`deepseek/deepseek-v4-flash`); the latter previously produced
    a double-prefixed `deepseek/deepseek/deepseek-v4-flash`.
    """
    prefix = f"{provider}/"
    bare_model = model[len(prefix):] if model.startswith(prefix) else model
    return f"{prefix}{bare_model}"


@router.put("/api/config/provider", response_model=ApiResponse)
async def set_provider(
    body: ProviderConfigRequest,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    model_string = _normalize_model_string(body.provider, body.model)
    result = await run_hermes("config", ["set", "model.default", body.model])
    if result.exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"hermes config set model.default failed: {result.stderr}",
        )
    result = await run_hermes("config", ["set", "model.provider", body.provider])
    if result.exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"hermes config set model.provider failed: {result.stderr}",
        )

    async def do_restart() -> None:
        try:
            await restart("hermes-gateway", settings.allowed_services)
        except Exception as exc:
            logger.error("Failed to restart hermes-gateway: %s", exc)

    background_tasks.add_task(do_restart)
    return ApiResponse(ok=True, data={"provider": body.provider, "model": body.model})


@router.put("/api/config/api-key", response_model=ApiResponse)
async def set_api_key(
    body: ApiKeyRequest,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    env_key = f"{body.provider.upper().replace('-', '_')}_API_KEY"
    set_env(settings.env_file, env_key, body.api_key)

    async def do_restart() -> None:
        try:
            await restart("hermes-gateway", settings.allowed_services)
        except Exception as exc:
            logger.error("Failed to restart hermes-gateway: %s", exc)

    background_tasks.add_task(do_restart)
    return ApiResponse(ok=True, data={"key": env_key, "provider": body.provider})


@router.delete("/api/config/api-key", response_model=ApiResponse)
async def delete_api_key(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    provider: str = Query(...),
) -> ApiResponse:
    env_key = f"{provider.upper().replace('-', '_')}_API_KEY"
    found = delete_env(settings.env_file, env_key)
    return ApiResponse(ok=True, data={"removed": found, "key": env_key})


@router.post("/api/config/test-key", response_model=ApiResponse)
async def test_api_key(body: ApiKeyRequest) -> ApiResponse:
    provider = body.provider.lower()
    base_url = _PROVIDER_BASE_URLS.get(provider)
    if not base_url:
        return ApiResponse(
            ok=False, error=f"Unknown provider '{body.provider}'. Cannot test key."
        )
    test_path = _PROVIDER_TEST_PATHS.get(provider, "/v1/models")
    url = f"{base_url}{test_path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url, headers={"Authorization": f"Bearer {body.api_key}"}
            )
        ok = resp.status_code == 200
        return ApiResponse(
            ok=ok,
            data={"status_code": resp.status_code, "provider": body.provider},
            error=None if ok else f"Provider returned HTTP {resp.status_code}",
        )
    except httpx.RequestError as exc:
        return ApiResponse(ok=False, error=f"Request failed: {exc}")


@router.get("/api/providers", response_model=ApiResponse)
async def list_providers(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    templates_dir = settings.templates_dir
    providers: list[dict] = []
    if not templates_dir.exists():
        return ApiResponse(ok=True, data=providers)

    import json

    for fpath in sorted(templates_dir.glob("*.json")):
        # Skip channel templates (heuristic: channel files have "token" in name)
        if "channel" in fpath.name.lower():
            continue
        try:
            with fpath.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            masked = _mask_dict(data) if isinstance(data, dict) else data
            providers.append({"file": fpath.name, "config": masked})
        except Exception as exc:
            logger.warning("Could not parse provider file %s: %s", fpath, exc)

    return ApiResponse(ok=True, data=providers)
