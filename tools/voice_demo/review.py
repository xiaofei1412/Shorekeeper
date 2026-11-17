"""Validate the complete M05A WAV matrix and build an offline listening page."""

import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import math
from pathlib import Path
import re
import wave

from inputs import REFERENCES
from download_model import REVISION


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate(results_path, output_dir, text_set_path):
    import numpy as np
    import soundfile as sf

    text_set = json.loads(text_set_path.read_text(encoding="utf-8"))
    items = text_set["items"]
    ids = ["T%02d" % number for number in range(1, 9)]
    if (len(items) != 8 or sorted(item["id"] for item in items) != ids
            or text_set.get("official_dialogue") is not False):
        raise ValueError("Text set must contain unique T01..T08 and declare nonofficial dialogue")
    texts = {item["id"]: item["text"] for item in items}
    text_set_sha256 = sha256(text_set_path)
    records = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    expected = {(reference, text_id) for reference in ("R1", "R2") for text_id in ids}
    if len(records) != 16:
        raise ValueError("Exactly 16 result records are required")
    by_key = {}
    checked = []
    output_dir = output_dir.resolve()
    for record in records:
        key = (record["reference_id"], record["text_id"])
        if key not in expected or key in by_key:
            raise ValueError("Unknown or duplicate reference/text pair: " + str(key))
        if record.get("status") != "succeeded":
            raise ValueError("Result did not succeed: " + str(key))
        reference = record["reference"]
        expected_reference = REFERENCES[key[0]]
        if (reference["asset_id"] != expected_reference["asset_id"]
                or reference["source_sha256"] != expected_reference["source_sha256"]
                or record["prompt_text_sent"] != "You are a helpful assistant.<|endofprompt|>" + reference["prompt_text"]
                or record["model_revision"] != REVISION):
            raise ValueError("Reference/prompt/model association mismatch: " + str(key))
        derived = Path(reference["derived_path"]).resolve()
        if not derived.is_relative_to(output_dir / "references") or sha256(derived) != reference["derived_sha256"]:
            raise ValueError("Derived reference path/hash mismatch: " + str(key))
        budget = record["budget"]
        if (not budget["fits"] or budget["I"] + budget["R"] + budget["S"] > budget["C"]
                or record["resources"]["sample_count"] <= 0):
            raise ValueError("Missing budget/resource evidence: " + str(key))
        if (record.get("text") != texts[key[1]]
                or record.get("text_set_id") != text_set["text_set_id"]
                or record.get("text_set_sha256") != text_set_sha256):
            raise ValueError("Result text/set provenance mismatch: " + str(key))
        name = record.get("wav")
        if name != key[0] + "_" + key[1] + ".wav":
            raise ValueError("WAV filename must match its reference/text pair")
        path = (output_dir / name).resolve()
        if not path.is_relative_to(output_dir) or not path.is_file():
            raise ValueError("WAV path is missing or escapes the output directory")
        digest = sha256(path)
        if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("wav_sha256"))) or digest != record["wav_sha256"]:
            raise ValueError("WAV hash mismatch: " + name)
        with wave.open(str(path), "rb") as stream:
            frames = stream.getnframes()
            if (stream.getcomptype() != "NONE" or stream.getframerate() != 24000
                    or stream.getnchannels() != 1 or stream.getsampwidth() != 2 or frames <= 0):
                raise ValueError("WAV must be nonempty 24 kHz mono PCM16: " + name)
            if len(stream.readframes(frames)) != frames * 2:
                raise ValueError("WAV has incomplete PCM frames: " + name)
        audio, rate = sf.read(str(path), dtype="float64", always_2d=True)
        info = sf.info(str(path))
        if (rate != 24000 or info.subtype != "PCM_16" or audio.shape != (frames, 1)
                or not np.isfinite(audio).all() or not np.any(audio)):
            raise ValueError("WAV failed full decoding/finite/non-silent checks: " + name)
        duration = frames / 24000
        if (record.get("frames") != frames or record.get("sample_rate") != 24000
                or record.get("channels") != 1 or record.get("subtype") != "PCM_16"
                or not math.isclose(record["duration_s"], duration, abs_tol=1e-9)):
            raise ValueError("Result WAV metadata mismatch: " + name)
        if (not isinstance(record.get("synthesis_s"), (int, float))
                or isinstance(record["synthesis_s"], bool)
                or not math.isfinite(record["synthesis_s"]) or record["synthesis_s"] < 0
                or record.get("device") not in ("cuda", "cpu")
                or not isinstance(record.get("onnx_providers"), dict)):
            raise ValueError("Missing or invalid measured time/device: " + name)
        by_key[key] = record
        checked.append({"reference_id": key[0], "text_id": key[1], "wav": name,
                        "sha256": digest, "frames": frames, "sample_rate": rate,
                        "channels": 1, "subtype": info.subtype, "duration_s": duration,
                        "synthesis_s": record["synthesis_s"],
                        "rtf": record["synthesis_s"] / duration,
                        "absolute_peak": float(np.abs(audio).max()),
                        "pcm_clipped_samples": int((np.abs(audio) >= 32767 / 32768).sum()),
                        "device": record["device"], "onnx_providers": record["onnx_providers"]})
    if set(by_key) != expected:
        raise ValueError("The R1/R2 by T01..T08 matrix is incomplete")
    if {path.name for path in output_dir.glob("*.wav")} != {record["wav"] for record in records}:
        raise ValueError("Output directory contains unexpected top-level WAV files")
    report = {
        "status": "passed", "validated_at": datetime.now(timezone.utc).isoformat(),
        "results_sha256": sha256(results_path), "text_set_sha256": text_set_sha256,
        "expected_files": 16, "validated_files": len(checked), "files": checked,
        "total_audio_s": sum(item["duration_s"] for item in checked),
        "signal_check": "All decoded samples finite; at least one nonzero PCM sample in each WAV.",
        "listening_status": "pending_user_review",
        "scope": "File provenance, complete PCM decoding and signal validity only; no voice-similarity or listening judgment.",
    }
    return report, items, by_key


