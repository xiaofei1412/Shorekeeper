"""Generate and save speech through the authenticated local M05B1 service."""
import argparse
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID
import wave


PROFILE = "shorekeeper-r1-v1"
DEFAULT_TOKEN_FILE = Path(__file__).resolve().parents[2] / "local/runtime/session.token"


class ClientError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_wav(path, expected_duration):
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 24000, "NONE"):
            raise ClientError("Invalid audio response.")
        frame_count = wav.getnframes()
        if frame_count <= 0 or abs(frame_count / 24000 - expected_duration) > 1 / 24000:
            raise ClientError("Invalid audio duration.")
        remaining = frame_count * 2
        signal = False
        while remaining:
            data = wav.readframes(min(24000, remaining // 2))
            if not data:
                raise ClientError("Incomplete audio response.")
            remaining -= len(data)
            signal = signal or any(data)
        if not signal:
            raise ClientError("Audio response has no signal.")


def generate(text, output, token_file=DEFAULT_TOKEN_FILE, port=8765):
    if not 1 <= port <= 65535 or not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
        raise ClientError("Invalid port or text length.")
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise ClientError("Cannot read the existing service token file.") from None
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise ClientError("Invalid service token file.")
    # Keep the bearer token on loopback, including when proxy environment variables exist.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    base = f"http://127.0.0.1:{port}/api/v1"
    headers = {"Authorization": "Bearer " + token}
    request = Request(base + "/speech", data=json.dumps({
        "text": text, "voice_profile_version": PROFILE,
    }, ensure_ascii=False).encode("utf-8"), headers=headers | {"Content-Type": "application/json"})
    temporary = None
    try:
        with opener.open(request, timeout=190) as response:
            if response.status != 200:
                raise ClientError("Unexpected speech response.")
            payload = response.read(65537)
            if len(payload) > 65536:
                raise ClientError("Invalid speech response.")
            body = json.loads(payload)
        if (not isinstance(body, dict)
                or set(body) != {"status", "audio_asset_id", "format", "duration_s", "error"}
                or body["status"] != "succeeded" or body["format"] != "wav" or body["error"] is not None):
            raise ClientError("Invalid speech response.")
        if not isinstance(body["audio_asset_id"], str):
            raise ClientError("Invalid audio identifier.")
        asset_id = UUID(body["audio_asset_id"])
        if asset_id.version != 4:
            raise ClientError("Invalid audio identifier.")
        duration = body["duration_s"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise ClientError("Invalid audio duration.")
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with opener.open(Request(base + "/audio/" + str(asset_id), headers=headers), timeout=30) as response:
            if response.status != 200 or response.headers.get_content_type() != "audio/wav":
                raise ClientError("Invalid audio response.")
            with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".speech-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                while chunk := response.read(65536):
                    handle.write(chunk)
        validate_wav(temporary, duration)
        os.replace(temporary, output)
        temporary = None
        return body
    except HTTPError as exc:
        exc.close()
        raise ClientError(f"Local service returned HTTP {exc.code}; speech was not saved.") from None
    except (OSError, URLError, HTTPException, ValueError, wave.Error, EOFError):
        raise ClientError("Cannot generate, validate or save speech through the local service.") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                raise ClientError("Cannot remove an incomplete local download.") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--play", action="store_true")
    args = parser.parse_args()
    try:
        result = generate(args.text, args.output, args.token_file, args.port)
    except ClientError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"生成/保存成功。audio_asset_id={result['audio_asset_id']} duration_s={result['duration_s']:.3f}", flush=True)
    if args.play:
        try:
            import winsound
            winsound.PlaySound(str(args.output.resolve()), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
        except (ImportError, OSError, RuntimeError):
            print("WAV已保存；本机播放调用失败。", file=sys.stderr)
            return 1
        print("播放调用结束。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
