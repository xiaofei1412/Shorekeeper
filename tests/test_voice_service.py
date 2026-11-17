from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid1, uuid4

from fastapi.testclient import TestClient
import pytest

from shorekeeper.app import create_app
from shorekeeper.contracts import SpeechRequest
from shorekeeper.voice.service import PROFILE, SpeechFailure, VoiceService


TOKEN = "b" * 43
AUTH = {"Authorization": "Bearer " + TOKEN}
FAKE = Path(__file__).with_name("fake_voice_worker.py")


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for controlled worker")


@contextmanager
def running(tmp_path, mode="normal", *, startup_timeout=2, generation_timeout=2):
    config = tmp_path / (mode + ".json")
    config.write_text(json.dumps({
        "voice_python": sys.executable, "source_root": str(tmp_path / mode),
        "runtime_root": "runtime", "model_manifest": "model.json", "cache_root": "cache",
        "output_root": str(tmp_path / "tts"), "log_root": str(tmp_path / "logs"),
    }), encoding="utf-8")
    service = VoiceService(config, startup_timeout=startup_timeout,
                           generation_timeout=generation_timeout, worker_script=FAKE)
    with TestClient(create_app(TOKEN, service=service)) as client:
        yield client, service


def speech(client, text="hello", profile=PROFILE):
    return client.post("/api/v1/speech", json={"text": text, "voice_profile_version": profile}, headers=AUTH)


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert TOKEN not in response.text
    assert "source_root" not in response.text
    assert "hello" not in response.text


def test_loading_is_observable_and_health_responsive(tmp_path):
    with running(tmp_path, "delayed_ready") as (client, service):
        assert client.get("/api/v1/health").json()["status"] == "ok"
        assert client.get("/api/v1/status", headers=AUTH).json()["components"]["tts"] == "loading"
        assert_error(speech(client), 503, "DEPENDENCY_UNAVAILABLE")
        wait_for(lambda: service.state == "ready")
        assert client.get("/api/v1/status", headers=AUTH).json()["product_ready"] is False
    assert service.process.poll() is not None


def test_success_download_index_and_restart(tmp_path):
    with running(tmp_path) as (client, service):
        wait_for(lambda: service.state == "ready")
        pid = service.process.pid
        first = speech(client)
        assert first.status_code == 200
        body = first.json()
        identifier = body["audio_asset_id"]
        assert body == {"status": "succeeded", "audio_asset_id": identifier, "format": "wav", "duration_s": 0.1, "error": None}
        response = client.get("/api/v1/audio/" + identifier, headers=AUTH)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"] == "audio/wav"
        assert response.content == (service.session_dir / (identifier + ".wav")).read_bytes()
        assert speech(client, "second").status_code == 200
        assert service.process.pid == pid
        # A disk file is never implicitly added to the in-memory index.
        unregistered = uuid4()
        (service.session_dir / (str(unregistered) + ".wav")).write_bytes(response.content)
        assert_error(client.get("/api/v1/audio/" + str(unregistered), headers=AUTH), 404, "NOT_FOUND")
        assert_error(speech(client, profile="unknown"), 404, "NOT_FOUND")
    with running(tmp_path) as (client, service):
        wait_for(lambda: service.state == "ready")
        assert_error(client.get("/api/v1/audio/" + identifier, headers=AUTH), 404, "NOT_FOUND")


@pytest.mark.parametrize("path", ["/api/v1/audio", "/api/v1/audio/", "/api/v1/audio/no-uuid", "/api/v1/audio/no-uuid/nested"])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_whole_audio_prefix_is_authenticated(path, headers):
    with TestClient(create_app(TOKEN)) as client:
        assert_error(client.get(path, headers=headers), 401, "UNAUTHENTICATED")


@pytest.mark.parametrize("identifier", ["not-a-uuid", str(uuid1())])
def test_invalid_audio_uuid_is_generic_422(identifier):
    with TestClient(create_app(TOKEN)) as client:
        assert_error(client.get("/api/v1/audio/" + identifier, headers=AUTH), 422, "INVALID_ARGUMENT")


def test_busy_does_not_queue_or_block_health(tmp_path):
    with running(tmp_path) as (client, service):
        wait_for(lambda: service.state == "ready")
        with ThreadPoolExecutor() as executor:
            first = executor.submit(speech, client, "slow")
            wait_for(lambda: service._busy)
            started = time.monotonic()
            assert client.get("/api/v1/health").status_code == 200
            assert client.get("/api/v1/status", headers=AUTH).json()["components"]["tts"] == "ready"
            assert_error(speech(client), 429, "BUSY")
            assert time.monotonic() - started < 0.5
            assert first.result().status_code == 200
        assert len(service.assets) == 1


def test_budget_rejection_keeps_worker_ready(tmp_path):
    with running(tmp_path) as (client, service):
        wait_for(lambda: service.state == "ready")
        assert_error(speech(client, "budget"), 409, "BUDGET_EXCEEDED")
        assert service.state == "ready"
        assert not service.assets
        assert speech(client).status_code == 200


