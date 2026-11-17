"""Validate M05A3's fixed diagnostic matrix and build a local listening page."""

import argparse
from datetime import datetime, timezone
from html import escape
import json
import math
import os
from pathlib import Path
import tempfile

from download_model import REVISION
from experiment import join_segments
from experiment_m05a3 import build_jobs, REGRESSION_KEYS
from inputs import resolve_reference
from review import sha256
from review_m05a2 import check_budget, check_wav, jsonl, PARAMETERS, require, within


FEEDBACK = "local/assets/batches/B20261117-OWNER-02/owner_feedback.json"
BASELINE_REPORT = "local/reports/M05A2/controlled-01"
BASELINE_OUTPUT = "local/voice_demo/M05A2/controlled-01"
COMPARISON_KEYS = (("B", "T02"), ("B", "T05"), ("B", "T06"), ("C", "T06"))


def validate(results_path, segments_path, output_dir, text_set_path, source_root):
    import numpy as np
    import soundfile as sf

    source_root, output_dir = source_root.resolve(), output_dir.resolve()
    report_dir = results_path.parent.resolve()
    require(output_dir.parent == source_root / "local/voice_demo/M05A3"
            and report_dir.parent == source_root / "local/reports/M05A3"
            and report_dir.name == output_dir.name, "Expected matching M05A3 run directories")
    lock_path = Path(__file__).resolve().parents[2] / "configs/voice-runtime.lock.json"
    lock_hash = sha256(lock_path)
    require(lock_hash == sha256(source_root / "configs/voice-runtime.lock.json"),
            "Source/worktree runtime locks differ at review time")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    text_set = json.loads(text_set_path.read_text(encoding="utf-8"))
    text_hash = sha256(text_set_path)
    require(text_hash == lock["text_set"]["sha256"] and text_set["official_dialogue"] is False
            and text_set["training_admission"] is False, "Changed original text set or admission")
    plans = build_jobs(text_set["items"])
    require(json.loads((report_dir / "experiment_plan.json").read_text(encoding="utf-8")) == plans,
            "Saved plan differs from the explicit M05A3 experiment")
    records, raw_records = jsonl(results_path), jsonl(segments_path)
    by_key = {(record["group"], record["text_id"]): record for record in records}
    by_segment = {record["segment_id"]: record for record in raw_records}
    expected = {("S%d" % seed, tid) for seed in (20261118, 20261119) for tid in ("T02", "N01", "N02")}
    expected |= {("P", "T05"), ("G", "T06")}
    require(len(records) == 8 and set(by_key) == expected, "Expected exactly six short, one names and one mixed whole WAV")
    require(len(raw_records) == 2 and set(by_segment) == {"G_T06_S01", "G_T06_S02"},
            "Expected exactly two original mixed-text segments")

    before = json.loads((report_dir / "preservation_before.json").read_text(encoding="utf-8"))
    after = json.loads((report_dir / "preservation_after.json").read_text(encoding="utf-8"))
    preservation = json.loads((report_dir / "preservation_check.json").read_text(encoding="utf-8"))
    required_preserved = {FEEDBACK}
    for relative in ("local/reports/M05A", "local/voice_demo/M05A", "local/reports/M05A2", "local/voice_demo/M05A2"):
        required_preserved.update(path.relative_to(source_root).as_posix()
                                  for path in (source_root / relative).rglob("*") if path.is_file())
    normalized = {Path(name).as_posix() for name in before}
    require(required_preserved <= normalized, "Preservation snapshot omits old results/reports or feedback")
    require(before == after and preservation["unchanged"] is True
            and preservation["file_count"] == len(before), "Historical/source files changed")
    for relative, digest in after.items():
        require(sha256(within(source_root, relative)) == digest, "Preserved input changed: " + relative)

    old_report_dir, old_output_dir = source_root / BASELINE_REPORT, source_root / BASELINE_OUTPUT
    old_results_path = old_report_dir / "results.jsonl"
    old_records = jsonl(old_results_path)
    baseline = {(record["group"], record["text_id"]): record for record in old_records}
    old_expected = {("B", "T%02d" % number) for number in range(1, 9)}
    old_expected |= {("C", tid) for tid in ("T01", "T04", "T06", "T07", "T08")}
    require(len(old_records) == 13 and set(baseline) == old_expected, "Incomplete M05A2 baseline matrix")
    old_checked = []
    for key, record in baseline.items():
        info, _ = check_wav(record, old_output_dir, "%s_%s.wav" % key)
        old_checked.append(dict(info, group=key[0], text_id=key[1]))
    feedback_path = source_root / FEEDBACK
    feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    require(feedback["provenance"]["results_path"] == BASELINE_REPORT + "/results.jsonl"
            and sha256(old_results_path) == feedback["provenance"]["results_sha256"]
            and feedback["provenance"]["index_path"] == BASELINE_OUTPUT + "/index.html"
            and sha256(source_root / feedback["provenance"]["index_path"]) == feedback["provenance"]["index_sha256"],
            "Owner feedback refers to a different baseline report/page")
    feedback_refs = {}
    for item in feedback["items"]:
        for ref in item["audio_refs"]:
            key = (ref["group"], item["text_id"])
            require(key not in feedback_refs and key in baseline
                    and ref["wav"] == baseline[key]["wav"] and ref["wav_sha256"] == baseline[key]["wav_sha256"],
                    "Owner feedback audio binding mismatch")
            feedback_refs[key] = ref
    require(set(REGRESSION_KEYS) == {("C", "T01"), ("B", "T03"), ("C", "T04"), ("C", "T07"), ("C", "T08")}
            and set(REGRESSION_KEYS + COMPARISON_KEYS) <= set(feedback_refs),
            "Regression/comparison selection is not bound to reported owner feedback")

    environment = json.loads((report_dir / "environment.json").read_text(encoding="utf-8"))
    old_environment = json.loads((old_report_dir / "environment.json").read_text(encoding="utf-8"))
    for key in ("packages", "python", "torch", "cuda_runtime", "model_devices", "onnx_providers", "torch_threads"):
        require(environment[key] == old_environment[key], "Controlled environment differs from M05A2: " + key)
    require(environment["code_revision"] == lock["source"]["revision"]
            and environment["matcha_tts_revision"] == lock["source"]["matcha_tts_revision"]
            and environment["model_revision"] == REVISION == lock["model"]["revision"]
            and environment["model_manifest_sha256"] == lock["model"]["model_manifest_sha256"]
            and environment["parameters"] == PARAMETERS and environment["device"] == "cuda"
            and environment["cuda_available"] is True and environment["text_set_sha256"] == text_hash,
            "Runtime/model/text set differs from the locked experiment")
    expected_reference = resolve_reference(source_root, "R1", output_dir / "references", original=True)
    source_audio = Path(expected_reference["input_path"]).resolve()
    reference_keys = tuple(expected_reference)

    def common(record, plan):
        require(record["group"] == plan["group"] and record["text_id"] == plan["text_id"]
                and record["category"] == plan["category"] and record["change"] == plan["change"]
                and record["text"] == record["display_text"]
                and record["seed"] == plan["seed"] and record["reference_id"] == "R1"
                and record["parameters"] == PARAMETERS and record["model_revision"] == REVISION
                and record["model_manifest_sha256"] == lock["model"]["model_manifest_sha256"]
                and record["text_set_id"] == text_set["text_set_id"] and record["text_set_sha256"] == text_hash
                and record["device"] == "cuda" and record["onnx_providers"] == environment["onnx_providers"],
                "Per-result seed/runtime mismatch")
        reference = record["reference"]
        require(all(reference[key] == expected_reference[key] for key in reference_keys)
                and record["prompt_text_sent"] == "You are a helpful assistant.<|endofprompt|>" + expected_reference["prompt_text"]
                and reference["actual_branch_sample_rates"] == {
                    "flow_features": 24000, "speech_token": 16000, "speaker_embedding": 16000},
                "Reference provenance/full prompt/original48k input changed")
        if record["generation_kind"] != "joined_segments":
            require(record["generation_kind"] in ("first_after_cold_load", "warm"), "Unknown generation kind")
            calls = record["frontend_audio_calls"]
            require([call["sample_rate"] for call in calls] == [24000, 16000, 16000]
                    and all(Path(call["input_path"]).resolve() == source_audio and call["channels"] == 1
                            and call["frames"] > 0 for call in calls), "Frontend did not use the original48k reference")

    checked, raw_checked = [], []
    for plan in plans:
        key = (plan["group"], plan["text_id"])
        record = by_key[key]
        common(record, plan)
        require(record["text"] == record["display_text"] == plan["display_text"]
                and record["spoken_text"] == plan["spoken_text"] and record["change"] == plan["change"],
                "Whole result text association differs from plan")
        info, audio = check_wav(record, output_dir, "%s_%s.wav" % key)
        checked.append(dict(info, group=key[0], text_id=key[1], seed=record["seed"]))
        if len(plan["segments"]) == 1:
            require(record["generation_kind"] != "joined_segments" and "join_map" not in record,
                    "Short/names text must be generated whole")
            for field in ("segment_id", "segment_index", "char_start", "char_end", "display_text", "spoken_text"):
                require(record[field] == plan["segments"][0][field], "Whole result was split or changed: " + field)
            check_budget(record)
            continue
        require(record["generation_kind"] == "joined_segments" and len(record["segments"]) == 2,
                "Mixed text must contain exactly two explicit semantic groups")
        segment_paths, total_s = [], 0
        for part, segment in zip(plan["segments"], record["segments"]):
            require(segment == by_segment[part["segment_id"]], "Joined/raw segment records differ")
            for field in ("segment_id", "segment_index", "char_start", "char_end", "display_text", "spoken_text"):
                require(segment[field] == part[field], "Mixed semantic boundary/characters changed: " + field)
            common(segment, plan)
            check_budget(segment)
            name = "segments/" + part["segment_id"] + ".wav"
            sinfo, _ = check_wav(segment, output_dir, name)
            raw_checked.append(dict(sinfo, segment_id=part["segment_id"]))
            segment_paths.append(output_dir / name)
            total_s += segment["synthesis_s"]
        require("".join(part["spoken_text"] for part in record["segments"]) == plan["spoken_text"]
                and math.isclose(record["synthesis_s"], total_s, abs_tol=1e-9),
                "Joined text/time omits or repeats a segment")
        with tempfile.TemporaryDirectory(prefix="m05a3-join-", dir=source_root / "local/cache/voice-M05A3") as temporary:
            reconstructed = Path(temporary) / "reconstructed.wav"
            mapping = join_segments(segment_paths, reconstructed)
            recomputed_pcm, rate = sf.read(str(reconstructed), dtype="int16")
            require(mapping == record["join_map"] and rate == 24000 and np.array_equal(audio, recomputed_pcm),
                    "PCM join map/output differs from lossless 180ms policy recomputation")
    require({path.relative_to(output_dir).as_posix() for path in output_dir.rglob("*.wav")}
            == {record["wav"] for record in records + raw_records}, "Unexpected or unmapped WAV")
    generated = [record for record in records if record["generation_kind"] != "joined_segments"] + raw_records
    require(len(generated) == 9 and sum(record["generation_kind"] == "first_after_cold_load" for record in generated) == 1,
            "Expected nine actual generation calls and one cold-load first result")
    regression = [{"group": group, "text_id": tid, "wav": baseline[(group, tid)]["wav"],
                   "wav_sha256": baseline[(group, tid)]["wav_sha256"], "generation": "read_only_existing_result",
                   "feedback_id": feedback["feedback_id"]} for group, tid in REGRESSION_KEYS]
    report = {"status": "passed", "validated_at": datetime.now(timezone.utc).isoformat(),
              "results_sha256": sha256(results_path), "segments_sha256": sha256(segments_path),
              "text_set_sha256": text_hash, "experiment_plan_sha256": sha256(report_dir / "experiment_plan.json"),
              "feedback_sha256": sha256(feedback_path), "baseline_results_sha256": sha256(old_results_path),
              "review_runtime_lock_sha256": lock_hash,
              "expected_new_whole_files": 8, "validated_new_whole_files": len(checked),
              "validated_raw_segments": len(raw_checked), "actual_generation_calls": len(generated),
              "new_whole_files": checked, "raw_segments": raw_checked, "old_baseline_files": old_checked,
              "preserved_file_count": len(before), "regression_references": regression,
              "total_new_whole_audio_s": sum(row["duration_s"] for row in checked),
              "total_raw_segment_audio_s": sum(row["duration_s"] for row in raw_checked),
              "listening_status": "pending_user_review",
              "scope": "Technical matrix, exact text/seed, provenance, PCM decode, budgets and lossless joins only; no listening judgment."}
    return report, text_set["items"], by_key, baseline, source_audio


