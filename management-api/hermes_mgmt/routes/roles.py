"""Agent roles & rule policies — pick a role from the dashboard, apply its rules.

The agent can play many roles (CSKH, sales, marketing, receptionist, custom...).
Each role = persona + tone + a list of rule groups to enforce. Rule groups live
as markdown files in config/rules/*.md; roles as yaml in config/roles/*.yaml.

Applying a role builds a system prompt = persona + the text of every enabled
rule group, writes it to HERMES_HOME/persona.md, points config.yaml at it, and
restarts the gateway so the bot adopts the role immediately.

Layout (under HERMES_TEMPLATES_DIR's parent repo, falling back to bundled):
  config/rules/<group>.md     one file per rule group (GUI toggles these)
  config/roles/<id>.yaml      preset roles (read-only)
  HERMES_HOME/roles/<id>.yaml custom roles created via API (writable)
  HERMES_HOME/persona.md      the built system prompt the bot uses
  HERMES_HOME/active_role.json {"id": "...", "applied_at": "..."}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, status

from hermes_mgmt.config import Settings
from hermes_mgmt.deps import get_settings_dep, require_auth
from hermes_mgmt.env_file import read_env
from hermes_mgmt.models import ApiResponse
from hermes_mgmt.systemd_ctl import restart

logger = logging.getLogger(__name__)

router = APIRouter(tags=["roles"], dependencies=[Depends(require_auth)])

# Repo config dir (rules + preset roles ship here). Resolve relative to this
# file: hermes_mgmt/routes/roles.py -> repo root is 3 parents up, but on a VPS
# install mgmt lives at /opt/hermes-mgmt while config ships at /opt/hermes-mgmt
# too (download list) — try a few known locations.
_CONFIG_CANDIDATES = [
    Path("/opt/hermes-mgmt/config"),
    Path(__file__).resolve().parents[2] / "config",  # repo checkout
    Path("/opt/hermes/config"),
]


def _config_dir() -> Path:
    for c in _CONFIG_CANDIDATES:
        if (c / "rules").is_dir():
            return c
    return _CONFIG_CANDIDATES[0]


def _rules_dir() -> Path:
    return _config_dir() / "rules"


def _preset_roles_dir() -> Path:
    return _config_dir() / "roles"


def _custom_roles_dir(settings: Settings) -> Path:
    d = settings.hermes_home / "roles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bot_persona_file(settings: Settings) -> Path:
    """Where the Zalo plugin reads the bot persona from.

    The plugin's _load_bot_persona() reads
    $ZALO_PERSONAL_SESSION_DIR/bot_persona.json (default /opt/data/zalo) with
    fields {name, self_intro, personality}. We write the assembled role text
    into `personality` so the bot actually adopts it.
    """
    merged = read_env(settings.env_file)
    merged.update(read_env(settings.hermes_home / ".env"))
    session_dir = merged.get("ZALO_PERSONAL_SESSION_DIR", "").strip() or "/opt/data/zalo"
    return Path(session_dir) / "bot_persona.json"


def _active_file(settings: Settings) -> Path:
    return settings.hermes_home / "active_role.json"


# ─── rule groups ─────────────────────────────────────────────────────────────


def _list_rule_groups() -> list[dict]:
    groups: list[dict] = []
    rdir = _rules_dir()
    if not rdir.is_dir():
        return groups
    for f in sorted(rdir.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        title = ""
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        groups.append({"id": f.stem, "title": title or f.stem, "body": text})
    return groups


@router.get("/api/rules", response_model=ApiResponse)
async def list_rules() -> ApiResponse:
    """List all rule groups (id + title + full markdown body)."""
    return ApiResponse(ok=True, data={"groups": _list_rule_groups()})


@router.get("/api/rules/{group_id}", response_model=ApiResponse)
async def get_rule(group_id: str) -> ApiResponse:
    f = _rules_dir() / f"{group_id}.md"
    if not f.exists():
        raise HTTPException(status_code=404, detail=f"Rule group '{group_id}' không tồn tại.")
    return ApiResponse(ok=True, data={"id": group_id, "body": f.read_text(encoding="utf-8")})


# ─── roles ───────────────────────────────────────────────────────────────────


def _load_role(settings: Settings, role_id: str) -> dict | None:
    """Custom role (HERMES_HOME) takes priority over preset (repo config)."""
    for base in (_custom_roles_dir(settings), _preset_roles_dir()):
        f = base / f"{role_id}.yaml"
        if f.exists():
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                data["id"] = data.get("id", role_id)
                data["source"] = "custom" if base != _preset_roles_dir() else "preset"
                return data
            except yaml.YAMLError:
                return None
    return None


def _all_roles(settings: Settings) -> list[dict]:
    seen: dict[str, dict] = {}
    # preset first, custom overrides by id
    for base, src in ((_preset_roles_dir(), "preset"), (_custom_roles_dir(settings), "custom")):
        if not base.is_dir():
            continue
        for f in sorted(base.glob("*.yaml")):
            try:
                d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            rid = d.get("id", f.stem)
            seen[rid] = {
                "id": rid,
                "label": d.get("label", rid),
                "description": d.get("description", ""),
                "emoji": d.get("emoji", ""),
                "rules": d.get("rules", []),
                "source": src,
            }
    return list(seen.values())


@router.get("/api/roles", response_model=ApiResponse)
async def list_roles(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """List all roles (preset + custom). Includes the active role id."""
    active = None
    af = _active_file(settings)
    if af.exists():
        try:
            active = json.loads(af.read_text(encoding="utf-8")).get("id")
        except (json.JSONDecodeError, OSError):
            active = None
    return ApiResponse(ok=True, data={"roles": _all_roles(settings), "active": active})


@router.get("/api/roles/active", response_model=ApiResponse)
async def active_role(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    af = _active_file(settings)
    if not af.exists():
        return ApiResponse(ok=True, data={"active": None})
    try:
        data = json.loads(af.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {"active": None}
    return ApiResponse(ok=True, data=data)


@router.get("/api/roles/{role_id}", response_model=ApiResponse)
async def get_role(
    role_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    role = _load_role(settings, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail=f"Role '{role_id}' không tồn tại.")
    return ApiResponse(ok=True, data=role)


@router.post("/api/roles", response_model=ApiResponse)
async def create_role(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    body: dict = Body(...),
) -> ApiResponse:
    """Create/update a custom role.

    Body: { id, label, description?, emoji?, tone?, persona, rules: [group_id...] }
    Custom roles are stored in HERMES_HOME/roles/<id>.yaml (presets are read-only).
    """
    rid = str(body.get("id", "")).strip().lower().replace(" ", "-")
    if not rid:
        raise HTTPException(status_code=400, detail="Thiếu 'id' cho role.")
    persona = str(body.get("persona", "")).strip()
    if not persona:
        raise HTTPException(status_code=400, detail="Thiếu 'persona' cho role.")

    valid_groups = {g["id"] for g in _list_rule_groups()}
    rules = [r for r in (body.get("rules") or []) if r in valid_groups]
    unknown = [r for r in (body.get("rules") or []) if r not in valid_groups]

    # Don't let a custom role shadow a preset id (avoid confusion).
    if (_preset_roles_dir() / f"{rid}.yaml").exists():
        raise HTTPException(
            status_code=409,
            detail=f"'{rid}' trùng role preset. Dùng id khác cho custom role.",
        )

    role = {
        "id": rid,
        "label": body.get("label", rid),
        "description": body.get("description", ""),
        "emoji": body.get("emoji", ""),
        "tone": body.get("tone", ""),
        "persona": persona,
        "rules": rules,
    }
    f = _custom_roles_dir(settings) / f"{rid}.yaml"
    f.write_text(yaml.safe_dump(role, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return ApiResponse(ok=True, data={"id": rid, "saved": True, "ignored_rules": unknown})


@router.delete("/api/roles/{role_id}", response_model=ApiResponse)
async def delete_role(
    role_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Delete a custom role. Presets cannot be deleted."""
    f = _custom_roles_dir(settings) / f"{role_id}.yaml"
    if not f.exists():
        if (_preset_roles_dir() / f"{role_id}.yaml").exists():
            raise HTTPException(status_code=403, detail="Không xoá được role preset.")
        raise HTTPException(status_code=404, detail=f"Role '{role_id}' không tồn tại.")
    f.unlink()
    return ApiResponse(ok=True, data={"id": role_id, "deleted": True})


