"""Exercise the application's CORS stack without starting its database lifespan."""

import importlib
import sys

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.config import settings


@pytest.fixture
def cors_client(monkeypatch):
    # Older test modules insert a partial security stub into sys.modules during
    # collection. Load the real module only for importing the actual application;
    # monkeypatch restores the previous entry after this test.
    security = sys.modules.get("app.core.security")
    if security is not None and getattr(security, "__spec__", None) is None:
        monkeypatch.delitem(sys.modules, "app.core.security")
        real_security = importlib.import_module("app.core.security")
        monkeypatch.setitem(sys.modules, "app.core.security", real_security)
    from app.main import app

    cors = next(item for item in app.user_middleware if item.cls is CORSMiddleware)
    origins = cors.kwargs["allow_origins"]
    assert origins == settings.cors_origin_list
    assert origins and "*" not in origins, "Configure explicit frontend origins"
    # Requests below do not need a database. Avoid opening the configured DB even
    # when FastAPI resolves dependencies for an invalid login request.
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: None)
    # No context manager: deliberately do not run startup schema/demo-data jobs.
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, origins[0], app
    finally:
        client.close()


def test_cors_headers_are_present_on_success_and_422(cors_client):
    client, origin, _ = cors_client
    headers = {"Origin": origin}
    success = client.get("/health", headers=headers)
    assert success.status_code == 200
    assert success.headers.get("access-control-allow-origin") == origin

    validation = client.post(
        f"{settings.api_prefix}/auth/login", json={}, headers=headers
    )
    assert validation.status_code == 422
    assert validation.headers.get("access-control-allow-origin") == origin


def test_cors_does_not_allow_an_unconfigured_origin(cors_client):
    client, _, _ = cors_client
    origin = "https://unconfigured-frontend.invalid"
    assert origin not in settings.cors_origin_list
    response = client.get("/health", headers={"Origin": origin})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_headers_are_present_on_500_error_response(cors_client, monkeypatch):
    client, origin, app = cors_client
    health_route = next(route for route in app.routes if route.path == "/health")

    def fail_health():
        raise RuntimeError("test error")

    # Inject a failure into an existing route; retain the real middleware and
    # error handlers. pytest restores the endpoint after this test.
    monkeypatch.setattr(health_route.dependant, "call", fail_health)
    response = client.get("/health", headers={"Origin": origin})
    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == origin