def resource_summary(report_dir, records, validation):
    environment = json.loads((report_dir / "environment.json").read_text(encoding="utf-8"))
    generated = [record for record in records.values() if record["generation_kind"] != "joined_segments"] + jsonl(report_dir / "segments.jsonl")
    resources = [environment["load_resources"]] + [record["resources"] for record in generated]
    summary = {"import_s": environment["import_s"], "model_load_s": environment["model_load_s"],
               "actual_generation_calls": len(generated),
               "synthesis_s_excluding_duplicate_joined_accounting": sum(record["synthesis_s"] for record in generated),
               "new_whole_wav_count": 8, "new_whole_audio_s": validation["total_new_whole_audio_s"],
               "raw_segment_count": 2, "raw_segment_audio_s": validation["total_raw_segment_audio_s"],
               "gpu_scope": "whole device including other processes; not per-process VRAM",
               "actual_model_devices": environment["model_devices"], "actual_onnx_providers": environment["onnx_providers"],
               "stream": False, "service_integration": "not_integrated", "listening_status": "pending_user_review"}
    for key in ("peak_process_rss_bytes", "os_peak_working_set_bytes", "peak_whole_gpu_used_mib"):
        summary[key] = max(row[key] for row in resources)
    for key in ("cuda_peak_allocated_bytes", "cuda_peak_reserved_bytes"):
        summary[key] = max(record[key] for record in generated)
    return summary


