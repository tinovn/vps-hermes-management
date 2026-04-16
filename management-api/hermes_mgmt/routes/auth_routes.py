from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse

from hermes_mgmt.auth import (
    create_session_token,
    hash_password,
    verify_password,
)
from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_rate_limiter, get_settings_dep, rate_limit_login, require_auth
from hermes_mgmt.env_file import delete_env, read_env, set_env
from hermes_mgmt.models import (
    ApiResponse,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserCreateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

_SESSION_COOKIE = "session"
_COOKIE_MAX_AGE = 86400 * 7  # 7 days

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hermes Management - Login</title>
  <style>
    body { font-family: sans-serif; display: flex; justify-content: center;
           align-items: center; min-height: 100vh; margin: 0; background: #f5f5f5; }
    .card { background: white; padding: 2rem; border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1); width: 100%; max-width: 360px; }
    h1 { margin: 0 0 1.5rem; font-size: 1.4rem; color: #333; }
    label { display: block; margin-bottom: 0.25rem; font-size: 0.9rem; color: #555; }
    input { width: 100%; padding: 0.6rem; margin-bottom: 1rem; border: 1px solid #ccc;
            border-radius: 4px; box-sizing: border-box; font-size: 1rem; }
    button { width: 100%; padding: 0.7rem; background: #2563eb; color: white;
             border: none; border-radius: 4px; font-size: 1rem; cursor: pointer; }
    button:hover { background: #1d4ed8; }
    .error { color: #dc2626; font-size: 0.9rem; margin-bottom: 1rem; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Hermes Management</h1>
    <form method="POST" action="/api/auth/login">
      <label for="username">Username</label>
      <input id="username" name="username" type="text" required autocomplete="username">
      <label for="password">Password</label>
      <input id="password" name="password" type="password" required autocomplete="current-password">
      <button type="submit">Sign in</button>
    </form>
  </div>
</body>
</html>"""


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page() -> HTMLResponse:
    return HTMLResponse(content=_LOGIN_HTML)


@router.post(
    "/api/auth/login",
    response_model=ApiResponse,
    dependencies=[Depends(rate_limit_login)],
)
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    env = read_env(settings.env_file)
    stored_user = env.get("HERMES_LOGIN_USER", "")
    stored_hash = env.get("HERMES_LOGIN_HASH", "")

    limiter = get_rate_limiter()

    if not stored_user or not stored_hash:
        limiter.record_failure(request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No user configured. Use POST /api/auth/create-user first.",
        )

    if body.username != stored_user or not verify_password(body.password, stored_hash):
        limiter.record_failure(request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    limiter.clear(request)
    token, expires_at = create_session_token(body.username, settings.session_secret)

    response.set_cookie(
        key=_SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=_COOKIE_MAX_AGE,
        path="/",
    )

    return ApiResponse(
        ok=True,
        data=LoginResponse(token=token, expires_at=expires_at).model_dump(),
    )


@router.post(
    "/api/auth/create-user",
    response_model=ApiResponse,
    dependencies=[Depends(require_auth)],
)
async def create_user(
    body: UserCreateRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    hashed = hash_password(body.password)
    set_env(settings.env_file, "HERMES_LOGIN_USER", body.username)
    set_env(settings.env_file, "HERMES_LOGIN_HASH", hashed)
    return ApiResponse(ok=True, data={"username": body.username})


@router.get(
    "/api/auth/user",
    response_model=ApiResponse,
    dependencies=[Depends(require_auth)],
)
async def get_user(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    env = read_env(settings.env_file)
    username = env.get("HERMES_LOGIN_USER", "")
    return ApiResponse(ok=True, data={"username": username})


@router.put(
    "/api/auth/change-password",
    response_model=ApiResponse,
    dependencies=[Depends(require_auth)],
)
async def change_password(
    body: ChangePasswordRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    env = read_env(settings.env_file)
    stored_hash = env.get("HERMES_LOGIN_HASH", "")
    if not verify_password(body.old_password, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Old password is incorrect.",
        )
    new_hash = hash_password(body.new_password)
    set_env(settings.env_file, "HERMES_LOGIN_HASH", new_hash)
    return ApiResponse(ok=True, data={"changed": True})


@router.delete(
    "/api/auth/user",
    response_model=ApiResponse,
    dependencies=[Depends(require_auth)],
)
async def delete_user(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    delete_env(settings.env_file, "HERMES_LOGIN_USER")
    delete_env(settings.env_file, "HERMES_LOGIN_HASH")
    return ApiResponse(ok=True, data={"deleted": True})


@router.post("/api/auth/logout", response_model=ApiResponse)
async def logout(response: Response) -> ApiResponse:
    response.delete_cookie(key=_SESSION_COOKIE, path="/")
    return ApiResponse(ok=True, data={"logged_out": True})
