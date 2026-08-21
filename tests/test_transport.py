"""Tests for the streamable-HTTP bearer-auth middleware and its config guard."""

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mygekko_mcp.config import Settings
from mygekko_mcp.transport import BearerAuthMiddleware


def _app(token: str) -> Starlette:
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/mcp", ok)])
    app.add_middleware(BearerAuthMiddleware, token=token)
    return app


def test_missing_authorization_is_401():
    client = TestClient(_app("secret"))
    assert client.get("/mcp").status_code == 401


def test_wrong_token_is_401():
    client = TestClient(_app("secret"))
    r = client.get("/mcp", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_wrong_scheme_is_401():
    client = TestClient(_app("secret"))
    r = client.get("/mcp", headers={"Authorization": "Basic secret"})
    assert r.status_code == 401


def test_correct_token_passes_through():
    client = TestClient(_app("secret"))
    r = client.get("/mcp", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200 and r.text == "ok"


def _settings(**over) -> Settings:
    base = dict(username="u@example.com", key="k", gekkoid="AAAA-BBBB-CCCC-DDDD")
    base.update(over)
    return Settings(**base)


def test_http_requires_token_by_default():
    problems = _settings().validate_http_ready()
    assert problems and "GEKKO_HTTP_TOKEN" in problems[0]


def test_http_ready_with_token():
    assert _settings(http_token="abc123").validate_http_ready() == []


def test_http_ready_with_explicit_no_auth_optin():
    assert _settings(http_allow_no_auth=True).validate_http_ready() == []
