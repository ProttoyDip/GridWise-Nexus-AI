"""Tests for app-level wiring in app.main: root route, CORS middleware."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_route_returns_service_info():
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "docs" in body


def test_cors_preflight_allows_configured_origin(monkeypatch):
    response = client.options(
        "/optimize-energy",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code in (200, 204)
    assert response.headers.get("access-control-allow-origin") == "*"


def test_cors_allows_get_endpoints_cross_origin():
    response = client.get("/health", headers={"Origin": "https://example.com"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "*"
