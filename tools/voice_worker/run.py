"""Resident, offline, pinned R1 worker. Stdout is exclusively JSONL protocol."""
import argparse
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from uuid import UUID


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools/voice_demo"))
from download_model import sha256
from experiment import JOIN_POLICY, join_segments, sentence_ranges
from inputs import REFERENCES, resolve_reference


def audit(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=True, allow_nan=False),
          file=sys.stderr, flush=True)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def request_from_line(line):
    value = json.loads(line, object_pairs_hook=unique_object)
    if not isinstance(value, dict) or set(value) != {"type", "request_id", "text", "output_name"}:
        raise ValueError("Invalid protocol fields")
    identifier = UUID(value["request_id"])
    output_id = UUID(value["output_name"].removesuffix(".wav"))
    if (value["type"] != "synthesize" or identifier.version != 4 or output_id.version != 4
            or str(identifier) != value["request_id"]
            or str(output_id) + ".wav" != value["output_name"]
            or not isinstance(value["text"], str) or not 1 <= len(value["text"]) <= 1000
            or value["text"] != value["text"].strip()):
        raise ValueError("Invalid protocol request")
    return value


def budget_for(model_input, lock):
    text = int(model_input["text_len"].item())
    prompt = int(model_input["prompt_text_len"].item())
    speech = int(model_input["llm_prompt_speech_token_len"].item())
    result = {"C": lock["effective_context_tokens"],
              "I": lock["special_embeddings"] + text + prompt + speech,
              "R": lock["max_token_text_ratio"] * text, "S": lock["safety_margin_tokens"],
              "target_text_tokens": text, "prompt_text_tokens": prompt,
              "prompt_speech_tokens": speech, "special_embeddings": lock["special_embeddings"]}
    result["fits"] = result["I"] + result["R"] + result["S"] <= result["C"]
    return result


