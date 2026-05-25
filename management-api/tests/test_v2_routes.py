"""Smoke tests for the v2 router family.

Subprocess calls are mocked at the run_hermes layer — the goal here is to
verify route wiring, auth, validation, and that env_overrides include
HERMES_HOME. Behavioural testing of the actual CLI happens on the VPS.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes_mgmt.models import CliResponse


def _ok(stdout: str = "ok", exit_code: int = 0) -> CliResponse:
    return CliResponse(stdout=stdout, stderr="", exit_code=exit_code)


@pytest.fixture
def mock_run():
    """Patch run_hermes used by v2 routes' shared run_for helper."""
    with patch(
        "hermes_mgmt.routes.v2._base.run_hermes",
        new=AsyncMock(return_value=_ok()),
    ) as mock:
        yield mock


class TestAuthGate:
    """Every v2 endpoint requires Bearer auth — unauth → 401."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/v2/config/show"),
            ("POST", "/api/v2/config/set"),
            ("GET", "/api/v2/auth"),
            ("POST", "/api/v2/sessions/prune"),
            ("GET", "/api/v2/gateway/status"),
            ("POST", "/api/v2/curator/run"),
            ("GET", "/api/v2/diagnostics/status"),
        ],
    )
    def test_requires_auth(self, client: TestClient, method: str, path: str) -> None:
        resp = client.request(method, path, json={})
        assert resp.status_code == 401


class TestConfig:
    def test_show(self, client: TestClient, auth_headers: dict, mock_run) -> None:
        resp = client.get("/api/v2/config/show", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["data"]["stdout"] == "ok"
        # HERMES_HOME passed via env_overrides
        _, kwargs = mock_run.call_args
        assert kwargs["env_overrides"]["HERMES_HOME"].endswith("/.hermes")

    def test_set(self, client: TestClient, auth_headers: dict, mock_run) -> None:
        resp = client.post(
            "/api/v2/config/set",
            headers=auth_headers,
            json={"key": "model.default", "value": "claude-sonnet-4-6"},
        )
        assert resp.status_code == 200
        # Args reached the CLI verbatim
        args = mock_run.call_args.args
        assert args[1] == ["set", "model.default", "claude-sonnet-4-6"]

    def test_set_invalid_key(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.post(
            "/api/v2/config/set",
            headers=auth_headers,
            json={"key": "bad key with space", "value": "x"},
        )
        assert resp.status_code == 422


class TestFallback:
    def test_add_with_model(
        self, client: TestClient, auth_headers: dict, mock_run
    ) -> None:
        resp = client.post(
            "/api/v2/fallback",
            headers=auth_headers,
            json={"provider": "deepseek", "model": "deepseek-chat"},
        )
        assert resp.status_code == 200
        assert mock_run.call_args.args[1] == ["add", "deepseek", "deepseek-chat"]

    def test_clear(self, client: TestClient, auth_headers: dict, mock_run) -> None:
        resp = client.delete("/api/v2/fallback", headers=auth_headers)
        assert resp.status_code == 200
        assert mock_run.call_args.args[1] == ["clear"]


class TestAuth:
    def test_add_api_key(
        self, client: TestClient, auth_headers: dict, mock_run
    ) -> None:
        resp = client.post(
            "/api/v2/auth/anthropic/api-key",
            headers=auth_headers,
            json={"api_key": "sk-ant-xxxxx"},
        )
        assert resp.status_code == 200
        assert mock_run.call_args.args[1] == [
            "add",
            "anthropic",
            "--api-key",
            "sk-ant-xxxxx",
        ]

    def test_remove_by_index(
        self, client: TestClient, auth_headers: dict, mock_run
    ) -> None:
        resp = client.delete("/api/v2/auth/anthropic/0", headers=auth_headers)
        assert resp.status_code == 200
        assert mock_run.call_args.args[1] == ["remove", "anthropic", "0"]

    def test_provider_validation(self, client: TestClient, auth_headers: dict) -> None:
        resp = client.get("/api/v2/auth/BAD-Provider", headers=auth_headers)
        assert resp.status_code == 422


class TestSessions:
    def test_list(self, client: TestClient, auth_headers: dict, mock_run) -> None:
        resp = client.get("/api/v2/sessions", headers=auth_headers)
        assert resp.status_code == 200

    def test_export_rejects_path_traversal(
        self, client: TestClient, auth_headers: dict, mock_run
    ) -> None:
        resp = client.post(
            "/api/v2/sessions/export",
            headers=auth_headers,
            json={"output": "../../etc/passwd"},
        )
        assert resp.status_code == 422


class TestCron:
    def test_create_assembles_args(
        self, client: TestClient, auth_headers: dict, mock_run
    ) -> None:
        resp = client.post(
            "/api/v2/cron",
            headers=auth_headers,
            json={"spec": "0 * * * *", "prompt": "summarize", "name": "hourly"},
        )
        assert resp.status_code == 200
        assert mock_run.call_args.args[1] == [
            "create",
            "--spec",
            "0 * * * *",
            "--prompt",
            "summarize",
            "--name",
            "hourly",
        ]

    def test_edit_requires_at_least_one_field(
        self, client: TestClient, auth_headers: dict, mock_run
    ) -> None:
        resp = client.patch(
            "/api/v2/cron/job-1", headers=auth_headers, json={}
        )
        assert resp.status_code == 422


class TestKanban:
    def test_create_task(
        self, client: TestClient, auth_headers: dict, mock_run
    ) -> None:
        resp = client.post(
            "/api/v2/kanban/tasks",
            headers=auth_headers,
            json={"title": "T1", "assignee": "alice", "skill": "research"},
        )
        assert resp.status_code == 200
        assert mock_run.call_args.args[1] == [
            "create",
            "T1",
            "--assignee",
            "alice",
            "--skill",
            "research",
        ]


class TestDiagnostics:
    def test_status_passes_flags(
        self, client: TestClient, auth_headers: dict, mock_run
    ) -> None:
        resp = client.get(
            "/api/v2/diagnostics/status?all=true&deep=true",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert mock_run.call_args.args[1] == ["--all", "--deep"]

    def test_doctor_does_not_500_on_nonzero(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        with patch(
            "hermes_mgmt.routes.v2._base.run_hermes",
            new=AsyncMock(return_value=CliResponse(stdout="issues", stderr="", exit_code=1)),
        ):
            resp = client.post("/api/v2/diagnostics/doctor", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["data"]["exit_code"] == 1

    def test_logs_rejects_bad_name(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        resp = client.get(
            "/api/v2/diagnostics/logs?name=../../etc/passwd",
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestRouterRegistration:
    def test_all_v2_namespaces_registered(self, client: TestClient) -> None:
        """Verify every namespace router actually got mounted."""
        from hermes_mgmt.routes.v2 import all_v2_routers

        expected_prefixes = {
            "/api/v2/config",
            "/api/v2/model",
            "/api/v2/fallback",
            "/api/v2/auth",
            "/api/v2/sessions",
            "/api/v2/memory",
            "/api/v2/skills",
            "/api/v2/bundles",
            "/api/v2/tools",
            "/api/v2/webhook",
            "/api/v2/gateway",
            "/api/v2/cron",
            "/api/v2/kanban",
            "/api/v2/curator",
            "/api/v2/profile",
            "/api/v2/diagnostics",
        }
        seen = {r.prefix for r in all_v2_routers if r.prefix}
        missing = expected_prefixes - seen
        assert not missing, f"namespaces missing: {missing}"
