"""v2 kanban endpoints — wrap `hermes kanban <action>`.

CLI surface (tasks + boards):
    hermes kanban init                        -> POST   /api/v2/kanban/init
    hermes kanban list                        -> GET    /api/v2/kanban/tasks
    hermes kanban show <id>                   -> GET    /api/v2/kanban/tasks/{task_id}
    hermes kanban create "<title>" [...]      -> POST   /api/v2/kanban/tasks
    hermes kanban assign <id> <profile>       -> POST   /api/v2/kanban/tasks/{task_id}/assign
    hermes kanban complete <id>               -> POST   /api/v2/kanban/tasks/{task_id}/complete
    hermes kanban block <id> "<reason>"       -> POST   /api/v2/kanban/tasks/{task_id}/block
    hermes kanban unblock <id>                -> POST   /api/v2/kanban/tasks/{task_id}/unblock
    hermes kanban boards create <slug>        -> POST   /api/v2/kanban/boards
    hermes kanban boards switch <slug>        -> POST   /api/v2/kanban/boards/{slug}/switch
    hermes kanban boards rename <slug> "<n>"  -> POST   /api/v2/kanban/boards/{slug}/rename
    hermes kanban boards rm <slug>            -> DELETE /api/v2/kanban/boards/{slug}
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.routes.v2._base import cli_payload, raise_for_exit_code, run_for

router = APIRouter(
    prefix="/api/v2/kanban",
    tags=["v2:kanban"],
    dependencies=[Depends(require_auth)],
)


_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _check_id(s: str, label: str = "id") -> None:
    if not _ID_RE.match(s):
        raise HTTPException(
            status_code=422,
            detail=f"{label} must match ^[A-Za-z0-9_.-]{{1,64}}$",
        )


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    body: str | None = Field(default=None, max_length=8192)
    assignee: str | None = Field(default=None, max_length=64)
    skill: str | None = Field(default=None, max_length=128)


class AssignRequest(BaseModel):
    profile: str = Field(min_length=1, max_length=64)


class BlockRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=512)


class BoardCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=64)


class BoardRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


# ----- core -----
@router.post("/init", response_model=ApiResponse)
async def init(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "kanban", ["init"])
    raise_for_exit_code(result, "hermes kanban init failed")
    return ApiResponse(ok=True, data=cli_payload(result))


# ----- tasks -----
@router.get("/tasks", response_model=ApiResponse)
async def list_tasks(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    result = await run_for(settings, "kanban", ["list"])
    raise_for_exit_code(result, "hermes kanban list failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.get("/tasks/{task_id}", response_model=ApiResponse)
async def show_task(
    task_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(task_id, "task_id")
    result = await run_for(settings, "kanban", ["show", task_id])
    raise_for_exit_code(result, f"hermes kanban show {task_id} failed")
    return ApiResponse(ok=True, data={"task_id": task_id, **cli_payload(result)})


@router.post("/tasks", response_model=ApiResponse)
async def create_task(
    body: TaskCreateRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    args = ["create", body.title]
    if body.body:
        args.extend(["--body", body.body])
    if body.assignee:
        args.extend(["--assignee", body.assignee])
    if body.skill:
        args.extend(["--skill", body.skill])
    result = await run_for(settings, "kanban", args)
    raise_for_exit_code(result, "hermes kanban create failed")
    return ApiResponse(ok=True, data=cli_payload(result))


@router.post("/tasks/{task_id}/assign", response_model=ApiResponse)
async def assign(
    task_id: str,
    body: AssignRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(task_id, "task_id")
    result = await run_for(settings, "kanban", ["assign", task_id, body.profile])
    raise_for_exit_code(result, f"hermes kanban assign {task_id} failed")
    return ApiResponse(
        ok=True,
        data={"task_id": task_id, "profile": body.profile, **cli_payload(result)},
    )


@router.post("/tasks/{task_id}/complete", response_model=ApiResponse)
async def complete(
    task_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(task_id, "task_id")
    result = await run_for(settings, "kanban", ["complete", task_id])
    raise_for_exit_code(result, f"hermes kanban complete {task_id} failed")
    return ApiResponse(ok=True, data={"task_id": task_id, **cli_payload(result)})


@router.post("/tasks/{task_id}/block", response_model=ApiResponse)
async def block(
    task_id: str,
    body: BlockRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(task_id, "task_id")
    result = await run_for(settings, "kanban", ["block", task_id, body.reason])
    raise_for_exit_code(result, f"hermes kanban block {task_id} failed")
    return ApiResponse(ok=True, data={"task_id": task_id, **cli_payload(result)})


@router.post("/tasks/{task_id}/unblock", response_model=ApiResponse)
async def unblock(
    task_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(task_id, "task_id")
    result = await run_for(settings, "kanban", ["unblock", task_id])
    raise_for_exit_code(result, f"hermes kanban unblock {task_id} failed")
    return ApiResponse(ok=True, data={"task_id": task_id, **cli_payload(result)})


# ----- boards -----
@router.post("/boards", response_model=ApiResponse)
async def create_board(
    body: BoardCreateRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(body.slug, "slug")
    result = await run_for(settings, "kanban", ["boards", "create", body.slug])
    raise_for_exit_code(result, f"hermes kanban boards create {body.slug} failed")
    return ApiResponse(ok=True, data={"slug": body.slug, **cli_payload(result)})


@router.post("/boards/{slug}/switch", response_model=ApiResponse)
async def switch_board(
    slug: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(slug, "slug")
    result = await run_for(settings, "kanban", ["boards", "switch", slug])
    raise_for_exit_code(result, f"hermes kanban boards switch {slug} failed")
    return ApiResponse(ok=True, data={"slug": slug, **cli_payload(result)})


@router.post("/boards/{slug}/rename", response_model=ApiResponse)
async def rename_board(
    slug: str,
    body: BoardRenameRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(slug, "slug")
    result = await run_for(
        settings, "kanban", ["boards", "rename", slug, body.name]
    )
    raise_for_exit_code(result, f"hermes kanban boards rename {slug} failed")
    return ApiResponse(
        ok=True, data={"slug": slug, "name": body.name, **cli_payload(result)}
    )


@router.delete("/boards/{slug}", response_model=ApiResponse)
async def remove_board(
    slug: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    _check_id(slug, "slug")
    result = await run_for(settings, "kanban", ["boards", "rm", slug])
    raise_for_exit_code(result, f"hermes kanban boards rm {slug} failed")
    return ApiResponse(ok=True, data={"slug": slug, **cli_payload(result)})
