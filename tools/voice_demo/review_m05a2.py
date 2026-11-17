"""Independently validate M05A2's controlled matrix and build its offline index."""

import argparse
from datetime import datetime, timezone
from html import escape
import json
import math
import os
from pathlib import Path
import wave

from download_model import REVISION
from inputs import REFERENCES
from review import sha256


GROUPED = {"T01", "T04", "T07", "T08"}
CHANGED = GROUPED | {"T06"}
PARAMETERS = {"fp16": False, "stream": False, "speed": 1.0,
              "text_frontend": False, "load_trt": False, "load_vllm": False}


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def within(root, relative):
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()), "Path escapes expected directory")
    return path


def check_budget(record):
    budget = record["budget"]
    require(budget["fits"] is True and budget["C"] == 32768 and budget["S"] == 32
            and budget["special_embeddings"] == 2
            and budget["R"] == 20 * budget["target_text_tokens"]
            and budget["I"] == sum(budget[key] for key in (
                "special_embeddings", "target_text_tokens", "prompt_text_tokens", "prompt_speech_tokens"))
            and budget["I"] + budget["R"] + budget["S"] <= budget["C"],
            "Invalid or exceeded complete speech LM budget")
    require(record["resources"]["sample_count"] > 0, "Missing resource samples")


def check_wav(record, output_dir, expected_name):
    import numpy as np
    import soundfile as sf

    require(record["status"] == "succeeded" and record["wav"] == expected_name,
            "Failed result or incorrect output association: " + expected_name)
    path = within(output_dir, expected_name)
    require(path.is_file() and sha256(path) == record["wav_sha256"], "WAV missing/hash mismatch: " + expected_name)
    with wave.open(str(path), "rb") as stream:
        frames = stream.getnframes()
        require(stream.getcomptype() == "NONE" and stream.getframerate() == 24000
                and stream.getnchannels() == 1 and stream.getsampwidth() == 2 and frames > 0
                and len(stream.readframes(frames)) == frames * 2, "Incomplete or wrong WAV format: " + expected_name)
    audio, rate = sf.read(str(path), dtype="int16")
    require(rate == 24000 and audio.shape == (frames,) and np.isfinite(audio).all() and np.any(audio),
            "WAV decode/finite/non-silent check failed: " + expected_name)
    duration = frames / rate
    require(record["frames"] == frames and record["sample_rate"] == rate
            and record["channels"] == 1 and record["subtype"] == "PCM_16"
            and math.isclose(record["duration_s"], duration, abs_tol=1e-9), "WAV metadata mismatch")
    clipped = int((np.abs(audio.astype("int32")) >= 32767).sum())
    require(clipped == record["pcm_clipped_samples"], "Clipping count differs from PCM")
    require(isinstance(record["synthesis_s"], (int, float)) and not isinstance(record["synthesis_s"], bool)
            and math.isfinite(record["synthesis_s"]) and record["synthesis_s"] >= 0,
            "Missing measured synthesis duration")
    return {"wav": expected_name, "sha256": record["wav_sha256"], "frames": frames,
            "duration_s": duration, "synthesis_s": record["synthesis_s"],
            "pcm_clipped_samples": clipped, "absolute_peak": float(np.abs(audio.astype("int32")).max() / 32768)}, audio


