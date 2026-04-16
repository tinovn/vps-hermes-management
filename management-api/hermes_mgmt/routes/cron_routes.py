from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from hermes_mgmt.cli_runner import run_hermes
from hermes_mgmt.deps import require_auth
from hermes_mgmt.models import ApiResponse, CronAddRequest, CliResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cron"], dependencies=[Depends(require_auth)])


def _cli_to_response(result: CliResponse) -> ApiResponse:
    if result.exit_code != 0:
        return ApiResponse(
            ok=False,
            error=result.stderr.strip() or f"hermes cron exited with code {result.exit_code}",
            data={"stdout": result.stdout, "stderr": result.stderr},
        )
    return ApiResponse(ok=True, data={"output": result.stdout.strip()})


@router.get("/api/cron", response_model=ApiResponse)
async def list_cron_jobs() -> ApiResponse:
    result = await run_hermes("cron", ["list"])
    if result.exit_code != 0:
        return ApiResponse(
            ok=False,
            error=result.stderr.strip() or "Failed to list cron jobs",
        )
    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    return ApiResponse(ok=True, data={"jobs": lines, "raw": result.stdout})


@router.post("/api/cron", response_model=ApiResponse)
async def add_cron_job(body: CronAddRequest) -> ApiResponse:
    args = ["create", "--spec", body.spec, "--command", body.command]
    if body.name:
        args += ["--name", body.name]
    result = await run_hermes("cron", args)
    return _cli_to_response(result)


@router.delete("/api/cron/{job_id}", response_model=ApiResponse)
async def remove_cron_job(job_id: str) -> ApiResponse:
    result = await run_hermes("cron", ["remove", job_id])
    return _cli_to_response(result)


@router.post("/api/cron/{job_id}/pause", response_model=ApiResponse)
async def pause_cron_job(job_id: str) -> ApiResponse:
    result = await run_hermes("cron", ["pause", job_id])
    return _cli_to_response(result)


@router.post("/api/cron/{job_id}/resume", response_model=ApiResponse)
async def resume_cron_job(job_id: str) -> ApiResponse:
    result = await run_hermes("cron", ["resume", job_id])
    return _cli_to_response(result)


@router.post("/api/cron/{job_id}/run", response_model=ApiResponse)
async def run_cron_job(job_id: str) -> ApiResponse:
    result = await run_hermes("cron", ["run", job_id])
    return _cli_to_response(result)


@router.get("/api/cron/status", response_model=ApiResponse)
async def cron_status() -> ApiResponse:
    result = await run_hermes("cron", ["status"])
    return _cli_to_response(result)
