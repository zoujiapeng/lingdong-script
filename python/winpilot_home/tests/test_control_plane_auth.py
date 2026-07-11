from __future__ import annotations

import pytest


def test_control_plane_requires_bearer_token_when_configured(monkeypatch):
    from fastapi.testclient import TestClient
    from winpilot import server

    monkeypatch.setattr(server, "CONTROL_TOKEN", "test-control-token")
    with TestClient(server.app) as client:
        assert client.get("/api/ping").status_code == 200
        assert client.get("/api/config").status_code == 401
        response = client.get(
            "/api/config",
            headers={"Authorization": "Bearer test-control-token"},
        )
        assert response.status_code == 200


def test_control_plane_allows_valid_renderer_cors_preflight(monkeypatch):
    from fastapi.testclient import TestClient
    from winpilot import server

    monkeypatch.setattr(server, "CONTROL_TOKEN", "test-control-token")
    with TestClient(server.app) as client:
        response = client.options(
            "/api/v2/platform",
            headers={
                "Origin": "http://localhost:5200",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5200"
        assert "authorization" in response.headers["access-control-allow-headers"].lower()

        rejected = client.options(
            "/api/v2/platform",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert rejected.status_code == 400
        assert "access-control-allow-origin" not in rejected.headers


def test_control_plane_websocket_rejects_wrong_token(monkeypatch):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from winpilot import server

    monkeypatch.setattr(server, "CONTROL_TOKEN", "test-control-token")
    with TestClient(server.app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/events?token=wrong"):
                pass
        assert exc.value.code == 4401