def listening_html(items, records, baseline, source_audio, source_root, output_dir, validation):
    def url(path):
        return escape(Path(os.path.relpath(path, output_dir)).as_posix(), quote=True)

    def player(label, record, root):
        return ('<article><h3>' + escape(label) + '</h3><audio controls preload="none" src="'
                + url(root / record["wav"]) + '" aria-label="' + escape(label, quote=True) + '"></audio>'
                + '<p>seed=' + str(record["seed"]) + ' · %.3f 秒</p>' % record["duration_s"]
                + '<p>展示文字：' + escape(record.get("display_text", record["text"]))
                + '</p><p>实际输入：' + escape(record.get("spoken_text", record["text"])) + '</p></article>')

    old_root = source_root / BASELINE_OUTPUT
    sections = ['<h2 id="short">短句 seed 诊断</h2><section><h3>T02：三个 seed</h3><div class="pair">'
                + player("旧基线 · B-T02", baseline[("B", "T02")], old_root)
                + ''.join(player("新生成 · T02", records[("S%d" % seed, "T02")], output_dir)
                          for seed in (20261118, 20261119)) + '</div></section>']
    for tid in ("N01", "N02"):
        sections.append('<section><h3>' + tid + '：新写短句，非官方/非训练/非角色留出题</h3><div class="pair">'
                        + ''.join(player("新生成 · " + tid, records[("S%d" % seed, tid)], output_dir)
                                  for seed in (20261118, 20261119)) + '</div></section>')
    sections.append('<h2 id="names">T05：名单标点</h2><section><div class="pair">'
                    + player("旧基线 · B-T05", baseline[("B", "T05")], old_root)
                    + player("新生成 · 仅显式标点变化", records[("P", "T05")], output_dir) + '</div></section>')
    sections.append('<h2 id="mixed">T06：保留原拼写的语义分组</h2><section><p>新结果仅在“米，/编号是”边界分组，原始两段全部保留。180ms为原生边界静音不足时的补足下限，不代表精确听感停顿。</p><div class="pair">'
                    + player("旧基线 · B-T06 · 原拼写", baseline[("B", "T06")], old_root)
                    + player("旧对照 · C-T06 · 强制谐音（非默认）", baseline[("C", "T06")], old_root)
                    + player("新生成 · 两段拼接", records[("G", "T06")], output_dir)
                    + '</div><details><summary>两个原始生成段</summary><div class="pair">'
                    + ''.join(player("新生成原始段 · " + segment["segment_id"], segment, output_dir)
                              for segment in records[("G", "T06")]["segments"]) + '</div></details></section>')
    sections.append('<h2 id="regression">只读回归：已有偏好/认可结果</h2><p>以下复用原文件，未重新生成或调参；原反馈仅适用于当时报告的细项。</p><section><div class="pair">'
                    + ''.join(player("只读复用 · %s-%s" % key, baseline[key], old_root)
                              for key in REGRESSION_KEYS) + '</div></section>')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>M05A3 停顿与混合读法对照</title><style>