def validate(results_path, segments_path, output_dir, text_set_path, source_root):
    import numpy as np

    source_root, output_dir = source_root.resolve(), output_dir.resolve()
    require(output_dir.is_relative_to(source_root / "local/voice_demo/M05A2"), "Expected M05A2 output directory")
    text_set = json.loads(text_set_path.read_text(encoding="utf-8"))
    texts = {item["id"]: item["text"] for item in text_set["items"]}
    ids = {"T%02d" % n for n in range(1, 9)}
    require(set(texts) == ids and len(text_set["items"]) == 8
            and text_set["official_dialogue"] is False and text_set["training_admission"] is False,
            "Unexpected original test set")
    text_set_hash = sha256(text_set_path)
    baseline_dir = source_root / "local/voice_demo/M05A/comparison-01"
    baseline_path = source_root / "local/reports/M05A/comparison-01/results.jsonl"
    baseline = {r["text_id"]: r for r in jsonl(baseline_path) if r["reference_id"] == "R1"}
    require(set(baseline) == ids, "Old R1 baseline is incomplete")
    records = jsonl(results_path)
    expected = {("B", text_id) for text_id in ids} | {("C", text_id) for text_id in CHANGED}
    by_key = {(r["group"], r["text_id"]): r for r in records}
    require(len(records) == 13 and set(by_key) == expected, "Expected exactly B8/C5 whole-result matrix")
    segment_records = jsonl(segments_path)
    require(len(segment_records) == 8, "Expected eight raw segments for four two-sentence C results")
    by_segment = {r["segment_id"]: r for r in segment_records}
    require(len(by_segment) == 8, "Duplicate segment ID")
    report_dir = results_path.parent
    preserved_before = json.loads((report_dir / "preservation_before.json").read_text(encoding="utf-8"))
    preserved_after = json.loads((report_dir / "preservation_after.json").read_text(encoding="utf-8"))
    preservation = json.loads((report_dir / "preservation_check.json").read_text(encoding="utf-8"))
    require(preserved_before == preserved_after and preservation["unchanged"] is True
            and preservation["file_count"] == len(preserved_before), "Historical/source files changed")
    for name, digest in preserved_after.items():
        require(sha256(within(source_root, name)) == digest, "Preserved file has subsequently changed: " + name)
    environment = json.loads((report_dir / "environment.json").read_text(encoding="utf-8"))
    old_environment = json.loads((baseline_path.parent / "environment.json").read_text(encoding="utf-8"))
    require(environment["packages"] == old_environment["packages"]
            and environment["model_devices"] == old_environment["model_devices"]
            and environment["onnx_providers"] == old_environment["onnx_providers"],
            "Controlled environment or execution devices differ from A")
    old_reference = baseline["T01"]["reference"]
    expected_source = REFERENCES["R1"]
    source_audio = within(source_root, old_reference["source_relative_path"])
    require(sha256(source_audio) == expected_source["source_sha256"], "Original R1 source hash mismatch")
    for path_key, hash_key in (("transcript_path", "transcript_file_sha256"), ("feedback_path", "feedback_sha256")):
        prompt_source = old_reference["prompt_source"]
        require(sha256(within(source_root, prompt_source[path_key])) == prompt_source[hash_key], "Prompt source hash mismatch")
    lock = json.loads((Path(__file__).resolve().parents[2] / "configs/voice-runtime.lock.json").read_text(encoding="utf-8"))
    checked, old_checked, raw_checked = [], [], []
    all_segment_ids = []

    def common(record, text_id):
        reference = record["reference"]
        require(record["reference_id"] == "R1" and record["seed"] == 20261117
                and record["parameters"] == PARAMETERS and record["model_revision"] == REVISION
                and record["model_manifest_sha256"] == lock["model"]["model_manifest_sha256"]
                and record["text_set_id"] == text_set["text_set_id"]
                and record["text_set_sha256"] == text_set_hash, "Controlled runtime/text-set mismatch")
        for key in ("seed", "parameters", "model_revision", "model_manifest_sha256", "prompt_text_sent", "text_set_sha256"):
            require(record[key] == baseline[text_id][key], "A/B/C fixed setting changed: " + key)
        for key in ("asset_id", "source_sha256", "source_relative_path", "prompt_text", "prompt_source", "original_audio"):
            require(reference[key] == old_reference[key], "Reference provenance changed: " + key)
        require(reference["asset_id"] == expected_source["asset_id"]
                and reference["source_sha256"] == expected_source["source_sha256"]
                and record["prompt_text_sent"] == "You are a helpful assistant.<|endofprompt|>" + reference["prompt_text"]
                and Path(reference["input_path"]).resolve() == source_audio
                and reference["input_sha256"] == expected_source["source_sha256"]
                and reference["input_mode"] == "original48k"
                and reference["original_audio"]["sample_rate"] == 48000,
                "High-fidelity reference must be the verified original 48 kHz file")
        require(reference["actual_branch_sample_rates"] == {
            "flow_features": 24000, "speech_token": 16000, "speaker_embedding": 16000},
            "Missing actual per-branch sample-rate evidence")
        if record.get("generation_kind") != "joined_segments":
            calls = record["frontend_audio_calls"]
            require([call["sample_rate"] for call in calls] == [24000, 16000, 16000]
                    and all(Path(call["input_path"]).resolve() == source_audio
                            and call["channels"] == 1 and call["frames"] > 0 for call in calls),
                    "Frontend branch probe used an unexpected input/rate")
        require(record["device"] == baseline[text_id]["device"]
                and record["onnx_providers"] == baseline[text_id]["onnx_providers"], "Controlled device path changed")

    for text_id in sorted(ids):
        old = baseline[text_id]
        require(old["text"] == texts[text_id], "Old text does not match original set")
        info, _ = check_wav(old, baseline_dir, "R1_" + text_id + ".wav")
        old_checked.append(info)
    for (group, text_id), record in sorted(by_key.items()):
        common(record, text_id)
        require(record["text"] == texts[text_id] and record["display_text"] == texts[text_id], "Display text changed")
        expected_name = group + "_" + text_id + ".wav"
        info, audio = check_wav(record, output_dir, expected_name)
        checked.append(dict(info, group=group, text_id=text_id))
        if group == "B":
            require(record["spoken_text"] == texts[text_id], "B spoken text must remain original")
            check_budget(record)
        elif text_id == "T06":
            require(record["spoken_text"] == "这根 优 艾思 比 太普 西 线长 一点五 米，编号是 诶 零 七。",
                    "T06 must retain USB Type-C / 1.5 metre / A07 with the declared explicit reading")
            check_budget(record)
        else:
            require(record["spoken_text"] == texts[text_id], "Grouped C must retain original text")
            segments = record["segments"]
            require(len(segments) == 2 and len(record["join_map"]["segments"]) == 2,
                    "Grouped C must contain two complete sentence segments")
            cursor = 0
            join_map = record["join_map"]
            require(join_map["policy"]["name"] == "preserve_native_edges_top_up_to_180ms"
                    and join_map["policy"]["silence_threshold_dbfs"] == -50
                    and join_map["policy"]["minimum_boundary_frames"] == 4320
                    and join_map["policy"]["silence_threshold_pcm"] == 104
                    and all(join_map["policy"][key] is False for key in ("trimming", "crossfade", "speed_change")),
                    "Sentence join policy differs from the fixed experiment")
            require(join_map["sample_rate"] == 24000 and join_map["frames"] == len(audio)
                    and len(join_map["boundaries"]) == 1, "Invalid join shape")
            pcm_cursor = 0
            total_synthesis = 0
            for number, segment in enumerate(segments):
                sid = f"C_{text_id}_S{number + 1:02d}"
                require(segment == by_segment.get(sid) and segment["segment_id"] == sid
                        and segment["segment_index"] == number and segment["char_start"] == cursor,
                        "Raw segment identity/order/character map mismatch")
                end = segment["char_end"]
                require(cursor < end <= len(texts[text_id])
                        and segment["display_text"] == segment["spoken_text"] == texts[text_id][cursor:end]
                        and segment["spoken_text"][-1] in "。？！!?", "Segment splits must retain complete sentence punctuation")
                cursor = end
                common(segment, text_id)
                check_budget(segment)
                segment_name = segment["wav"]
                require(Path(segment_name).stem == sid, "Segment filename association mismatch")
                sinfo, segment_pcm = check_wav(segment, output_dir, segment_name)
                raw_checked.append(dict(sinfo, segment_id=sid))
                all_segment_ids.append(sid)
                total_synthesis += segment["synthesis_s"]
                mapping = join_map["segments"][number]
                require(Path(mapping["input_wav"]).resolve() == within(output_dir, segment_name)
                        and mapping["wav_sha256"] == segment["wav_sha256"] and mapping["frames"] == len(segment_pcm)
                        and mapping["frame_start"] == pcm_cursor
                        and mapping["frame_end"] == pcm_cursor + len(segment_pcm)
                        and np.array_equal(audio[pcm_cursor:pcm_cursor + len(segment_pcm)], segment_pcm),
                        "Joined output does not preserve original segment PCM")
                signal = np.flatnonzero(np.abs(segment_pcm.astype("int32")) > math.ceil(32768 * 10 ** (-50 / 20)))
                require(len(signal) > 0, "Segment is silent at the declared -50 dBFS boundary threshold")
                leading, trailing = int(signal[0]), int(len(segment_pcm) - signal[-1] - 1)
                require(mapping["leading_silence_frames"] == leading and mapping["trailing_silence_frames"] == trailing,
                        "Native silence measurements do not match PCM")
                pcm_cursor += len(segment_pcm)
                if number == 0:
                    boundary = join_map["boundaries"][0]
                    next_mapping = join_map["segments"][1]
                    native = trailing + next_mapping["leading_silence_frames"]
                    gap = max(0, round(0.18 * 24000) - native)
                    require(boundary["after_segment_index"] == 0 and boundary["native_silence_frames"] == native
                            and boundary["added_gap_frames"] == gap
                            and boundary["output_gap_start_frame"] == pcm_cursor
                            and boundary["output_gap_end_frame"] == pcm_cursor + gap
                            and not np.any(audio[pcm_cursor:pcm_cursor + gap]), "Sentence gap policy/mapping mismatch")
                    pcm_cursor += gap
            require(cursor == len(texts[text_id]) and pcm_cursor == len(audio)
                    and math.isclose(record["synthesis_s"], total_synthesis, abs_tol=1e-9),
                    "Grouped result omits/repeats text/audio or miscounts generation time")
    require(set(all_segment_ids) == set(by_segment), "Unmapped raw segment")
    require({p.name for p in output_dir.glob("*.wav")} == {r["wav"] for r in records}, "Unexpected top-level WAV")
    require({p.relative_to(output_dir).as_posix() for p in output_dir.rglob("*.wav")}
            == {r["wav"] for r in records + segment_records}, "Unexpected or unmapped nested WAV")
    candidates = [{"text_id": text_id, "group": "C" if text_id in CHANGED else "B",
                   "wav": by_key[("C" if text_id in CHANGED else "B", text_id)]["wav"],
                   "generation": "reused_existing_result", "listening_status": "pending_user_review"}
                  for text_id in sorted(ids)]
    report = {"status": "passed", "validated_at": datetime.now(timezone.utc).isoformat(),
              "results_sha256": sha256(results_path), "segments_sha256": sha256(segments_path),
              "baseline_results_sha256": sha256(baseline_path), "text_set_sha256": text_set_hash,
              "expected_new_whole_files": 13, "validated_new_whole_files": len(checked),
              "validated_raw_segments": len(raw_checked), "new_whole_files": checked,
              "raw_segments": raw_checked, "old_baseline_files": old_checked,
              "preserved_file_count": len(preserved_before),
              "total_new_whole_audio_s": sum(r["duration_s"] for r in checked),
              "total_raw_segment_audio_s": sum(r["duration_s"] for r in raw_checked),
              "candidates": candidates, "listening_status": "pending_user_review",
              "scope": "Provenance, fixed settings, full PCM decode, token budgets, and lossless sentence-join mapping; no listening judgment."}
    return report, text_set["items"], by_key, baseline, source_audio


