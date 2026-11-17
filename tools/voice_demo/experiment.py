"""The fixed M05A2 text variants and one non-destructive sentence join policy."""

import hashlib
import math
from pathlib import Path
import re


GROUPED_TEXT_IDS = {"T01", "T04", "T07", "T08"}
READINGS = [
    {"source": "USB Type-C", "spoken": "优 艾思 比 太普 西",
     "meaning": "USB letters followed by Type-C; pronunciation experiment only"},
    {"source": "1.5", "spoken": "一点五", "meaning": "decimal 1.5; metre unit retained"},
    {"source": "A07", "spoken": "诶 零 七", "meaning": "identifier A, zero, seven in order"},
]
JOIN_POLICY = {
    "name": "preserve_native_edges_top_up_to_180ms",
    "silence_threshold_dbfs": -50,
    "minimum_boundary_s": 0.18,
    "edge_measurement": "consecutive samples at or below amplitude threshold",
    "trimming": False,
    "crossfade": False,
    "speed_change": False,
}


def sentence_ranges(text):
    """Split only at full-sentence punctuation, retaining every input character."""
    return [(match.start(), match.end())
            for match in re.finditer(r".+?(?:[。！？!?]+|$)", text, flags=re.DOTALL)]


def build_jobs(items):
    """Return B's eight original texts, then C's five independently varied texts."""
    expected = {"T%02d" % number for number in range(1, 9)}
    if len(items) != 8 or {item["id"] for item in items} != expected:
        raise ValueError("M05A2 requires the unique original T01..T08 text set")
    jobs = []
    for group in ("B", "C"):
        for item in items:
            text_id, text = item["id"], item["text"]
            if group == "C" and text_id not in GROUPED_TEXT_IDS | {"T06"}:
                continue
            change = "original_target_text"
            spoken_text = text
            replacements = []
            if group == "C" and text_id == "T06":
                change = "explicit_number_and_english_reading"
                for replacement in READINGS:
                    if spoken_text.count(replacement["source"]) != 1:
                        raise ValueError("T06 must contain each pinned reading source exactly once")
                    spoken_text = spoken_text.replace(replacement["source"], replacement["spoken"])
                replacements = [dict(replacement) for replacement in READINGS]
            ranges = [(0, len(text))]
            if group == "C" and text_id in GROUPED_TEXT_IDS:
                change = "complete_sentence_groups"
                ranges = sentence_ranges(text)
                if len(ranges) != 2:
                    raise ValueError("The pinned grouped text must have exactly two complete sentences")
            segments = [{"segment_id": "%s_%s_S%02d" % (group, text_id, index + 1),
                         "segment_index": index, "char_start": start, "char_end": end,
                         "display_text": text[start:end],
                         "spoken_text": spoken_text if replacements else text[start:end]}
                        for index, (start, end) in enumerate(ranges)]
            jobs.append({"group": group, "text_id": text_id, "category": item["category"],
                         "display_text": text, "spoken_text": spoken_text,
                         "change": change, "replacements": replacements,
                         "segments": segments})
    return jobs


def join_segments(segment_paths, output_path, sample_rate=24000):
    """Keep all native PCM frames and add only enough silence for a 180 ms boundary."""
    import numpy as np
    import soundfile as sf

    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError("Refusing to overwrite joined WAV: " + str(output_path))
    if len(segment_paths) < 2:
        raise ValueError("Sentence joining needs at least two segments")
    threshold = math.ceil(32768 * 10 ** (JOIN_POLICY["silence_threshold_dbfs"] / 20))
    minimum_frames = round(sample_rate * JOIN_POLICY["minimum_boundary_s"])
    arrays, segments = [], []
    for path in map(Path, segment_paths):
        audio, rate = sf.read(str(path), dtype="int16", always_2d=True)
        info = sf.info(str(path))
        if rate != sample_rate or info.subtype != "PCM_16" or audio.shape[1] != 1 or not len(audio):
            raise ValueError("Join inputs must be nonempty mono PCM16 at the output sample rate")
        active = np.flatnonzero(np.abs(audio[:, 0].astype("int32")) > threshold)
        if not len(active):
            raise ValueError("Join input has no samples above the silence threshold")
        arrays.append(audio)
        segments.append({"input_wav": str(path.resolve()),
                         "wav_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "frames": len(audio), "leading_silence_frames": int(active[0]),
                         "trailing_silence_frames": len(audio) - int(active[-1]) - 1})
    pieces, boundaries, cursor = [], [], 0
    for index, (audio, segment) in enumerate(zip(arrays, segments)):
        segment.update(frame_start=cursor, frame_end=cursor + len(audio))
        pieces.append(audio)
        cursor += len(audio)
        if index + 1 < len(segments):
            native = segment["trailing_silence_frames"] + segments[index + 1]["leading_silence_frames"]
            gap = max(0, minimum_frames - native)
            boundaries.append({"after_segment_index": index, "native_silence_frames": native,
                               "added_gap_frames": gap, "output_gap_start_frame": cursor,
                               "output_gap_end_frame": cursor + gap})
            if gap:
                pieces.append(np.zeros((gap, 1), dtype="int16"))
            cursor += gap
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as stream:
        sf.write(stream, np.concatenate(pieces), sample_rate, subtype="PCM_16", format="WAV")
    return {"policy": dict(JOIN_POLICY, silence_threshold_pcm=threshold,
                           minimum_boundary_frames=minimum_frames),
            "sample_rate": sample_rate, "frames": cursor,
            "segments": segments, "boundaries": boundaries}
