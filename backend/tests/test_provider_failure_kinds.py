"""Verify app.llm.provider maps real httpx failures to the correct typed
exception (LLMQuotaExceededError / LLMTimeoutError / LLMServerError /
generic LLMProviderError), since app.llm.interpreter's failure-specific
retry strategy depends entirely on that mapping being precise. No real
network calls: uses httpx.MockTransport to control the response/exception
each request receives.
"""

import httpx
import pytest

from app.llm.provider import (
    LLMProviderError,
    LLMQuotaExceededError,
    LLMServerError,
    LLMTimeoutError,
    OpenAICompatibleProvider,
)


@pytest.fixture
def patch_httpx_post(monkeypatch):
    """Monkeypatch app.llm.provider.httpx.post to route through a given
    httpx.MockTransport, for one test."""

    def apply(transport: httpx.MockTransport):
        client = httpx.Client(transport=transport)

        def fake_post(url, headers=None, json=None, timeout=None):
            return client.post(url, headers=headers, json=json, timeout=timeout)

        import app.llm.provider as provider_module

        monkeypatch.setattr(provider_module.httpx, "post", fake_post)

    return apply


def _provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(base_url="https://example.test/v1", api_key="key", model="m")


def test_429_maps_to_quota_exceeded(patch_httpx_post):
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    patch_httpx_post(httpx.MockTransport(handler))
    with pytest.raises(LLMQuotaExceededError):
        _provider().complete("sys", "user")


def test_insufficient_credit_402_maps_to_quota_exceeded(patch_httpx_post):
    def handler(request):
        return httpx.Response(402, json={"error": {"message": "Insufficient credits, please top up"}})

    patch_httpx_post(httpx.MockTransport(handler))
    with pytest.raises(LLMQuotaExceededError):
        _provider().complete("sys", "user")


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_5xx_maps_to_server_error(patch_httpx_post, status_code):
    def handler(request):
        return httpx.Response(status_code, json={"error": {"message": "upstream failure"}})

    patch_httpx_post(httpx.MockTransport(handler))
    with pytest.raises(LLMServerError):
        _provider().complete("sys", "user")


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_other_4xx_maps_to_generic_provider_error_not_server_error(patch_httpx_post, status_code):
    def handler(request):
        return httpx.Response(status_code, json={"error": {"message": "bad request"}})

    patch_httpx_post(httpx.MockTransport(handler))
    with pytest.raises(LLMProviderError) as excinfo:
        _provider().complete("sys", "user")
    assert not isinstance(excinfo.value, LLMServerError)
    assert not isinstance(excinfo.value, LLMQuotaExceededError)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectTimeout("connect timed out"),
        httpx.ReadTimeout("read timed out"),
        httpx.WriteTimeout("write timed out"),
        httpx.PoolTimeout("pool timed out"),
    ],
)
def test_timeout_variants_map_to_timeout_error(patch_httpx_post, exc):
    def handler(request):
        raise exc

    patch_httpx_post(httpx.MockTransport(handler))
    with pytest.raises(LLMTimeoutError):
        _provider().complete("sys", "user")


def test_connection_error_maps_to_generic_provider_error_not_timeout(patch_httpx_post):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    patch_httpx_post(httpx.MockTransport(handler))
    with pytest.raises(LLMProviderError) as excinfo:
        _provider().complete("sys", "user")
    assert not isinstance(excinfo.value, LLMTimeoutError)


def test_successful_response_returns_content(patch_httpx_post):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    patch_httpx_post(httpx.MockTransport(handler))
    assert _provider().complete("sys", "user") == "pong"
