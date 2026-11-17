"""Input provenance regressions; optional --source-root validates the real inputs."""

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import inputs


REAL_SOURCE_ROOT = None
REAL_DERIVED_DIR = None
HAS_AUDIO_DEPS = all(importlib.util.find_spec(name) for name in ("numpy", "scipy", "soundfile"))


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="m05a-input-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.derived = self.root / "derived"
        self.source_relative = "local/assets/raw/PLAT-01/test.wav"
        self.source = self.root / self.source_relative
        self.source.parent.mkdir(parents=True)
        self._write_wav(1)
        self.expected = copy.deepcopy(inputs.REFERENCES["R1"])
        self.expected["source_sha256"] = inputs._sha256(self.source)
        self.patcher = patch.dict(inputs.REFERENCES, {"R1": self.expected})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.manifest = {
            "asset_id": self.expected["asset_id"], "kind": "audio",
            "relative_path": self.source_relative, "sha256": self.expected["source_sha256"],
        }
        self.owner = {
            "asset_id": self.expected["asset_id"],
            "source_sha256": self.expected["source_sha256"],
            "candidate_id": self.expected["candidate_id"],
            "base_transcript_id": self.expected["supersedes_transcript_id"],
            "text_status": "owner_provided_complete_text", "text_verbatim": "测试参考文字。",
        }
        self.feedback = {"feedback_id": "fixture-feedback", "transcript_supplements": [self.owner]}
        self.transcript = {
            "transcript_id": self.expected["transcript_id"],
            "asset_id": self.expected["asset_id"],
            "source_sha256": self.expected["source_sha256"],
            "supersedes_transcript_id": self.expected["supersedes_transcript_id"],
            "feedback_ref": inputs.FEEDBACK, "feedback_id": "fixture-feedback",
            "text_status": "owner_provided_complete_text", "text_verbatim": "测试参考文字。",
            "alignment_status": "pending",
        }
        self._write_records()

    def _write_wav(self, seconds):
        with wave.open(str(self.source), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(48000)
            stream.writeframes(b"\x00\x01" * (48000 * seconds))

    def _write_json(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def _write_records(self):
        self._write_json(inputs.MANIFEST, self.manifest)
        self._write_json(inputs.FEEDBACK, self.feedback)
        self.transcript["feedback_sha256"] = inputs._sha256(self.root / inputs.FEEDBACK)
        self._write_json(inputs.TRANSCRIPTS, self.transcript)

    def resolve(self):
        return inputs.resolve_reference(self.root, "R1", self.derived)

    def test_unknown_reference_rejected_before_io(self):
        with self.assertRaisesRegex(ValueError, "R1 or R2"):
            inputs.resolve_reference(self.root / "missing", "R3", self.derived)

    def test_audio_hash_tampering_rejected(self):
        with self.source.open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "Source audio hash"):
            self.resolve()

    def test_feedback_hash_tampering_rejected(self):
        self.transcript["feedback_sha256"] = "0" * 64
        self._write_json(inputs.TRANSCRIPTS, self.transcript)
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.resolve()

    def test_changed_owner_text_rejected_even_with_updated_feedback_hash(self):
        self.owner["text_verbatim"] = "另外一段文字。"
        self._write_records()
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.resolve()

    def test_source_path_escape_rejected(self):
        self.manifest["relative_path"] = "../outside.wav"
        self._write_records()
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.resolve()

    def test_duplicate_manifest_identity_rejected(self):
        path = self.root / inputs.MANIFEST
        path.write_text(path.read_text(encoding="utf-8") + "\n" + json.dumps(self.manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.resolve()

    def test_long_reference_rejected(self):
        self._write_wav(31)
        digest = inputs._sha256(self.source)
        self.expected["source_sha256"] = digest
        self.manifest["sha256"] = digest
        self.owner["source_sha256"] = digest
        self.transcript["source_sha256"] = digest
        self._write_records()
        with self.assertRaisesRegex(ValueError, "30 seconds"):
            self.resolve()

    def test_output_into_source_assets_rejected(self):
        with self.assertRaisesRegex(ValueError, "source assets"):
            inputs.resolve_reference(self.root, "R1", self.source.parent)

    @unittest.skipUnless(HAS_AUDIO_DEPS, "numpy/scipy/soundfile required")
    def test_original48k_uses_exact_source_without_deriving(self):
        with wave.open(str(self.source), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(3)
            stream.setframerate(48000)
            stream.writeframes(b"\x00\x00\x01" * 48000)
        digest = inputs._sha256(self.source)
        self.expected["source_sha256"] = digest
        self.manifest["sha256"] = digest
        self.owner["source_sha256"] = digest
        self.transcript["source_sha256"] = digest
        self._write_records()
        result = inputs.resolve_reference(self.root, "R1", self.derived, original=True)
        self.assertEqual(Path(result["input_path"]), self.source.resolve())
        self.assertEqual(result["input_sha256"], digest)
        self.assertNotIn("derived_path", result)
        self.assertFalse(self.derived.exists())

    @unittest.skipUnless(HAS_AUDIO_DEPS, "numpy/scipy/soundfile required")
    def test_original48k_rejects_lower_precision_reference(self):
        with self.assertRaisesRegex(ValueError, "48 kHz mono PCM24"):
            inputs.resolve_reference(self.root, "R1", self.derived, original=True)

    @unittest.skipUnless(HAS_AUDIO_DEPS, "numpy/scipy/soundfile required")
    def test_conversion_is_complete_repeatable_and_preserves_source(self):
        before = self.source.read_bytes()
        result = self.resolve()
        repeated = self.resolve()
        self.assertEqual(result["derived_sha256"], repeated["derived_sha256"])
        self.assertEqual(before, self.source.read_bytes())
        self.assertEqual(result["prompt_text"], self.owner["text_verbatim"])
        with wave.open(result["derived_path"], "rb") as stream:
            self.assertEqual((stream.getframerate(), stream.getnchannels(), stream.getsampwidth()), (16000, 1, 2))
            self.assertEqual(stream.getnframes(), 16000)
            self.assertEqual(len(stream.readframes(16000)), 32000)

    @unittest.skipUnless(HAS_AUDIO_DEPS, "numpy/scipy/soundfile required")
    def test_different_existing_output_is_not_overwritten(self):
        self.derived.mkdir()
        path = self.derived / "R1-16000-mono-pcm16.wav"
        path.write_bytes(b"existing unknown data")
        with self.assertRaises(FileExistsError):
            self.resolve()
        self.assertEqual(path.read_bytes(), b"existing unknown data")
        self.assertFalse((self.derived / "R1-16000-mono-pcm16.pending.wav").exists())


class RealReferenceTests(unittest.TestCase):
    def test_real_R1_R2_hash_chain_and_conversion(self):
        if REAL_SOURCE_ROOT is None or not HAS_AUDIO_DEPS:
            self.skipTest("pass --source-root and --derived-dir; audio deps required")
        for reference_id in ("R1", "R2"):
            with self.subTest(reference_id=reference_id):
                result = inputs.resolve_reference(REAL_SOURCE_ROOT, reference_id, REAL_DERIVED_DIR)
                self.assertLessEqual(result["original_audio"]["duration_s"], 30)
                self.assertEqual(result["source_sha256"], inputs.REFERENCES[reference_id]["source_sha256"])
                self.assertEqual(result["derived_audio"]["sample_rate"], 16000)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--derived-dir", type=Path)
    options, unittest_arguments = parser.parse_known_args()
    if bool(options.source_root) != bool(options.derived_dir):
        parser.error("--source-root and --derived-dir must be supplied together")
    REAL_SOURCE_ROOT = options.source_root
    REAL_DERIVED_DIR = options.derived_dir
    unittest.main(argv=[__file__] + unittest_arguments)
