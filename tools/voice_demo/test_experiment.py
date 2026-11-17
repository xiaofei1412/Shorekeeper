"""Check the actual M05A2 text mapping and non-destructive join frame accounting."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf

from experiment import build_jobs, join_segments


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.items = json.loads((Path(__file__).parent / "text_sets/m05a-v1.json").read_text(
            encoding="utf-8"))["items"]

    def test_exact_matrix_and_sentence_ranges_reconstruct_every_character(self):
        jobs = build_jobs(self.items)
        self.assertEqual(len(jobs), 13)
        self.assertEqual({job["text_id"] for job in jobs if job["group"] == "C"},
                         {"T01", "T04", "T06", "T07", "T08"})
        for job in jobs:
            segments = job["segments"]
            self.assertEqual("".join(part["display_text"] for part in segments), job["display_text"])
            previous = 0
            for index, part in enumerate(segments):
                self.assertEqual(part["segment_index"], index)
                self.assertEqual(part["char_start"], previous)
                self.assertEqual(job["display_text"][part["char_start"]:part["char_end"]],
                                 part["display_text"])
                previous = part["char_end"]
            self.assertEqual(previous, len(job["display_text"]))
            if job["change"] == "complete_sentence_groups":
                self.assertEqual(len(segments), 2)
                self.assertTrue(all(part["display_text"][-1] in "。？" for part in segments))
                self.assertEqual("".join(part["spoken_text"] for part in segments), job["spoken_text"])

    def test_t06_reading_preserves_identifier_decimal_unit_and_connector_mapping(self):
        job = next(job for job in build_jobs(self.items) if (job["group"], job["text_id"]) == ("C", "T06"))
        self.assertEqual(job["display_text"], "这根 USB Type-C 线长 1.5 米，编号是 A07。")
        self.assertEqual(job["spoken_text"], "这根 优 艾思 比 太普 西 线长 一点五 米，编号是 诶 零 七。")
        self.assertEqual({part["source"] for part in job["replacements"]}, {"USB Type-C", "1.5", "A07"})
        self.assertEqual(len(job["segments"]), 1)

    def test_changed_t06_source_is_rejected(self):
        next(item for item in self.items if item["id"] == "T06")["text"] = "线长15米，编号A70。"
        with self.assertRaises(ValueError):
            build_jobs(self.items)

    def test_join_preserves_pcm_and_tops_up_only_short_native_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = np.concatenate((np.zeros(100, "int16"), np.full(1000, 2000, "int16"),
                                    np.zeros(800, "int16")))
            second = np.concatenate((np.zeros(400, "int16"), np.full(600, -2000, "int16"),
                                     np.zeros(5000, "int16")))
            third = np.concatenate((np.zeros(200, "int16"), np.full(900, 2000, "int16")))
            paths = [root / (str(index) + ".wav") for index in range(3)]
            for path, audio in zip(paths, (first, second, third)):
                sf.write(str(path), audio, 24000, subtype="PCM_16")
            joined = root / "joined.wav"
            mapping = join_segments(paths, joined)
            decoded, rate = sf.read(str(joined), dtype="int16")
            self.assertEqual(rate, 24000)
            self.assertEqual(mapping["boundaries"][0]["added_gap_frames"], 4320 - 1200)
            self.assertEqual(mapping["boundaries"][1]["added_gap_frames"], 0)
            for segment, original in zip(mapping["segments"], (first, second, third)):
                np.testing.assert_array_equal(decoded[segment["frame_start"]:segment["frame_end"]], original)
            for boundary in mapping["boundaries"]:
                self.assertTrue(np.all(decoded[boundary["output_gap_start_frame"]:
                                               boundary["output_gap_end_frame"]] == 0))
            self.assertEqual(len(decoded), mapping["frames"])
            with self.assertRaises(FileExistsError):
                join_segments(paths, joined)


if __name__ == "__main__":
    unittest.main()
