import importlib.util
import io
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4
import wave

import pytest


spec = importlib.util.spec_from_file_location("speech_client", Path(__file__).with_name("run.py"))
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
TOKEN = "a" * 43


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x01\x00" * 2400)
    return output.getvalue()


@pytest.fixture
def server(tmp_path):
    state = {
        "body": {"status": "succeeded", "audio_asset_id": str(uuid4()), "format": "wav", "duration_s": .1, "error": None},
        "audio": wav_bytes(), "requests": [], "post_status": 200,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            state["requests"].append((self.path, self.headers["Authorization"], json.loads(payload)))
            self.send_response(state["post_status"])
            self.send_header("Content-Type", "application/json")
            if state["post_status"] == 302:
                self.send_header("Location", state["redirect"])
            self.end_headers()
            self.wfile.write(json.dumps(state["body"]).encode())

        def do_GET(self):
            state["requests"].append((self.path, self.headers["Authorization"]))
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.end_headers()
            self.wfile.write(state["audio"])

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    token = tmp_path / "session.token"
    token.write_text(TOKEN, encoding="utf-8")
    state.update(port=instance.server_port, token=token, output=tmp_path / "speech.wav")
    try:
        yield state
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def generate(state):
    return client.generate("漂泊者，今天的海面很平静。", state["output"], state["token"], state["port"])


def test_generate_saves_identical_wav_and_uses_bearer(server, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    assert generate(server) == server["body"]
    assert server["output"].read_bytes() == server["audio"]
    assert server["requests"] == [
        ("/api/v1/speech", "Bearer " + TOKEN, {"text": "漂泊者，今天的海面很平静。", "voice_profile_version": client.PROFILE}),
        ("/api/v1/audio/" + server["body"]["audio_asset_id"], "Bearer " + TOKEN),
    ]
    assert list(server["output"].parent.glob(".speech-*.tmp")) == []


@pytest.mark.parametrize("changes", [
    {"audio_asset_id": "../../private"}, {"audio_asset_id": 4}, {"duration_s": float("nan")},
    {"duration_s": 0}, {"duration_s": True}, {"status": "failed"}, {"format": "mp3"},
])
def test_invalid_success_is_rejected_without_download(server, changes):
    server["body"].update(changes)
    with pytest.raises(client.ClientError):
        generate(server)
    assert len(server["requests"]) == 1
    assert not server["output"].exists()


@pytest.mark.parametrize("audio", [wav_bytes()[:-2], b"not a wav", wav_bytes().replace(b"\x01\x00" * 2400, b"\x00" * 4800)])
def test_invalid_audio_keeps_existing_output_and_removes_temporary(server, audio):
    server["audio"] = audio
    server["output"].write_bytes(b"existing")
    with pytest.raises(client.ClientError):
        generate(server)
    assert server["output"].read_bytes() == b"existing"
    assert list(server["output"].parent.glob(".speech-*.tmp")) == []


def test_no_redirect_is_followed(server):
    server.update(post_status=302, redirect=f"http://127.0.0.1:{server['port']}/leak")
    with pytest.raises(client.ClientError, match="HTTP 302"):
        generate(server)
    assert len(server["requests"]) == 1


def test_error_does_not_echo_service_body(server):
    server.update(post_status=500, body={"private": "secret text " + TOKEN})
    with pytest.raises(client.ClientError) as exc:
        generate(server)
    assert str(exc.value) == "Local service returned HTTP 500; speech was not saved."


def test_does_not_create_a_missing_token(tmp_path):
    token = tmp_path / "session.token"
    with pytest.raises(client.ClientError, match="existing service token"):
        client.generate("你好", tmp_path / "out.wav", token)
    assert not token.exists()


def test_default_token_belongs_to_backend_checkout():
    assert client.DEFAULT_TOKEN_FILE == Path(__file__).resolve().parents[2] / "local/runtime/session.token"
