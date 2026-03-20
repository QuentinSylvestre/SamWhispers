"""Tests for whisper server client."""

from __future__ import annotations

import httpx
import pytest
import respx

from samwhispers.transcribe import WhisperClient


@pytest.fixture()
def client() -> WhisperClient:
    return WhisperClient("http://localhost:8080", language="en")


@respx.mock
def test_transcribe_success(client: WhisperClient) -> None:
    """Successful transcription returns text."""
    respx.post("http://localhost:8080/inference").mock(
        return_value=httpx.Response(200, json={"text": "hello world"})
    )
    assert client.transcribe(b"fake-wav") == "hello world"


@respx.mock
def test_transcribe_empty_response(client: WhisperClient) -> None:
    """Empty text field returns empty string."""
    respx.post("http://localhost:8080/inference").mock(
        return_value=httpx.Response(200, json={"text": ""})
    )
    assert client.transcribe(b"fake-wav") == ""


@respx.mock
def test_transcribe_server_error(client: WhisperClient) -> None:
    """500 error after retries raises HTTPStatusError."""
    respx.post("http://localhost:8080/inference").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.transcribe(b"fake-wav")


@respx.mock
def test_transcribe_retry_on_503(client: WhisperClient) -> None:
    """503 is retried, succeeds on second attempt."""
    route = respx.post("http://localhost:8080/inference")
    route.side_effect = [
        httpx.Response(503, text="Service Unavailable"),
        httpx.Response(200, json={"text": "retried ok"}),
    ]
    assert client.transcribe(b"fake-wav") == "retried ok"
    assert route.call_count == 2


@respx.mock
def test_transcribe_connection_error(client: WhisperClient) -> None:
    """Connection error after retries raises."""
    respx.post("http://localhost:8080/inference").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(httpx.ConnectError):
        client.transcribe(b"fake-wav")


@respx.mock
def test_health_check_ok(client: WhisperClient) -> None:
    """Health check returns True on 200."""
    respx.get("http://localhost:8080/").mock(return_value=httpx.Response(200, text="<html>"))
    assert client.health_check() is True


@respx.mock
def test_health_check_down(client: WhisperClient) -> None:
    """Health check returns False on connection error."""
    respx.get("http://localhost:8080/").mock(side_effect=httpx.ConnectError("refused"))
    assert client.health_check() is False