def test_generation_timeout_discards_results_and_does_not_restart(tmp_path):
    with running(tmp_path, generation_timeout=0.05) as (client, service):
        wait_for(lambda: service.state == "ready")
        pid = service.process.pid
        with ThreadPoolExecutor() as executor:
            pending = executor.submit(speech, client, "slow")
            wait_for(lambda: service._request_id is not None)
            request_id = service._request_id
            assert_error(pending.result(), 504, "TIMEOUT")
        assert service.state == "failed"
        assert_error(speech(client), 503, "DEPENDENCY_UNAVAILABLE")
        wait_for(lambda: service.process.poll() is not None)
        # Inject a formerly valid packet after the deadline; it cannot resurrect a result.
        client.portal.call(service._message, {"type": "result", "request_id": request_id, "seed": 20261117})
        assert service.process.pid == pid
        assert service.state == "failed"
        assert not service.assets


def test_startup_timeout_preserves_health(tmp_path):
    with running(tmp_path, "startup_timeout", startup_timeout=0.05) as (client, service):
        wait_for(lambda: service.state == "failed")
        assert client.get("/api/v1/health").status_code == 200
        assert_error(speech(client), 503, "DEPENDENCY_UNAVAILABLE")
        wait_for(lambda: service.process.poll() is not None)


def test_idle_worker_death_is_observed(tmp_path):
    with running(tmp_path, "dead_idle") as (client, service):
        wait_for(lambda: service.state == "ready")
        wait_for(lambda: service.state == "failed")
        assert_error(speech(client), 503, "DEPENDENCY_UNAVAILABLE")


@pytest.mark.parametrize("text", ["dead", "internal", "wrong_id", "extra_path", "bad_json", "duplicate", "wrong_format", "silence", "truncated"])
def test_failure_never_registers_or_returns_fake_success(tmp_path, text):
    with running(tmp_path) as (client, service):
        wait_for(lambda: service.state == "ready")
        assert_error(speech(client, text), 500, "INTERNAL")
        assert service.state == "failed"
        assert not service.assets
        assert_error(speech(client), 503, "DEPENDENCY_UNAVAILABLE")


def test_invalid_explicit_configuration_fails_without_breaking_health(tmp_path):
    config = tmp_path / "invalid.json"
    config.write_text('{"unexpected": true}', encoding="utf-8")
    service = VoiceService(config)
    with TestClient(create_app(TOKEN, service=service)) as client:
        wait_for(lambda: service.state == "failed")
        assert client.get("/api/v1/health").status_code == 200
        assert_error(speech(client), 503, "DEPENDENCY_UNAVAILABLE")


def test_stalled_pipe_obeys_deadline_without_blocking_health(tmp_path):
    with running(tmp_path, "no_read", generation_timeout=0.2) as (client, service):
        wait_for(lambda: service.state == "ready")
        with ThreadPoolExecutor() as executor:
            pending = executor.submit(speech, client, "😀" * 1000)
            wait_for(lambda: service._busy)
            assert client.get("/api/v1/health").status_code == 200
            assert_error(pending.result(), 504, "TIMEOUT")
        assert not service.assets


def test_cancelled_http_caller_does_not_release_generation_slot(tmp_path):
    # Reuse the real route coroutine to cancel its caller without cancelling the worker task.
    config = tmp_path / "normal.json"
    config.write_text(json.dumps({
        "voice_python": sys.executable, "source_root": str(tmp_path / "normal"),
        "runtime_root": "runtime", "model_manifest": "model.json", "cache_root": "cache",
        "output_root": str(tmp_path / "tts"), "log_root": str(tmp_path / "logs"),
    }), encoding="utf-8")

    async def scenario():
        service = VoiceService(config, worker_script=FAKE)
        app = create_app(TOKEN, service=service)
        route = next(route.endpoint for route in app.routes if route.path == "/api/v1/speech")
        async with app.router.lifespan_context(app):
            while service.state == "loading":
                await asyncio.sleep(0.01)
            assert service.state == "ready"
            caller = asyncio.create_task(route(SpeechRequest(text="slow", voice_profile_version=PROFILE), None))
            while not service._busy:
                await asyncio.sleep(0.01)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            with pytest.raises(SpeechFailure) as failure:
                await service.synthesize("second", PROFILE)
            assert failure.value.code == "BUSY"
            while service._busy:
                await asyncio.sleep(0.01)
            assert len(service.assets) == 1
            assert service.state == "ready"

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "nt", reason="Windows virtualenv redirector process ownership")
def test_windows_worker_tree_is_stopped_even_after_launcher_exit(tmp_path):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    with running(tmp_path) as (client, service):
        wait_for(lambda: service.state == "ready")
        handle = kernel.OpenProcess(0x00100000, False, service.worker_pid)
        assert handle
        assert kernel.WaitForSingleObject(handle, 0) == 258
        if service.worker_pid != service.process.pid:
            service.process.terminate()
            service.process.wait(timeout=2)
    try:
        assert kernel.WaitForSingleObject(handle, 2000) == 0
    finally:
        kernel.CloseHandle(handle)
