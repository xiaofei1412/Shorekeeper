"""Resolve the two approved M05A references without modifying source assets."""

import hashlib
import json
import math
from pathlib import Path
import wave


REFERENCES = {
    "R1": {
        "asset_id": "5ee6fa77-4bad-45f0-b7a5-ef99c8bdc7ff",
        "source_sha256": "97520478cd4a4b848909d810cd5703c14476dfd084cd14f7034eb07c9dc8aed2",
        "candidate_id": "M02A-A05",
        "transcript_id": "M02C-T-A05",
        "supersedes_transcript_id": "M02B-T-A05",
    },
    "R2": {
        "asset_id": "9a45ad09-bdf5-4893-b314-8800aea81a4a",
        "source_sha256": "87e2a31feaa44f7b13a57047ad016d6101f0a9ba9f410d46da92ea3d939c75f1",
        "candidate_id": "M02A-A03",
        "transcript_id": "M02C-T-A03",
        "supersedes_transcript_id": "M02B-T-A03",
    },
}
MANIFEST = "local/assets/manifests/PLAT-01.jsonl"
TRANSCRIPTS = "local/assets/batches/B20261116-M02C-01/derived/transcript_supplements.jsonl"
FEEDBACK = "local/assets/batches/B20261116-OWNER-01/owner_feedback.json"


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _within(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Input path escapes the source root")
    return path


def _one(records, field, value):
    matches = [record for record in records if record.get(field) == value]
    if len(matches) != 1:
        raise ValueError("Expected exactly one matching " + field)
    return matches[0]


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def resolve_reference(source_root: Path, reference_id: str, derived_dir: Path, *, original=False) -> dict:
    """Validate provenance; optionally pass the untouched 48 kHz original upstream."""
    if reference_id not in REFERENCES:
        raise ValueError("reference_id must be R1 or R2")
    expected = REFERENCES[reference_id]
    source_root = Path(source_root).resolve()
    derived_dir = Path(derived_dir).resolve()
    if derived_dir.is_relative_to(source_root / "local" / "assets"):
        raise ValueError("Derived audio must not be written into source assets")
    manifest_path = _within(source_root, MANIFEST)
    transcript_path = _within(source_root, TRANSCRIPTS)
    feedback_path = _within(source_root, FEEDBACK)
    manifest = _one(_jsonl(manifest_path), "asset_id", expected["asset_id"])
    transcript = _one(_jsonl(transcript_path), "transcript_id", expected["transcript_id"])
    feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    owner = _one(feedback["transcript_supplements"], "asset_id", expected["asset_id"])
    feedback_sha256 = _sha256(feedback_path)
    if manifest.get("kind") != "audio" or manifest.get("sha256") != expected["source_sha256"]:
        raise ValueError("Reference manifest identity/hash mismatch")
    for record in (owner, transcript):
        if (record.get("asset_id") != expected["asset_id"]
                or record.get("source_sha256") != expected["source_sha256"]
                or record.get("text_status") != "owner_provided_complete_text"):
            raise ValueError("Reference transcript identity/hash/status mismatch")
    if (owner.get("candidate_id") != expected["candidate_id"]
            or owner.get("base_transcript_id") != expected["supersedes_transcript_id"]
            or transcript.get("supersedes_transcript_id") != expected["supersedes_transcript_id"]
            or transcript.get("feedback_ref") != FEEDBACK
            or transcript.get("feedback_id") != feedback.get("feedback_id")
            or transcript.get("feedback_sha256") != feedback_sha256
            or transcript.get("text_verbatim") != owner.get("text_verbatim")
            or not isinstance(transcript.get("text_verbatim"), str)
            or not transcript["text_verbatim"].strip()):
        raise ValueError("Owner feedback/transcript provenance mismatch")
    source_path = _within(source_root, manifest["relative_path"])
    if _sha256(source_path) != expected["source_sha256"]:
        raise ValueError("Source audio hash mismatch")
    with wave.open(str(source_path), "rb") as stream:
        frames = stream.getnframes()
        rate = stream.getframerate()
        channels = stream.getnchannels()
        width = stream.getsampwidth()
        pcm = stream.readframes(frames)
        if (stream.getcomptype() != "NONE" or frames <= 0 or rate <= 0
                or len(pcm) != frames * channels * width or frames / rate > 30):
            raise ValueError("Reference must be a complete nonempty PCM WAV no longer than 30 seconds")
    import numpy as np
    from scipy.signal import resample_poly
    import soundfile as sf

    audio, decoded_rate = sf.read(str(source_path), dtype="float32", always_2d=True)
    if decoded_rate != rate or audio.shape != (frames, channels) or not np.isfinite(audio).all():
        raise ValueError("Decoded reference does not match WAV metadata")
    result = {
        "reference_id": reference_id,
        "asset_id": expected["asset_id"],
        "source_sha256": expected["source_sha256"],
        "source_relative_path": manifest["relative_path"],
        "prompt_text": transcript["text_verbatim"],
        "prompt_source": {
            "transcript_id": expected["transcript_id"],
            "transcript_path": TRANSCRIPTS,
            "transcript_file_sha256": _sha256(transcript_path),
            "feedback_id": feedback["feedback_id"],
            "feedback_path": FEEDBACK,
            "feedback_sha256": feedback_sha256,
            "text_field": "text_verbatim",
            "alignment_status": transcript.get("alignment_status"),
        },
        "original_audio": {
            "sample_rate": rate, "channels": channels, "sample_width_bytes": width,
            "frames": frames, "duration_s": frames / rate, "format": "PCM WAV",
        },
        "rights": manifest.get("rights"),
    }
    if original:
        if (rate, channels, width) != (48000, 1, 3):
            raise ValueError("M05A2 requires the original 48 kHz mono PCM24 reference")
        result.update(input_path=str(source_path), input_sha256=expected["source_sha256"],
                      input_mode="original48k")
        return result
    mono = audio.mean(axis=1)
    divisor = math.gcd(rate, 16000)
    converted = resample_poly(mono, 16000 // divisor, rate // divisor)
    derived_dir.mkdir(parents=True, exist_ok=True)
    derived_path = derived_dir / (reference_id + "-16000-mono-pcm16.wav")
    temporary_path = derived_dir / (reference_id + "-16000-mono-pcm16.pending.wav")
    if temporary_path.exists():
        raise FileExistsError("Pending derived file already exists: " + str(temporary_path))
    sf.write(str(temporary_path), converted, 16000, subtype="PCM_16", format="WAV")
    if derived_path.exists():
        identical = _sha256(derived_path) == _sha256(temporary_path)
        temporary_path.unlink()
        if not identical:
            raise FileExistsError("Refusing to overwrite different derived audio: " + str(derived_path))
    else:
        temporary_path.rename(derived_path)
    result.update({
        "derived_path": str(derived_path),
        "derived_sha256": _sha256(derived_path),
        "derived_audio": {
            "sample_rate": 16000, "channels": 1, "subtype": "PCM_16",
            "frames": len(converted), "duration_s": len(converted) / 16000,
            "resampler": "scipy.signal.resample_poly",
            "up": 16000 // divisor, "down": rate // divisor,
            "mono_conversion": "arithmetic channel mean",
        },
    })
    return result
