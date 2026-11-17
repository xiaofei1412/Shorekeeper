"""M05A3 matrix contract and optional read-only checks against a real delivery."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from experiment_m05a3 import build_jobs, REGRESSION_KEYS

SOURCE_ROOT = None
RUN_ID = None
TEXT_SET = Path(__file__).parent / "text_sets/m05a-v1.json"


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.items = json.loads(TEXT_SET.read_text(encoding="utf-8"))["items"]
        self.plans = build_jobs(self.items)

    def test_exact_whole_and_call_matrix_with_all_short_seeds(self):
        self.assertEqual(len(self.plans), 8)
        self.assertEqual(sum(len(p["segments"]) for p in self.plans), 9)
        self.assertEqual({(p["text_id"], p["seed"]) for p in self.plans[:6]},
                         {(tid, seed) for tid in ("T02", "N01", "N02") for seed in (20261118, 20261119)})
        for plan in self.plans[:6]:
            self.assertEqual(len(plan["segments"]), 1)
            self.assertEqual(plan["display_text"], plan["spoken_text"])
            self.assertNotIn(" ", plan["spoken_text"])
            self.assertFalse(plan["official_dialogue"] or plan["training_admission"] or plan["character_holdout"])

    def test_names_only_change_three_punctuation_characters_without_split(self):
        plan = next(p for p in self.plans if p["text_id"] == "T05")
        self.assertEqual(plan["spoken_text"], "黑海岸，索诺拉，泰缇斯。这三个名字，请听清楚。")
        self.assertEqual([(a, b) for a, b in zip(plan["display_text"], plan["spoken_text"]) if a != b],
                         [("、", "，"), ("、", "，"), ("，", "。")])
        self.assertEqual(len(plan["segments"]), 1)
        self.assertEqual(plan["seed"], 20261117)

    def test_mixed_text_keeps_original_spelling_and_exact_semantic_boundary(self):
        plan = next(p for p in self.plans if p["text_id"] == "T06")
        self.assertEqual(plan["display_text"], plan["spoken_text"])
        self.assertEqual([s["spoken_text"] for s in plan["segments"]],
                         ["这根 USB Type-C 线长 1.5 米，", "编号是 A07。"])
        self.assertEqual([(s["char_start"], s["char_end"]) for s in plan["segments"]], [(0, 23), (23, 31)])
        self.assertEqual("".join(s["spoken_text"] for s in plan["segments"]), plan["spoken_text"])
        for changed in ("这根 USB Type-C 线长 1.5 米，编号是 A7。", "这根 USB Type C 线长 1.5 米，编号是 A07。"):
            items = deepcopy(self.items)
            next(item for item in items if item["id"] == "T06")["text"] = changed
            with self.assertRaises(ValueError):
                build_jobs(items)

    def test_regression_set_retains_only_the_five_approved_results(self):
        self.assertEqual(set(REGRESSION_KEYS), {("C", "T01"), ("B", "T03"), ("C", "T04"),
                                               ("C", "T07"), ("C", "T08")})
        self.assertNotIn(("C", "T06"), REGRESSION_KEYS)


class DeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SOURCE_ROOT is None:
            raise unittest.SkipTest("Pass --source-root and --run-id to validate actual delivery")

    def setUp(self):
        import review_m05a3
        self.reviewer = review_m05a3
        self.report = SOURCE_ROOT / "local/reports/M05A3" / RUN_ID
        self.output = SOURCE_ROOT / "local/voice_demo/M05A3" / RUN_ID
        self.result_path = self.report / "results.jsonl"
        self.segment_path = self.report / "segments.jsonl"
        self.results = review_m05a3.jsonl(self.result_path)
        self.segments = review_m05a3.jsonl(self.segment_path)

    def validate(self):
        return self.reviewer.validate(self.result_path, self.segment_path, self.output, TEXT_SET, SOURCE_ROOT)

    def rejects(self, change):
        results, segments = deepcopy(self.results), deepcopy(self.segments)
        change(results, segments)
        real_jsonl = self.reviewer.jsonl

        def altered(path):
            if path.resolve() == self.result_path.resolve():
                return results
            if path.resolve() == self.segment_path.resolve():
                return segments
            return real_jsonl(path)

        with patch.object(self.reviewer, "jsonl", side_effect=altered):
            with self.assertRaises(ValueError):
                self.validate()

    def test_real_delivery_validates(self):
        report = self.validate()[0]
        self.assertEqual(report["status"], "passed")

    def test_duplicate_whole_result_rejected(self):
        self.rejects(lambda results, segments: results.append(deepcopy(results[0])))

    def test_wrong_seed_rejected(self):
        self.rejects(lambda results, segments: results[0].update(seed=20261117))

    def test_short_word_internal_space_rejected(self):
        self.rejects(lambda results, segments: results[0].update(spoken_text="嗯，我 在。"))

    def test_mixed_identifier_zero_removed_rejected(self):
        def change(results, segments):
            next(r for r in results if r["group"] == "G")["spoken_text"] = "这根 USB Type-C 线长 1.5 米，编号是 A7。"
        self.rejects(change)

    def test_semantic_boundary_shift_rejected(self):
        def change(results, segments):
            segments[0]["char_end"] -= 1
            next(r for r in results if r["group"] == "G")["segments"][0]["char_end"] -= 1
        self.rejects(change)

    def test_pcm_mapping_shift_rejected(self):
        def change(results, segments):
            next(r for r in results if r["group"] == "G")["join_map"]["segments"][0]["frame_start"] += 1
        self.rejects(change)

    def test_16k_reference_path_rejected(self):
        self.rejects(lambda results, segments: results[0]["reference"].update(input_mode="derived16k"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--run-id")
    args, remaining = parser.parse_known_args()
    if bool(args.source_root) != bool(args.run_id):
        parser.error("--source-root and --run-id are required together")
    SOURCE_ROOT, RUN_ID = args.source_root, args.run_id
    unittest.main(argv=[__file__] + remaining)
