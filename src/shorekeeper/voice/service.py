"""One owned, resident worker and a session-local audio index."""
import asyncio
from array import array
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import struct
import threading
from uuid import UUID, uuid4
import wave


PROFILE = "shorekeeper-r1-v1"
CONFIG_FIELDS = {
    "voice_python", "source_root", "runtime_root", "model_manifest", "cache_root",
    "output_root", "log_root",
}


class SpeechFailure(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AudioAsset:
    path: Path
    duration_s: float


def load_config(path: Path) -> dict[str, Path]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != CONFIG_FIELDS:
        raise ValueError("Invalid TTS configuration")
    if any(not isinstance(value, str) or not value.strip() for value in data.values()):
        raise ValueError("Invalid TTS configuration paths")
    return {key: (path.parent / value).resolve() for key, value in data.items()}


def validate_wav(path: Path) -> float:
    """Require a complete PCM16 signal, not just a readable WAV header."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing controlled output")
    with path.open("rb") as stream:
        header = stream.read(12)
    if (len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE"
            or struct.unpack("<I", header[4:8])[0] + 8 != path.stat().st_size):
        raise ValueError("Incomplete WAV container")
    with wave.open(str(path), "rb") as handle:
        if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth(), handle.getcomptype()) != (24000, 1, 2, "NONE"):
            raise ValueError("Invalid output format")
        count = handle.getnframes()
        if count <= 0:
            raise ValueError("Empty output")
        remaining = count
        energy = 0
        while remaining:
            chunk_frames = min(remaining, 24000)
            data = handle.readframes(chunk_frames)
            if len(data) != chunk_frames * 2:
                raise ValueError("Incomplete output")
            samples = array("h", data)
            if os.sys.byteorder != "little":
                samples.byteswap()
            energy += sum(sample * sample for sample in samples)
            remaining -= chunk_frames
        if energy == 0 or handle.readframes(1):
            raise ValueError("Invalid output signal")
        return count / 24000


class VoiceService:
    def __init__(
        self, config_path: Path | None = None, *, startup_timeout: float = 120,
        generation_timeout: float = 180, worker_script: Path | None = None,
    ):
        self.config_path = config_path.resolve() if config_path is not None else None
        self.startup_timeout = startup_timeout
        self.generation_timeout = generation_timeout
        self.worker_script = worker_script or Path(__file__).resolve().parents[3] / "tools/voice_worker/run.py"
        self.state = "not_configured" if config_path is None else "loading"
        self.session_id = uuid4()
        self.session_dir: Path | None = None
        self.process: subprocess.Popen | None = None
        self.worker_pid: int | None = None
        self.assets: dict[UUID, AudioAsset] = {}
        self._busy = False
        self._closed = False
        self._startup_task: asyncio.Task | None = None
        self._ready: asyncio.Future | None = None
        self._pending: asyncio.Future | None = None
        self._request_id: str | None = None
        self._stderr = None
        self._reader: threading.Thread | None = None
        self._stop_task: asyncio.Task | None = None
        self._job = None

    def start(self) -> None:
        if self.config_path is not None:
            self._startup_task = asyncio.create_task(self._start())

    async def _start(self) -> None:
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        try:
            config = await asyncio.to_thread(load_config, self.config_path)
            self.session_dir = config["output_root"] / str(self.session_id)
            self.session_dir.mkdir(parents=True, exist_ok=False)
            config["log_root"].mkdir(parents=True, exist_ok=True)
            self._stderr = (config["log_root"] / (str(self.session_id) + "-worker.stderr.log")).open("xb")
            env = os.environ.copy()
            env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            if os.name == "nt":
                from shorekeeper.voice.windows_job import WorkerJob
                self._job = WorkerJob()
            self.process = subprocess.Popen(
                [str(config["voice_python"]), str(self.worker_script), "--config", str(self.config_path),
                 "--session-dir", str(self.session_dir)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr, env=env,
                creationflags=self._job.creation_flags if self._job is not None else 0,
            )
            if self._job is not None:
                self._job.assign_and_resume(self.process)
            self._reader = threading.Thread(target=self._read_stdout, args=(loop,), daemon=True)
            self._reader.start()
            await asyncio.wait_for(asyncio.shield(self._ready), self.startup_timeout)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._fail(SpeechFailure(503, "DEPENDENCY_UNAVAILABLE"))
            if self._ready.done() and not self._ready.cancelled():
                self._ready.exception()

    def _read_stdout(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            while True:
                line = self.process.stdout.readline(65537)
                if not line or len(line) > 65536 or not line.endswith(b"\n"):
                    break
                try:
                    # Duplicate JSON keys are a protocol error, not last-key-wins.
                    def unique(pairs):
                        result = {}
                        for key, value in pairs:
                            if key in result:
                                raise ValueError("Duplicate protocol key")
                            result[key] = value
                        return result
                    message = json.loads(line.decode("utf-8"), object_pairs_hook=unique)
                except (UnicodeError, ValueError):
                    break
                loop.call_soon_threadsafe(self._message, message)
        except (OSError, ValueError):
            pass
        finally:
            try:
                loop.call_soon_threadsafe(self._fail, SpeechFailure(500, "INTERNAL"))
            except RuntimeError:
                pass

    def _message(self, message) -> None:
        if self._closed or self.state == "failed":
            return
        try:
            if not isinstance(message, dict):
                raise ValueError("Invalid protocol")
            if self.state == "loading":
                if (set(message) != {"type", "profile_version", "pid", "load_count"}
                        or message["type"] != "ready" or message["profile_version"] != PROFILE
                        or type(message["pid"]) is not int
                        or not (self._job.contains(message["pid"]) if self._job is not None else message["pid"] == self.process.pid)
                        or type(message["load_count"]) is not int or message["load_count"] != 1):
                    raise ValueError("Invalid ready")
                self.state = "ready"
                self.worker_pid = message["pid"]
                self._ready.set_result(None)
                return
            if (self._pending is None or self._pending.done()
                    or message.get("request_id") != self._request_id):
                raise ValueError("Unexpected result")
            if message.get("type") == "result":
                if (set(message) != {"type", "request_id", "seed"}
                        or type(message["seed"]) is not int or not 0 <= message["seed"] < 2**32):
                    raise ValueError("Invalid result")
                self._pending.set_result(None)
            elif (message.get("type") == "error" and set(message) == {"type", "request_id", "code"}
                    and message["code"] == "BUDGET_EXCEEDED"):
                self._pending.set_exception(SpeechFailure(409, "BUDGET_EXCEEDED"))
            else:
                raise ValueError("Worker error or invalid protocol")
        except (ValueError, KeyError):
            self._fail(SpeechFailure(500, "INTERNAL"))

    def _fail(self, failure: SpeechFailure) -> None:
        if self._closed or self.state == "failed":
            return
        self.state = "failed"
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(SpeechFailure(503, "DEPENDENCY_UNAVAILABLE"))
        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(failure)
        if self.process is not None:
            self._stop_task = asyncio.create_task(asyncio.to_thread(self._stop_process))

    async def synthesize(self, text: str, profile: str) -> tuple[UUID, AudioAsset]:
        if self._closed or self.state != "ready":
            raise SpeechFailure(503, "DEPENDENCY_UNAVAILABLE")
        if profile != PROFILE:
            raise SpeechFailure(404, "NOT_FOUND")
        if self._busy:
            raise SpeechFailure(429, "BUSY")
        # No await occurs before claiming the one slot: requests never queue.
        self._busy = True
        request_id = str(uuid4())
        asset_id = uuid4()
        name = str(asset_id) + ".wav"
        temporary = self.session_dir / (name + ".part")
        output = self.session_dir / name
        self._request_id = request_id
        self._pending = asyncio.get_running_loop().create_future()
        try:
            payload = {"type": "synthesize", "request_id": request_id, "text": text, "output_name": name}

            async def exchange():
                # Even a stalled pipe must leave health/status responsive and obey the deadline.
                await asyncio.to_thread(self._send, payload)
                await asyncio.shield(self._pending)

            try:
                await asyncio.wait_for(exchange(), self.generation_timeout)
            except TimeoutError:
                failure = SpeechFailure(504, "TIMEOUT")
                self._fail(failure)
                # Consume the pending exception set by _fail after shield timed out.
                self._pending.exception()
                raise failure
            duration = await asyncio.to_thread(validate_wav, temporary)
            if self.state != "ready" or self.process.poll() is not None:
                failure = SpeechFailure(500, "INTERNAL")
                self._fail(failure)
                raise failure
            temporary.replace(output)
            asset = AudioAsset(output, duration)
            self.assets[asset_id] = asset
            return asset_id, asset
        except SpeechFailure:
            raise
        except asyncio.CancelledError:
            # A disconnected HTTP caller does not authorize worker cancellation.
            raise
        except Exception:
            failure = SpeechFailure(500, "INTERNAL")
            self._fail(failure)
            if self._pending.done() and not self._pending.cancelled():
                self._pending.exception()
            raise failure from None
        finally:
            self._pending = None
            self._request_id = None
            self._busy = False

    def _send(self, payload: dict) -> None:
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        self.process.stdin.flush()

    async def close(self) -> None:
        self._closed = True
        if self._startup_task is not None:
            self._startup_task.cancel()
            await asyncio.gather(self._startup_task, return_exceptions=True)
        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(SpeechFailure(503, "DEPENDENCY_UNAVAILABLE"))
        if self._stop_task is not None:
            await self._stop_task
        else:
            await asyncio.to_thread(self._stop_process)
        if self._stderr is not None:
            self._stderr.close()

    def _stop_process(self) -> None:
        if self._job is not None:
            self._job.terminate()
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    self.process.terminate()
                except OSError:
                    pass
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            if self.process.stdin is not None:
                try:
                    self.process.stdin.close()
                except OSError:
                    # A timed-out pipe can retain buffered bytes after its reader was killed.
                    pass
            if self._reader is not None:
                self._reader.join(timeout=5)
            if self.process.stdout is not None:
                self.process.stdout.close()
        if self._job is not None:
            self._job.close()
