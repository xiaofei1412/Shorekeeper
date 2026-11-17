"""Pinned local CosyVoice3 zero-shot worker, independent of the core Python stack."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

from inputs import resolve_reference
from download_model import sha256, REVISION as MODEL_REVISION, REQUIRED

CODE_REVISION = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
PROMPT_PREFIX = "You are a helpful assistant.<|endofprompt|>"
STATE = {"stage": "arguments"}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def preserved_files(source_root):
    paths = []
    for relative in ("local/reports/M05A", "local/voice_demo/M05A"):
        paths.extend(p for p in (source_root / relative).rglob("*") if p.is_file())
    for relative in ("local/assets/manifests/PLAT-01.jsonl",
                     "local/assets/batches/B20261116-M02C-01/derived/transcript_supplements.jsonl",
                     "local/assets/batches/B20261116-OWNER-01/owner_feedback.json",
                     "local/assets/batches/B20261117-OWNER-01/owner_feedback.json",
                     "pyproject.toml", "uv.lock"):
        paths.append(source_root / relative)
    from inputs import MANIFEST, REFERENCES
    assets = [json.loads(line) for line in (source_root / MANIFEST).read_text(encoding="utf-8").splitlines()]
    paths.extend(source_root / row["relative_path"] for row in assets
                 if row.get("asset_id") in {ref["asset_id"] for ref in REFERENCES.values()})
    return {str(path.relative_to(source_root)): sha256(path) for path in sorted(set(paths))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--text-set", type=Path, required=True)
    parser.add_argument("--reference-id", choices=["R1", "R2", "both"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    parser.add_argument("--seed", type=int, default=20261117)
    parser.add_argument("--smoke-upstream", action="store_true")
    parser.add_argument("--m05a2", action="store_true", help="Run the fixed R1 B8/C5 controlled experiment")
    args = parser.parse_args()
    if Path(args.run_id).name != args.run_id or args.run_id in (".", ".."):
        parser.error("run-id must be a single directory name")
    if args.m05a2:
        if args.reference_id != "R1" or args.seed != 20261117 or args.device != "cuda" or args.smoke_upstream:
            parser.error("M05A2 fixes R1, seed 20261117, CUDA and no upstream smoke job")
        for actual, relative in ((args.output_root, "local/voice_demo/M05A2"),
                                 (args.report_root, "local/reports/M05A2"),
                                 (args.cache_root, "local/cache/voice-M05A2")):
            if actual.resolve() != (args.source_root / relative).resolve():
                parser.error("M05A2 private output/report/cache path differs from task card")
    output = args.output_root / args.run_id
    report = args.report_root / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    report.mkdir(parents=True, exist_ok=False)
    STATE.update(report=report, stage="source_and_weight_validation")
    before = preserved_files(args.source_root) if args.m05a2 else None
    if before is not None:
        write_json(report / "preservation_before.json", before)
    runtime_lock = json.loads((Path(__file__).resolve().parents[2] / "configs/voice-runtime.lock.json").read_text(encoding="utf-8"))
    args.cache_root.mkdir(parents=True, exist_ok=True)
    for key, folder in {"HF_HOME": "huggingface", "MODELSCOPE_CACHE": "modelscope",
                        "MPLCONFIGDIR": "matplotlib", "NUMBA_CACHE_DIR": "numba",
                        "TEMP": "tmp", "TMP": "tmp"}.items():
        directory = args.cache_root / folder
        directory.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(directory)
    if args.device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    # Never fetch a missing tokenizer/model implicitly during the experiment.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["PYTHONUTF8"] = "1"
    code = args.runtime_root / "CosyVoice"
    revision = subprocess.check_output(["git", "-C", str(code), "rev-parse", "HEAD"], text=True).strip()
    if revision != CODE_REVISION:
        raise ValueError("CosyVoice code revision mismatch")
    if subprocess.check_output(["git", "-C", str(code), "status", "--porcelain"], text=True).strip():
        raise ValueError("CosyVoice source has local changes")
    model_dir = args.runtime_root / "Fun-CosyVoice3-0.5B-2512"
    manifest = json.loads(args.model_manifest.read_text(encoding="utf-8"))
    if manifest["revision"] != MODEL_REVISION or not REQUIRED.issubset({f["path"] for f in manifest["files"]}):
        raise ValueError("Weight manifest revision or required files mismatch")
    for item in manifest["files"]:
        path = (model_dir / item["path"]).resolve()
        if not path.is_relative_to(model_dir.resolve()) or sha256(path) != item["sha256"]:
            raise ValueError("Weight manifest hash/path mismatch: " + item["path"])
    model_manifest_sha256 = sha256(args.model_manifest)
    if model_manifest_sha256 != runtime_lock["model"]["model_manifest_sha256"]:
        raise ValueError("Weight manifest differs from committed runtime lock")
    model_config = json.loads((model_dir / "CosyVoice-BlankEN/config.json").read_text(encoding="utf-8"))
    budget_lock = runtime_lock["speech_lm_budget"]
    if budget_lock["max_token_text_ratio"] != 20 or budget_lock["special_embeddings"] != 2:
        raise ValueError("Budget lock does not match the pinned CosyVoice3 inference implementation")
    if model_config["max_position_embeddings"] != budget_lock["effective_context_tokens"]:
        raise ValueError("Speech LM context window differs from runtime lock")
    sys.path[:0] = [str(code), str(code / "third_party/Matcha-TTS")]
    # Imports are measured separately from model loading.
    started = time.perf_counter()
    STATE["stage"] = "imports"
    import numpy as np
    import soundfile as sf
    import torch
    from resources import Resources
    from cosyvoice.cli.cosyvoice import CosyVoice3
    import_s = time.perf_counter() - started
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable; no silent CPU fallback")
    torch.set_num_threads(4)
    text_set = json.loads(args.text_set.read_text(encoding="utf-8"))
    if args.m05a2 and sha256(args.text_set) != runtime_lock["text_set"]["sha256"]:
        raise ValueError("M05A2 text set differs from the locked baseline")
    references = ["R1", "R2"] if args.reference_id == "both" else [args.reference_id]
    STATE["stage"] = "reference_resolution"
    resolved = {ref: resolve_reference(args.source_root, ref, output / "references", original=args.m05a2)
                for ref in references}
    write_json(report / "references.json", resolved)
    environment = {"python": sys.version, "executable": sys.executable,
                   "platform": platform.platform(), "processor": platform.processor(),
                   "code_revision": revision, "torch": torch.__version__,
                   "model_revision": MODEL_REVISION, "model_manifest_sha256": model_manifest_sha256,
                   "cuda_runtime": torch.version.cuda, "cuda_available": torch.cuda.is_available(),
                   "device": args.device, "torch_threads": torch.get_num_threads(),
                   "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
                   "text_set_sha256": sha256(args.text_set), "import_s": import_s}
    if args.m05a2:
        baseline_environment = json.loads((args.source_root / "local/reports/M05A/comparison-01/environment.json").read_text(encoding="utf-8"))
        if environment["packages"] != baseline_environment["packages"]:
            raise ValueError("M05A2 environment packages differ from the A baseline")
    if args.device == "cuda":
        STATE["stage"] = "cuda_kernel_probe"
        environment.update(gpu=torch.cuda.get_device_name(0), capability=torch.cuda.get_device_capability(0),
                           arch_list=torch.cuda.get_arch_list())
        # A real kernel probe; availability alone is insufficient.
        probe = torch.ones((128, 128), device="cuda")
        if (probe @ probe).mean().item() != 128:
            raise RuntimeError("CUDA kernel probe failed")
        del probe
        torch.cuda.reset_peak_memory_stats()
    write_json(report / "environment.json", environment)
    STATE["stage"] = "model_load"
    loading = Resources()
    try:
        with loading:
            load_started = time.perf_counter()
            model = CosyVoice3(str(model_dir), load_trt=False, load_vllm=False, fp16=False)
            if args.device == "cuda":
                torch.cuda.synchronize()
            load_s = time.perf_counter() - load_started
    finally:
        write_json(report / "load_samples.json", loading.samples)
    environment.update(model_load_s=load_s, load_resources=loading.summary(),
                       model_devices={name: str(next(getattr(model.model, name).parameters()).device)
                                      for name in ("llm", "flow", "hift")},
                       onnx_providers={"campplus": model.frontend.campplus_session.get_providers(),
                                       "speech_tokenizer": model.frontend.speech_tokenizer_session.get_providers()},
                       text_frontend=model.frontend.text_frontend,
                       parameters={"fp16": False, "stream": False, "speed": 1.0,
                                   "text_frontend": False, "load_trt": False, "load_vllm": False})
    if args.device == "cuda":
        environment.update(load_cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                           load_cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved())
    write_json(report / "environment.json", environment)
    write_json(report / "load_samples.json", loading.samples)
    jobs = [(ref, item) for ref in references for item in text_set["items"]]
    plans = []
    completed_segments = {}
    if args.m05a2:
        from experiment import build_jobs, join_segments
        plans = build_jobs(text_set["items"])
        write_json(report / "experiment_plan.json", plans)
        (output / "segments").mkdir()
        jobs = []
        for plan in plans:
            for segment in plan["segments"]:
                grouped = len(plan["segments"]) > 1
                jobs.append(("R1", {**segment, "id": plan["text_id"], "text": segment["spoken_text"],
                                    "group": plan["group"], "category": plan["category"],
                                    "change": plan["change"], "grouped": grouped,
                                    "stem": "segments/" + segment["segment_id"] if grouped
                                            else plan["group"] + "_" + plan["text_id"]}))
    if args.smoke_upstream:
        # The reference comes only from the exact locked official code checkout.
        jobs = [("upstream", {"id": "smoke", "category": "official example",
                  "text": "收到好友从远方寄来的生日礼物，那种意外的惊喜与深深的祝福让我心中充满了甜蜜的快乐，笑容如花儿般绽放。"})]
        resolved["upstream"] = {"derived_path": str(code / "asset/zero_shot_prompt.wav"),
                                 "source_sha256": sha256(code / "asset/zero_shot_prompt.wav"),
                                 "prompt_text": "希望你以后能够做的比我还好呦。"}
    for number, (ref, item) in enumerate(jobs):
        STATE["stage"] = "synthesis:" + ref + ":" + item["id"]
        reference = resolved[ref]
        prompt = PROMPT_PREFIX + reference["prompt_text"]
        record = {"created_at": datetime.now(timezone.utc).isoformat(),
                  "run_id": args.run_id, "text_set_id": text_set["text_set_id"],
                  "text_set_sha256": environment["text_set_sha256"],
                  "text_id": item["id"], "text": item.get("display_text", item["text"]), "category": item["category"],
                  "reference_id": ref, "reference": reference, "prompt_text_sent": prompt,
                  "seed": args.seed, "device": args.device,
                  "model_revision": MODEL_REVISION, "model_manifest_sha256": model_manifest_sha256,
                  "generation_kind": "first_after_cold_load" if number == 0 else "warm",
                  "model_load_s": load_s, "parameters": environment["parameters"],
                  "onnx_providers": environment["onnx_providers"]}
        if args.m05a2:
            record.update({key: item[key] for key in ("group", "display_text", "spoken_text", "change",
                                                     "segment_id", "segment_index", "char_start", "char_end")})
        torch.manual_seed(args.seed)
        np.random.seed(args.seed)
        import random
        random.seed(args.seed)
        if args.device == "cuda":
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
        resources = Resources()
        begin = time.perf_counter()
        try:
            with resources:
                with torch.inference_mode():
                    # Same frontend/model path as inference_zero_shot with text_frontend=False.
                    # Measure all embeddings before starting autoregressive speech generation.
                    if args.m05a2:
                        # Observe the pinned loader's outputs without changing its resampling.
                        import cosyvoice.cli.frontend as frontend_module
                        loader = frontend_module.load_wav
                        branch_calls = []
                        def observed_load(path, target_sr, *loader_args, **loader_kwargs):
                            speech = loader(path, target_sr, *loader_args, **loader_kwargs)
                            branch_calls.append({"input_path": str(path), "sample_rate": target_sr,
                                                 "frames": int(speech.shape[1]), "channels": int(speech.shape[0])})
                            return speech
                        frontend_module.load_wav = observed_load
                        try:
                            model_input = model.frontend.frontend_zero_shot(
                                item["text"], prompt, reference["input_path"], model.sample_rate, "")
                        finally:
                            frontend_module.load_wav = loader
                        if [call["sample_rate"] for call in branch_calls] != [24000, 16000, 16000]:
                            raise ValueError("Unexpected pinned frontend audio branch calls")
                        reference["actual_branch_sample_rates"] = dict(zip(
                            ("flow_features", "speech_token", "speaker_embedding"),
                            [call["sample_rate"] for call in branch_calls]))
                        record["frontend_audio_calls"] = branch_calls
                    else:
                        model_input = model.frontend.frontend_zero_shot(
                            item["text"], prompt, reference["derived_path"], model.sample_rate, "")
                    text_tokens = int(model_input["text_len"].item())
                    prompt_tokens = int(model_input["prompt_text_len"].item())
                    speech_tokens = int(model_input["llm_prompt_speech_token_len"].item())
                    budget = {"C": budget_lock["effective_context_tokens"],
                              "R": budget_lock["max_token_text_ratio"] * text_tokens,
                              "S": budget_lock["safety_margin_tokens"],
                              "I": budget_lock["special_embeddings"] + text_tokens + prompt_tokens + speech_tokens,
                              "target_text_tokens": text_tokens, "prompt_text_tokens": prompt_tokens,
                              "prompt_speech_tokens": speech_tokens, "special_embeddings": budget_lock["special_embeddings"],
                              "measurement_kind": "exact_speech_lm_input_embeddings"}
                    budget["fits"] = budget["I"] + budget["R"] + budget["S"] <= budget["C"]
                    record["budget"] = budget
                    if not budget["fits"]:
                        raise ValueError("Speech LM context budget exceeded; input was not truncated")
                    chunks = [part["tts_speech"].cpu() for part in model.model.tts(
                        **model_input, stream=False, speed=1.0)]
                if args.device == "cuda":
                    torch.cuda.synchronize()
                synthesis_s = time.perf_counter() - begin
                audio = torch.cat(chunks, dim=1).squeeze(0).numpy()
                if not len(audio) or not np.isfinite(audio).all() or not np.any(audio):
                    raise ValueError("Empty, nonfinite or silent synthesis")
                wav = output / (item["stem"] + ".wav" if args.m05a2 else f"{ref}_{item['id']}.wav")
                sf.write(str(wav), audio, model.sample_rate, subtype="PCM_16")
                decoded, sample_rate = sf.read(str(wav), always_2d=True)
                if len(decoded) != len(audio) or sample_rate != model.sample_rate or decoded.shape[1] != 1:
                    raise ValueError("Decoded WAV does not match generated frames")
                record.update(status="succeeded", wav=wav.relative_to(output).as_posix(), wav_sha256=sha256(wav),
                              synthesis_s=synthesis_s, frames=len(audio), sample_rate=sample_rate,
                              channels=1, subtype="PCM_16", duration_s=len(audio) / sample_rate,
                              rtf=synthesis_s / (len(audio) / sample_rate),
                              absolute_peak=float(np.abs(audio).max()),
                              pcm_clipped_samples=int((np.abs(decoded) >= 32767 / 32768).sum()))
        except Exception as exc:
            record.update(status="failed", error_type=type(exc).__name__, error=str(exc),
                          elapsed_s=time.perf_counter() - begin)
            raise
        finally:
            if args.device == "cuda":
                record.update(cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                              cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved())
            record["resources"] = resources.summary()
            sample_stem = item["stem"].replace("/", "_") if args.m05a2 else f"{ref}_{item['id']}"
            write_json(report / (sample_stem + "_samples.json"), resources.samples)
            result_file = "segments.jsonl" if args.m05a2 and item["grouped"] else "results.jsonl"
            with (report / result_file).open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if args.m05a2 and item["grouped"]:
            completed_segments.setdefault(item["id"], []).append(record)
        print(json.dumps({k: record[k] for k in ("reference_id", "text_id", "status", "synthesis_s", "duration_s")}, ensure_ascii=False), flush=True)
    if args.m05a2:
        for plan in plans:
            if len(plan["segments"]) < 2:
                continue
            segments = completed_segments[plan["text_id"]]
            wav = output / ("C_" + plan["text_id"] + ".wav")
            mapping = join_segments([output / segment["wav"] for segment in segments], wav, sample_rate=24000)
            decoded, rate = sf.read(str(wav), always_2d=True)
            record = {key: value for key, value in segments[0].items()
                      if key not in ("budget", "resources", "segment_id", "segment_index", "char_start", "char_end",
                                     "frontend_audio_calls", "cuda_peak_allocated_bytes", "cuda_peak_reserved_bytes")}
            total_s = sum(segment["synthesis_s"] for segment in segments)
            record.update(text=plan["display_text"], display_text=plan["display_text"], spoken_text=plan["spoken_text"],
                          generation_kind="joined_segments", segments=segments, join_map=mapping,
                          wav=wav.name, wav_sha256=sha256(wav), frames=len(decoded), sample_rate=rate,
                          duration_s=len(decoded) / rate, synthesis_s=total_s, rtf=total_s / (len(decoded) / rate),
                          absolute_peak=float(np.abs(decoded).max()),
                          pcm_clipped_samples=int((np.abs(decoded) >= 32767 / 32768).sum()))
            with (report / "results.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        write_json(report / "references.json", resolved)
        after = preserved_files(args.source_root)
        write_json(report / "preservation_after.json", after)
        write_json(report / "preservation_check.json", {"unchanged": before == after, "file_count": len(before)})
        if before != after:
            raise ValueError("Historical files or source inputs changed during M05A2")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if "report" in STATE:
            write_json(STATE["report"] / "failure.json",
                       {"stage": STATE["stage"], "error_type": type(exc).__name__,
                        "error": str(exc), "traceback": traceback.format_exc()})
        raise