def resource_summary(report_dir, records, validation):
    environment = json.loads((report_dir / "environment.json").read_text(encoding="utf-8"))
    generated = [record for record in records.values() if record["generation_kind"] != "joined_segments"] + jsonl(report_dir / "segments.jsonl")
    resources = [environment["load_resources"]] + [record["resources"] for record in generated]
    return {"import_s": environment["import_s"], "model_load_s": environment["model_load_s"],
            "actual_generation_calls": len(generated),
            "synthesis_s_excluding_duplicate_joined_accounting": sum(record["synthesis_s"] for record in generated),
            "new_whole_wav_count": 13, "new_whole_audio_s": validation["total_new_whole_audio_s"],
            "raw_segment_count": 8, "raw_segment_audio_s": validation["total_raw_segment_audio_s"],
            "peak_process_rss_bytes": max(item["peak_process_rss_bytes"] for item in resources),
            "os_peak_working_set_bytes": max(item["os_peak_working_set_bytes"] for item in resources),
            "peak_whole_gpu_used_mib": max(item["peak_whole_gpu_used_mib"] for item in resources),
            "gpu_scope": "whole device including other processes; not per-process VRAM",
            "cuda_peak_allocated_bytes": max(record["cuda_peak_allocated_bytes"] for record in generated),
            "cuda_peak_reserved_bytes": max(record["cuda_peak_reserved_bytes"] for record in generated),
            "actual_model_devices": environment["model_devices"],
            "actual_onnx_providers": environment["onnx_providers"],
            "stream": False, "service_integration": "not_integrated",
            "listening_status": "pending_user_review"}