# ─── apply ───────────────────────────────────────────────────────────────────


def _build_persona(settings: Settings, role: dict) -> dict:
    """Map a role to the Zalo plugin's persona fields {name, self_intro, personality}.

    The plugin uses each field differently:
      - name        : how the bot refers to itself ("trợ lý bán hàng của sếp")
      - self_intro  : the verbatim answer when someone asks "em là ai" — so it
                      MUST reflect the role, else the bot says the generic default
      - personality : the mandatory speaking style + mission + rules block
    """
    label = role.get("label", role.get("id"))

    # name: explicit role.name → else derived from label
    name = (role.get("name") or "").strip() or f"trợ lý {label} của sếp"

    # self_intro: explicit role.self_intro → else a role-aware default
    self_intro = (role.get("self_intro") or "").strip()
    if not self_intro:
        self_intro = f"Dạ em là {name} ạ. Em ở đây để hỗ trợ anh/chị về {label.lower()}."

    # personality: tone + persona + rule bodies (this is the behaviour block)
    parts: list[str] = []
    parts.append(f"VAI TRÒ: {label}.")
    if role.get("tone"):
        parts.append(f"Tông giọng: {role['tone']}")
    if role.get("persona"):
        parts.append(role["persona"].strip())

    rdir = _rules_dir()
    enabled = role.get("rules") or []
    if enabled:
        parts.append("QUY TẮC BẮT BUỘC TUÂN THỦ:")
        for gid in enabled:
            f = rdir / f"{gid}.md"
            if f.exists():
                lines = [
                    ln for ln in f.read_text(encoding="utf-8").splitlines()
                    if not ln.startswith("# ") and not ln.startswith("> ")
                ]
                parts.append("\n".join(lines).strip())
    parts.append(
        "Ưu tiên khi xung đột: an toàn tài khoản + pháp luật + đạo đức > "
        "yêu cầu tăng trưởng. Khi nghi ngờ vi phạm, từ chối lịch sự."
    )
    personality = "\n\n".join(p for p in parts if p).strip()
    return {"name": name, "self_intro": self_intro, "personality": personality}


