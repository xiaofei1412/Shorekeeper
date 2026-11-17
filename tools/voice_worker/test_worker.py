"""Protocol and complete-input budget checks without importing the TTS stack."""
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest


spec = importlib.util.spec_from_file_location("resident_worker", Path(__file__).with_name("run.py"))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def request():
    return {"type": "synthesize", "request_id": str(uuid4()), "text": "保留 USB Type-C 和 1.5 米。",
            "output_name": str(uuid4()) + ".wav"}


def test_request_preserves_new_text():
    value = request()
    assert worker.request_from_line(json.dumps(value)) == value


@pytest.mark.parametrize("change", [
    {"output_name": "../file.wav"}, {"output_name": str(uuid4()) + ".wav.part"},
    {"request_id": "not-uuid"}, {"text": " hello "}, {"text": "字" * 1001},
    {"text": ""}, {"extra": "ignored?"}, {"type": "shutdown"},
])
def test_bad_protocol_is_rejected(change):
    with pytest.raises((ValueError, TypeError, AttributeError)):
        worker.request_from_line(json.dumps(request() | change))


def test_duplicate_key_is_rejected():
    with pytest.raises(ValueError):
        worker.request_from_line('{"type":"a","type":"b"}')


def test_sentence_grouping_keeps_every_character_and_decimal():
    text = "这根 USB Type-C 线长 1.5 米。编号 A07，请收好！还有 example.com? 好。"
    groups = [text[start:end] for start, end in worker.sentence_ranges(text)]
    assert groups == ["这根 USB Type-C 线长 1.5 米。", "编号 A07，请收好！", "还有 example.com?", " 好。"]
    assert "".join(groups) == text


def test_budget_counts_prompt_speech_and_full_generation_reserve():
    class Scalar:
        def __init__(self, value):
            self.value = value

        def item(self):
            return self.value

    value = {"text_len": Scalar(1500), "prompt_text_len": Scalar(100),
             "llm_prompt_speech_token_len": Scalar(1200)}
    lock = {"effective_context_tokens": 32768, "special_embeddings": 2,
            "max_token_text_ratio": 20, "safety_margin_tokens": 32}
    result = worker.budget_for(value, lock)
    assert (result["I"], result["R"], result["S"], result["C"], result["fits"]) == (2802, 30000, 32, 32768, False)
