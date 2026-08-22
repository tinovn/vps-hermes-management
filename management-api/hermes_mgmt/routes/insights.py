from __future__ import annotations

import logging
import sqlite3
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.models import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["insights"], dependencies=[Depends(require_auth)])

_SESSIONS_SQL = (
    "SELECT id, coalesce(title,'') AS title, coalesce(source,'') AS source, "
    "coalesce(model,'') AS model, coalesce(message_count,0) AS messages, "
    "coalesce(api_call_count,0) AS api_calls, coalesce(tool_call_count,0) AS tool_calls, "
    "coalesce(input_tokens,0) AS input_tokens, "
    "coalesce(cache_read_tokens,0) AS cache_read_tokens, "
    "coalesce(output_tokens,0) AS output_tokens, "
    "coalesce(reasoning_tokens,0) AS reasoning_tokens, "
    "started_at, ended_at "
    "FROM sessions WHERE started_at >= ? "
    "ORDER BY (input_tokens+cache_read_tokens+output_tokens+reasoning_tokens) DESC "
    "LIMIT ?"
)


@router.get("/api/insights/sessions", response_model=ApiResponse)
async def sessions_tokens(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    days: int = Query(1, ge=1, le=90, description="Rolling window in days."),
    limit: int = Query(200, ge=1, le=2000, description="Max sessions returned."),
    include_empty: bool = Query(False, description="Include sessions with 0 tokens."),
) -> ApiResponse:
    """Per-session token usage over the last ``days`` days (rolling window).

    Reads the core session store (state.db) READ-ONLY. Token fields:
    input (fresh), cache_read, output, reasoning; total = their sum.
    Ordered by total tokens desc.
    """
    db = settings.hermes_home / "state.db"
    if not db.exists():
        return ApiResponse(ok=False, error=f"session store not found: {db}")
    cutoff = time.time() - days * 86400
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(_SESSIONS_SQL, (cutoff, limit)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("insights sessions query failed: %s", exc)
        return ApiResponse(ok=False, error=f"session store query failed: {exc}")

    sessions: list[dict] = []
    tot = {"input": 0, "cache_read": 0, "output": 0, "reasoning": 0, "total": 0}
    for r in rows:
        total = (
            r["input_tokens"] + r["cache_read_tokens"]
            + r["output_tokens"] + r["reasoning_tokens"]
        )
        if total == 0 and not include_empty:
            continue
        tot["input"] += r["input_tokens"]
        tot["cache_read"] += r["cache_read_tokens"]
        tot["output"] += r["output_tokens"]
        tot["reasoning"] += r["reasoning_tokens"]
        tot["total"] += total
        sessions.append(
            {
                "id": r["id"],
                "title": r["title"],
                "source": r["source"],
                "model": r["model"],
                "messages": r["messages"],
                "api_calls": r["api_calls"],
                "tool_calls": r["tool_calls"],
                "input_tokens": r["input_tokens"],
                "cache_read_tokens": r["cache_read_tokens"],
                "output_tokens": r["output_tokens"],
                "reasoning_tokens": r["reasoning_tokens"],
                "total_tokens": total,
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
            }
        )
    return ApiResponse(
        ok=True,
        data={"days": days, "count": len(sessions), "totals": tot, "sessions": sessions},
    )