@router.post("/api/roles/{role_id}/apply", response_model=ApiResponse)
async def apply_role(
    role_id: str,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ApiResponse:
    """Apply a role: build persona + rules, write it, restart gateway.

    Writes HERMES_HOME/persona.md and records active_role.json. The Zalo plugin
    reads the persona via its persona mechanism; we also restart the gateway so
    the new system prompt takes effect.
    """
    role = _load_role(settings, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail=f"Role '{role_id}' không tồn tại.")

    persona_fields = _build_persona(settings, role)

    # Write all three fields the plugin reads (name + self_intro + personality)
    # so the bot adopts the role's identity AND behaviour — not just tone.
    pf = _bot_persona_file(settings)
    pf.parent.mkdir(parents=True, exist_ok=True)
    persona_obj: dict = {}
    if pf.exists():
        try:
            persona_obj = json.loads(pf.read_text(encoding="utf-8"))
            if not isinstance(persona_obj, dict):
                persona_obj = {}
        except (json.JSONDecodeError, OSError):
            persona_obj = {}
    persona_obj.update(persona_fields)
    pf.write_text(json.dumps(persona_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    import datetime

    active = {
        "id": role_id,
        "label": role.get("label", role_id),
        "rules": role.get("rules", []),
        "applied_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _active_file(settings).write_text(json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _restart_gw() -> None:
        try:
            await restart("hermes-gateway", settings.allowed_services)
        except Exception as exc:
            logger.error("gateway restart after role apply failed: %s", exc)

    background_tasks.add_task(_restart_gw)
    return ApiResponse(
        ok=True,
        data={
            "id": role_id,
            "applied": True,
            "persona_file": str(pf),
            "rules": role.get("rules", []),
            "name": persona_fields["name"],
            "self_intro": persona_fields["self_intro"],
            "persona_preview": persona_fields["personality"][:400],
        },
    )
