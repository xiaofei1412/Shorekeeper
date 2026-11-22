"""Start the built Windows workbench and own its local speech service."""

import http.client
from importlib.util import find_spec
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"
POLL_INTERVAL = 0.25
BIND_TIMEOUT = 30
STARTUP_TIMEOUT = 150
SHUTDOWN_TIMEOUT = 8


class LaunchError(Exception):
    pass


def load_desktop_config(path):
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise LaunchError(f"无法读取桌面配置 {path}。请按工作台 README 准备私有 JSON。") from None
    if not isinstance(config, dict):
        raise LaunchError("桌面配置必须是含 port 和 token_file 的 JSON 对象。")

    port = config.get("port")
    if type(port) not in (int, float) or not 1 <= port <= 65535 or port != int(port):
        raise LaunchError("桌面配置的 port 必须是 1–65535 之间的整数。")
    token_file = config.get("token_file")
    if (not isinstance(token_file, str) or not token_file
            or re.search(r'^[\\/]|[<>:"|?*\x00-\x1f]', token_file)
            or ".." in re.split(r"[\\/]", token_file)):
        raise LaunchError("桌面配置的 token_file 必须是配置目录内的相对文件路径。")
    token_path = path.parent / token_file
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        raise LaunchError("会话令牌文件不可读。请按 README 准备后端既有令牌并核对 token_file。") from None
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise LaunchError("会话令牌格式无效。请核对 token_file 是否指向后端既有令牌。")
    return int(port), token_path, token


def start_owned(job, args, cwd, env, log):
    # Keep the backend in this console so Ctrl+Break can reach its group.
    # Assign while suspended so the venv redirector cannot spawn outside the job.
    flags = (job.creation_flags & ~subprocess.CREATE_NO_WINDOW) | subprocess.CREATE_NEW_PROCESS_GROUP
    with log.open("xb") as output:
        process = subprocess.Popen(
            args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, creationflags=flags,
        )
    try:
        job.assign_and_resume(process)
    except OSError:
        process.kill()
        process.wait()
        raise
    return process


def tts_state(port, token):
    connection = http.client.HTTPConnection(HOST, port, timeout=0.5)
    try:
        connection.request("GET", "/api/v1/status", headers={"Authorization": f"Bearer {token}"})
        response = connection.getresponse()
        if response.status == 401:
            raise LaunchError("后端会话鉴权失败。请核对桌面配置和既有令牌文件。")
        if response.status == 200:
            return json.loads(response.read(64 * 1024))["components"]["tts"]
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
        return None
    finally:
        connection.close()