def validate_runtime(config):
    profile = json.loads((REPO / "configs/voice-profile.lock.json").read_text(encoding="utf-8"))
    lock_path = REPO / profile["runtime_lock"]
    if sha256(lock_path) != profile["runtime_lock_sha256"]:
        raise ValueError("Runtime lock hash mismatch")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    expected = REFERENCES["R1"]
    if (profile["profile_version"] != "shorekeeper-r1-v1" or profile["reference_id"] != "R1"
            or profile["reference_asset_id"] != expected["asset_id"]
            or profile["reference_sha256"] != expected["source_sha256"]
            or profile["transcript_id"] != expected["transcript_id"]
            or profile["reference_mode"] != "original48k"
            or profile["join_policy"] != JOIN_POLICY["name"]):
        raise ValueError("Profile/reference mismatch")
    if ".".join(map(str, sys.version_info[:3])) != lock["runtime"]["python"]:
        raise ValueError("Python differs from runtime lock")
    requirements = REPO / lock["runtime"]["requirements_lock"]
    if sha256(requirements) != lock["runtime"]["requirements_lock_sha256"]:
        raise ValueError("Requirements lock hash mismatch")
    normalize = lambda name: name.lower().replace("_", "-").replace(".", "-")
    packages = {normalize(d.metadata["Name"]): d.version for d in importlib.metadata.distributions()}
    pinned = dict(line.split("==") for line in requirements.read_text(encoding="utf-8").splitlines()
                  if line and not line.startswith("#"))
    if packages != {normalize(name): version for name, version in pinned.items()}:
        raise ValueError("Environment differs from pinned packages")
    runtime = Path(config["runtime_root"])
    code = runtime / "CosyVoice"
    for directory, revision in ((code, lock["source"]["revision"]),
                                (code / "third_party/Matcha-TTS", lock["source"]["matcha_tts_revision"])):
        options = dict(text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if (subprocess.check_output(["git", "-C", str(directory), "rev-parse", "HEAD"], **options).strip() != revision
                or subprocess.check_output(["git", "-C", str(directory), "status", "--porcelain"], **options).strip()):
            raise ValueError("Source revision or clean-state mismatch")
    manifest_path = Path(config["model_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (sha256(manifest_path) != lock["model"]["model_manifest_sha256"]
            or manifest["revision"] != lock["model"]["revision"]
            or manifest["files"] != lock["model"]["files"]):
        raise ValueError("Model manifest mismatch")
    model_dir = (runtime / "Fun-CosyVoice3-0.5B-2512").resolve()
    for item in manifest["files"]:
        path = (model_dir / item["path"]).resolve()
        if not path.is_relative_to(model_dir) or sha256(path) != item["sha256"]:
            raise ValueError("Model file hash mismatch")
    context = json.loads((model_dir / "CosyVoice-BlankEN/config.json").read_text(encoding="utf-8"))
    budget = lock["speech_lm_budget"]
    if (context["max_position_embeddings"] != budget["effective_context_tokens"]
            or budget["max_token_text_ratio"] != 20 or budget["special_embeddings"] != 2):
        raise ValueError("Speech LM budget mismatch")
    return profile, lock, code, model_dir


def main():
    # Duplicate the original pipe, then redirect fd 1 as well as Python stdout.
    # Native third-party writes therefore cannot corrupt protocol JSONL.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    emit = lambda value: protocol.write(json.dumps(value, allow_nan=False) + "\n")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--session-dir", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    # Core resolves local paths; worker independently resolves relative paths identically.
    for key in config:
        config[key] = str((args.config.resolve().parent / config[key]).resolve())
    cache = Path(config["cache_root"])
    for key, folder in {"HF_HOME": "huggingface", "MODELSCOPE_CACHE": "modelscope",
                        "MPLCONFIGDIR": "matplotlib", "NUMBA_CACHE_DIR": "numba",
                        "TEMP": "tmp", "TMP": "tmp"}.items():
        directory = cache / folder
        directory.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(directory)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONUTF8="1")
    started = time.perf_counter()
    profile, lock, code, model_dir = validate_runtime(config)
    sys.path[:0] = [str(code), str(code / "third_party/Matcha-TTS")]
    import numpy as np
    import soundfile as sf
    import torch
    from cosyvoice.cli.cosyvoice import CosyVoice3
    from resources import Resources
    # The selected low-level frontend/tts methods do not log input text.
    # Suppress upstream informational/debug messages; retain warnings on stderr.
    logging.disable(logging.INFO)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required; no fallback")
    torch.set_num_threads(profile["torch_threads"])
    reference = resolve_reference(Path(config["source_root"]), "R1", cache / "references", original=True)
    prompt = profile["prompt_prefix"] + reference["prompt_text"]
    loading = Resources()
    load_started = time.perf_counter()
    with loading:
        model = CosyVoice3(str(model_dir), load_trt=False, load_vllm=False, fp16=False)
        torch.cuda.synchronize()
    if model.sample_rate != profile["sample_rate"]:
        raise ValueError("Unexpected model sample rate")
    audit("loaded", pid=os.getpid(), load_count=1, model_load_s=time.perf_counter()-load_started,
          startup_s=time.perf_counter()-started, resources=loading.summary(),
          profile_sha256=sha256(REPO / "configs/voice-profile.lock.json"),
          model_manifest_sha256=sha256(Path(config["model_manifest"])),
          parameters=lock["runtime"], reference_sha256=reference["source_sha256"],
          model_devices={name: str(next(getattr(model.model, name).parameters()).device)
                         for name in ("llm", "flow", "hift")},
          onnx_providers={"campplus": model.frontend.campplus_session.get_providers(),
                          "speech_tokenizer": model.frontend.speech_tokenizer_session.get_providers()})
    emit({"type": "ready", "profile_version": profile["profile_version"], "pid": os.getpid(), "load_count": 1})
    for line in iter(lambda: sys.stdin.readline(65537), ""):
        if len(line) > 65536 or not line.endswith("\n"):
            raise ValueError("Invalid protocol line")
        request = request_from_line(line)
        request_id = request["request_id"]
        directory = cache / "requests" / request_id
        directory.mkdir(parents=True, exist_ok=False)
        began = time.perf_counter()
        torch.cuda.reset_peak_memory_stats()
        resources = Resources()
        try:
            with resources, torch.inference_mode():
                inputs, ranges, budgets = [], sentence_ranges(request["text"]), []
                for start, end in ranges:
                    model_input = model.frontend.frontend_zero_shot(
                        request["text"][start:end], prompt, reference["input_path"], model.sample_rate, "")
                    inputs.append(model_input)
                    budgets.append(budget_for(model_input, lock["speech_lm_budget"]))
                audit("budget", request_id=request_id, ranges=ranges, budgets=budgets,
                      seed=profile["seed"], text_sha256=hashlib.sha256(request["text"].encode()).hexdigest())
                if not all(budget["fits"] for budget in budgets):
                    emit({"type": "error", "request_id": request_id, "code": "BUDGET_EXCEEDED"})
                    continue
                paths = []
                for index, model_input in enumerate(inputs):
                    seed = profile["seed"]
                    torch.manual_seed(seed)
                    np.random.seed(seed)
                    random.seed(seed)
                    chunks = [part["tts_speech"].cpu() for part in model.model.tts(
                        **model_input, stream=False, speed=1.0)]
                    audio = torch.cat(chunks, dim=1).squeeze(0).numpy()
                    if not len(audio) or not np.isfinite(audio).all() or not np.any(audio):
                        raise ValueError("Invalid generated signal")
                    path = directory / (str(index) + ".wav")
                    sf.write(str(path), audio, model.sample_rate, subtype="PCM_16")
                    decoded, rate = sf.read(str(path), always_2d=True)
                    if decoded.shape != (len(audio), 1) or rate != 24000:
                        raise ValueError("Incomplete generated segment")
                    paths.append(path)
                temporary = args.session_dir / (request["output_name"] + ".part")
                if len(paths) == 1:
                    with temporary.open("xb") as stream:
                        stream.write(paths[0].read_bytes())
                else:
                    mapping = join_segments(paths, temporary)
                    audit("join", request_id=request_id, boundaries=mapping["boundaries"])
                torch.cuda.synchronize()
            audit("generated", request_id=request_id, pid=os.getpid(), seed=profile["seed"],
                  synthesis_s=time.perf_counter()-began, resources=resources.summary(),
                  cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                  cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved())
            emit({"type": "result", "request_id": request_id, "seed": profile["seed"]})
        except Exception as exc:
            audit("generation_failed", request_id=request_id, error_type=type(exc).__name__)
            emit({"type": "error", "request_id": request_id, "code": "INTERNAL"})
            return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        audit("worker_failed", error_type=type(exc).__name__)
        sys.exit(1)