body{font:16px/1.65 system-ui,sans-serif;color:#19322e;background:#eff5f3;margin:0;padding:24px}main{max-width:1100px;margin:auto}
h1{font-size:28px}h2{margin-top:36px}section,.notice{background:white;padding:20px;border:1px solid #cfdfd9;border-radius:10px;margin:18px 0}
.pair{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}article{background:#f3f8f6;padding:14px;border-radius:7px;min-width:0}
h3{margin:0 0 8px}audio{width:100%}p{overflow-wrap:anywhere}a{color:#245f53}nav{display:flex;gap:18px;flex-wrap:wrap}
@media(max-width:650px){body{padding:12px}.pair{grid-template-columns:1fr}}</style><main><h1>M05A3 · 停顿与混合读法对照</h1>
<p class="notice">8个新整条WAV、2个新原始段，实际模型生成9次。所有新结果均待试听，无最佳候选或默认配置提升。
请比较短词组连贯、名单专名及停顿、英文/数字读法、节奏与电音。文字完整和技术检查不能证明听感无漏读/重复；相同seed不保证CUDA逐位一致。</p>
<nav><a href="#short">短句</a><a href="#names">名单</a><a href="#mixed">混合读法</a><a href="#regression">只读回归</a></nav>
<section><h2>固定参考与模型</h2><p>R1 · 初奏原48kHz/24bit WAV；manifest → OWNER全文 → M02C-T-A05来源链未变。FP32、speed=1、非流式。</p>
<audio controls preload="none" src="''' + url(source_audio) + '''" aria-label="R1 原48k参考"></audio>
<p>模型：FunAudioLLM/Fun-CosyVoice3-0.5B-2512 · revision ''' + REVISION + '''</p>
<p>CosyVoice源码：074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc</p></section>''' + ''.join(sections) + '</main></html>\n'


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--text-set", type=Path, required=True)
    args = parser.parse_args()
    require(args.output_dir.resolve().parent == args.source_root.resolve() / "local/voice_demo/M05A3"
            and args.report_dir.resolve().parent == args.source_root.resolve() / "local/reports/M05A3"
            and args.report_dir.name == args.output_dir.name, "Expected matching M05A3 output/report run directories")
    report_path = args.report_dir / "technical_validation.json"
    try:
        report, items, records, baseline, source_audio = validate(
            args.report_dir / "results.jsonl", args.report_dir / "segments.jsonl",
            args.output_dir, args.text_set, args.source_root)
    except Exception as exc:
        write_json(report_path, {"status": "failed", "error_type": type(exc).__name__, "error": str(exc),
                                 "listening_status": "not_evaluated"})
        raise
    (args.output_dir / "index.html").write_text(listening_html(
        items, records, baseline, source_audio, args.source_root, args.output_dir, report), encoding="utf-8")
    write_json(args.report_dir / "resource_summary.json", resource_summary(args.report_dir, records, report))
    write_json(args.report_dir / "regression_references.json", report["regression_references"])
    write_json(report_path, report)
    files = []
    for kind, root in (("output", args.output_dir), ("report", args.report_dir)):
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != "delivery_manifest.json":
                files.append({"kind": kind, "relative_path": path.relative_to(root).as_posix(),
                              "size_bytes": path.stat().st_size, "sha256": sha256(path)})
    write_json(args.report_dir / "delivery_manifest.json", {"schema_version": 1, "run_id": args.output_dir.name,
                                                           "files": files, "listening_status": "pending_user_review"})
    print(json.dumps({"status": report["status"], "new_whole_files": 8, "raw_segments": 2,
                      "actual_generation_calls": 9, "total_new_whole_audio_s": report["total_new_whole_audio_s"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