def run(job, python, desktop, electron, config, tts_config, port, token_path, token, logs):
    backend_log = logs / "backend.log"
    backend = None
    workbench = None
    interrupted = False

    def interrupt(_signum, _frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGBREAK, interrupt)
    try:
        backend = start_owned(job, [
            python, "-B", "-u", "-X", "utf8", "-m", "shorekeeper", "--port", str(port),
            "--tts-config", tts_config, "--token-file", token_path,
        ], ROOT, {**os.environ, "PYTHONPATH": str(ROOT / "src")}, backend_log)

        bind_deadline = time.monotonic() + BIND_TIMEOUT
        while not interrupted:
            exited = backend.poll() is not None
            # The official entry point emits this line only after its exclusive bind
            # and token load. Read this child's output before contacting the port.
            with backend_log.open("rb") as stream:
                output = stream.read(64 * 1024)
            if exited:
                if f"Cannot listen on {HOST}:{port};".encode("ascii") in output:
                    raise LaunchError(
                        f"后端无法绑定 {HOST}:{port}，端口已占用或不可用。"
                        "若已有语音服务，请按工作台 README 使用双终端方式连接。"
                    )
                raise LaunchError("后端在启动时退出。请检查 backend.log 和核心 Python 环境。")
            if b"Local session token file:" in output:
                break
            if time.monotonic() >= bind_deadline:
                raise LaunchError("等待后端绑定端口超时。请检查 backend.log。")
            time.sleep(POLL_INTERVAL)

        if interrupted:
            return 130
        electron_env = {**os.environ, "SHOREKEEPER_DESKTOP_CONFIG": str(config)}
        electron_env.pop("ELECTRON_RUN_AS_NODE", None)
        workbench = start_owned(job, [electron, "."], desktop, electron_env, logs / "workbench.log")
        startup_deadline = time.monotonic() + STARTUP_TIMEOUT
        ready = False
        while not interrupted:
            exit_code = workbench.poll()
            if exit_code is not None:
                if exit_code:
                    raise LaunchError(f"工作台异常退出（{exit_code}）。请检查 workbench.log 和本地构建。")
                return 0
            if backend.poll() is not None:
                raise LaunchError("语音后端提前退出。请检查 backend.log。")
            if not ready:
                state = tts_state(port, token)
                if state in ("failed", "not_configured"):
                    raise LaunchError("语音服务启动失败。请检查 TTS 配置和其中 log_root 下的日志。")
                ready = state == "ready"
                if not ready and time.monotonic() >= startup_deadline:
                    raise LaunchError("语音服务启动超时。请检查 backend.log 和 TTS 日志。")
            time.sleep(POLL_INTERVAL)
        return 130
    finally:
        try:
            if backend is not None and backend.poll() is None:
                try:
                    backend.send_signal(signal.CTRL_BREAK_EVENT)
                    backend.wait(timeout=SHUTDOWN_TIMEOUT)
                except (OSError, subprocess.TimeoutExpired):
                    pass  # The job below reaps the remaining processes.
        finally:
            job.terminate()
            for process in (backend, workbench):
                if process is not None:
                    process.wait(timeout=5)


def main():
    logs = None
    try:
        if sys.platform != "win32" or sys.version_info[:2] != (3, 12):
            raise LaunchError("请使用 Windows 核心 Python 3.12 环境运行启动器。")
        if any(find_spec(package) is None for package in ("fastapi", "pydantic", "uvicorn")):
            raise LaunchError("核心依赖不完整。请在项目根目录运行 uv sync --locked。")

        desktop = ROOT / "apps" / "desktop"
        electron = desktop / "node_modules" / "electron" / "dist" / "electron.exe"
        if not electron.is_file():
            raise LaunchError(
                "缺少 Electron。请在 apps/desktop 依次运行 npm.cmd ci、"
                "npx.cmd --no install-electron 和 npm.cmd run build。"
            )
        for required in ("dist/electron/main.js", "dist/electron/preload.cjs", "dist/ui/index.html"):
            if not (desktop / required).is_file():
                raise LaunchError(f"缺少工作台构建文件 {required}。请在 apps/desktop 运行 npm.cmd run build。")

        runtime = ROOT / "local" / "runtime"
        config = (ROOT / os.environ.get("SHOREKEEPER_DESKTOP_CONFIG", runtime / "desktop-config.json")).resolve()
        tts_config = (ROOT / os.environ.get("SHOREKEEPER_TTS_CONFIG", runtime / "tts-config.json")).resolve()
        port, token_path, token = load_desktop_config(config)
        if not tts_config.is_file():
            raise LaunchError(f"缺少 TTS 配置 {tts_config}。请按 README 指定既有 M05B1 私有配置。")

        log_root = runtime / "desktop-launcher"
        log_root.mkdir(parents=True, exist_ok=True)
        logs = Path(tempfile.mkdtemp(prefix="run-", dir=log_root))
        print(f"启动守岸人工作台，端口 {port}。日志目录 {logs}", flush=True)

        sys.path.insert(0, str(ROOT / "src"))
        from shorekeeper.voice.windows_job import WorkerJob

        job = WorkerJob()
        try:
            return run(job, sys.executable, desktop, electron, config, tts_config, port, token_path, token, logs)
        finally:
            job.close()
    except (LaunchError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        if logs is not None:
            print(f"本次日志目录 {logs}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
