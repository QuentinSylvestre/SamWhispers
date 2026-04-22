"""Tests for whisper server client."""

from __future__ import annotations

import httpx
import pytest
import respx

from samwhispers.exceptions import ShutdownRequested
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


@respx.mock
def test_language_switch_between_requests() -> None:
    """Language property change is reflected in the next request."""
    client = WhisperClient("http://localhost:8080", language="en")
    assert client.language == "en"

    route = respx.post("http://localhost:8080/inference").mock(
        return_value=httpx.Response(200, json={"text": "ok"})
    )

    client.transcribe(b"wav1")
    assert route.calls[0].request.content  # request was made

    client.language = "fr"
    assert client.language == "fr"

    client.transcribe(b"wav2")
    # Verify the second request used "fr" in the form data
    body = route.calls[1].request.content.decode()
    assert "fr" in body


@respx.mock
def test_retry_exits_early_on_shutdown_event() -> None:
    """Retry sleep is interrupted when shutdown event is set."""
    import threading

    event = threading.Event()
    event.set()  # pre-set so the first retry sleep exits immediately

    client = WhisperClient("http://localhost:8080", language="en", shutdown_event=event)

    respx.post("http://localhost:8080/inference").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )
    with pytest.raises(ShutdownRequested, match="Shutdown requested"):
        client.transcribe(b"fake-wav")