def listening_html(items, records):
    sections = []
    for item in sorted(items, key=lambda value: value["id"]):
        cells = []
        for reference in ("R1", "R2"):
            record = records[(reference, item["id"])]
            providers = json.dumps(record["onnx_providers"], ensure_ascii=False)
            cells.append(
                '<article><h3>' + reference + '</h3><audio controls preload="none" src="'
                + escape(record["wav"], quote=True) + '" aria-label="'
                + escape(reference + " " + item["id"], quote=True) + '"></audio>'
                + '<p>时长 %.3f 秒 · 合成 %.3f 秒</p>' % (record["duration_s"], record["synthesis_s"])
                + '<p class="device">Torch设备：' + escape(record["device"])
                + '<br>ONNX：' + escape(providers) + '</p></article>')
        sections.append('<section><h2>' + escape(item["id"]) + '</h2><p class="text">'
                        + escape(item["text"]) + '</p><div class="pair">' + ''.join(cells) + '</div></section>')
    return '''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>M05A 新合成语音试听</title><style>
body{font:16px/1.65 system-ui,sans-serif;color:#18242a;background:#f1f5f4;margin:0;padding:28px}
main{max-width:1050px;margin:auto}h1{font-size:28px;margin:0}h2{font-size:17px;margin:0;color:#36635e}
section{background:white;padding:22px;border:1px solid #d2dedb;border-radius:10px;margin:20px 0}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}article{min-width:0;background:#f5f8f7;padding:14px;border-radius:7px}
h3{margin:0 0 10px}.text{font-size:18px;margin:8px 0 20px}audio{width:100%}.device{font-size:12px;overflow-wrap:anywhere;color:#4b6168}
.notice{background:#e1ede9;padding:14px;border-radius:7px}article p{margin:8px 0}
@media(max-width:650px){body{padding:14px}.pair{grid-template-columns:1fr}}
@media print{body{background:white;padding:0}section{break-inside:avoid}audio{display:none}}
</style><main><h1>M05A 新合成语音试听</h1>
<p>R1：初奏，约11.14秒参考。R2：烦恼，约23.61秒参考。同组8句、共16段新合成语音。</p>
<p class="notice">以下文字为手工创作的试听测试句，非官方台词。请比较音色、咬字、停顿、重复漏字及长句表现。技术校验不代表声线或听感通过；听审结论待用户填写。</p>
<p>设备与ONNX执行来源逐段按实测结果列出。点击每段播放器可直接试听，页面不加载外部资源。</p>
''' + ''.join(sections) + '</main></html>\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--text-set", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        report, items, records = validate(args.results, args.output_dir, args.text_set)
    except Exception as exc:
        report = {"status": "failed", "validated_at": datetime.now(timezone.utc).isoformat(),
                  "error_type": type(exc).__name__, "error": str(exc),
                  "listening_status": "not_evaluated"}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    (args.output_dir / "index.html").write_text(listening_html(items, records), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "validated_files": report["validated_files"],
                      "total_audio_s": report["total_audio_s"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