def listening_html(items, records, baseline, source_audio, source_root, output_dir, candidates):
    def url(path):
        return escape(Path(os.path.relpath(path, output_dir)).as_posix(), quote=True)

    def player(label, record, root):
        return ('<article><h3>' + escape(label) + '</h3><audio controls preload="none" src="'
                + url(root / record["wav"]) + '" aria-label="' + escape(label, quote=True) + '"></audio>'
                + '<p>%.3f 秒</p>' % record["duration_s"]
                + ('<p>实际读法：' + escape(record["spoken_text"]) + '</p>'
                   if record.get("spoken_text", record["text"]) != record["text"] else '') + '</article>')

    old_dir = source_root / "local/voice_demo/M05A/comparison-01"
    sections = ['<h2 id="ab">A/B：原16k派生与原48k参考输入（8对）</h2>']
    for item in items:
        tid = item["id"]
        sections.append('<section><h3>' + tid + '</h3><p class="text">' + escape(item["text"])
                        + '</p><div class="pair">' + player("A · 旧R1基线 · " + tid, baseline[tid], old_dir)
                        + player("B · 原48k输入 · " + tid, records[("B", tid)], output_dir) + '</div></section>')
    sections.append('<h2 id="bc">B/C：读法或完整句分组（5对）</h2>')
    for item in items:
        tid = item["id"]
        if tid in CHANGED:
            sections.append('<section><h3>' + tid + (' · 显式读法' if tid == 'T06' else ' · 完整句分组')
                            + '</h3><p class="text">' + escape(item["text"]) + '</p><div class="pair">'
                            + player("B · 整条原文 · " + tid, records[("B", tid)], output_dir)
                            + player("C · " + tid, records[("C", tid)], output_dir) + '</div></section>')
    sections.append('<h2 id="candidate">8句候选清单（复用已有B/C）</h2><p>按实验覆盖列出，未评定听感优胜，未提升默认配置。</p>')
    for candidate in candidates:
        record = records[(candidate["group"], candidate["text_id"])]
        sections.append('<section><p class="text">' + escape(record["text"]) + '</p>'
                        + player(candidate["group"] + ' · ' + candidate["text_id"], record, output_dir) + '</section>')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>M05A2 R1受控对照试听</title><style>
