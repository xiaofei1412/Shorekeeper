"""Controlled stdio worker for CPU-only lifecycle and protocol tests."""
import argparse
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import wave


parser = argparse.ArgumentParser()
parser.add_argument("--config", type=Path)
parser.add_argument("--session-dir", type=Path)
args = parser.parse_args()
config = json.loads(args.config.read_text(encoding="utf-8"))
mode = Path(config["source_root"]).name


def emit(message):
    print(json.dumps(message), flush=True)


if mode == "startup_timeout":
    time.sleep(60)
if mode == "delayed_ready":
    time.sleep(0.3)
emit({"type": "ready", "profile_version": "shorekeeper-r1-v1", "pid": os.getpid(), "load_count": 1})
if mode == "dead_idle":
    time.sleep(0.2)
    sys.exit(3)
if mode == "no_read":
    time.sleep(60)

for line in sys.stdin:
    request = json.loads(line)
    text = request["text"]
    if text == "slow":
        time.sleep(1)
    if text == "dead":
        sys.exit(4)
    if text in {"budget", "internal"}:
        emit({"type": "error", "request_id": request["request_id"],
              "code": "BUDGET_EXCEEDED" if text == "budget" else "INTERNAL"})
        continue
    if text == "bad_json":
        print("upstream log is not protocol", flush=True)
        continue
    path = args.session_dir / (request["output_name"] + ".part")
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, 22050 if text == "wrong_format" else 24000, 0, "NONE", "not compressed"))
        handle.writeframes(b"".join(struct.pack("<h", 0 if text == "silence" else int(1000 * math.sin(i / 10))) for i in range(2400)))
    if text == "truncated":
        path.write_bytes(path.read_bytes()[:-4])
    message = {"type": "result", "request_id": request["request_id"], "seed": 20261117}
    if text == "wrong_id":
        message["request_id"] = "00000000-0000-4000-8000-000000000000"
    if text == "extra_path":
        message["path"] = str(path)
    emit(message)
    if text == "duplicate":
        emit(message)