body{font:16px/1.65 system-ui,sans-serif;color:#19322e;background:#eff5f3;margin:0;padding:24px}main{max-width:1060px;margin:auto}
h1{font-size:28px}h2{margin-top:36px}section,.notice{background:white;padding:20px;border:1px solid #cfdfd9;border-radius:10px;margin:18px 0}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:18px}article{background:#f3f8f6;padding:14px;border-radius:7px;min-width:0}h3{margin:0 0 8px}audio{width:100%}
.text{font-size:18px}a{color:#245f53}nav{display:flex;gap:18px;flex-wrap:wrap}@media(max-width:650px){body{padding:12px}.pair{grid-template-columns:1fr}}
</style><main><h1>M05A2 · R1受控对照试听</h1>
<p class="notice">测试文字为手工创作，非官方台词。A为旧结果；B只改变参考输入采样路径；C分别改变T06读法或完整句分组。
请比较R1音色、电音、断句、节奏及有无漏读/重复。技术检查不代替听审；相同seed也不保证CUDA逐位一致。</p>
<nav><a href="#ab">8对 A/B</a><a href="#bc">5对 B/C</a><a href="#candidate">8句候选</a></nav>
<section><h2>R1参考 · 初奏原声</h2><p>来源：既有资产清单与OWNER全文；原48kHz/24bit WAV，仅用于本地参考。</p>
<audio controls preload="none" src="''' + url(source_audio) + '''" aria-label="R1 原48k参考"></audio>
<p>参考全文：''' + escape(baseline["T01"]["reference"]["prompt_text"]) + '''</p></section>''' + ''.join(sections) + '</main></html>\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--text-set", type=Path, required=True)
    args = parser.parse_args()
    require(args.report_dir.resolve().is_relative_to(args.source_root.resolve() / "local/reports/M05A2"),
            "Expected M05A2 report directory")
    require(args.report_dir.name == args.output_dir.name, "Report/output run IDs differ")
    report_path = args.report_dir / "technical_validation.json"
    try:
        report, items, records, baseline, source_audio = validate(
            args.report_dir / "results.jsonl", args.report_dir / "segments.jsonl",
            args.output_dir, args.text_set, args.source_root)
    except Exception as exc:
        report_path.write_text(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                                          "error": str(exc)}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    (args.output_dir / "index.html").write_text(listening_html(
        items, records, baseline, source_audio, args.source_root, args.output_dir, report["candidates"]), encoding="utf-8")
    (args.report_dir / "candidates.json").write_text(json.dumps(report["candidates"], ensure_ascii=False, indent=2), encoding="utf-8")
    (args.report_dir / "resource_summary.json").write_text(json.dumps(resource_summary(
        args.report_dir, records, report), ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "new_whole_files": 13, "raw_segments": 8,
                      "total_new_whole_audio_s": report["total_new_whole_audio_s"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
