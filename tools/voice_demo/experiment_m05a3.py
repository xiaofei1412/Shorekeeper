"""Explicit M05A3 experiment inputs; no automatic text repair or candidate selection."""

SHORT_SEEDS = (20261118, 20261119)
SHORT_TEXTS = {"T02": "嗯，我在。", "N01": "好，我听着。", "N02": "嗯，我们走吧。"}
NAMES_ORIGINAL = "黑海岸、索诺拉、泰缇斯，这三个名字，请听清楚。"
NAMES_PUNCTUATED = "黑海岸，索诺拉，泰缇斯。这三个名字，请听清楚。"
MIXED_TEXT = "这根 USB Type-C 线长 1.5 米，编号是 A07。"
REGRESSION_KEYS = (("C", "T01"), ("B", "T03"), ("C", "T04"), ("C", "T07"), ("C", "T08"))


def build_jobs(items):
    texts = {item["id"]: item["text"] for item in items}
    if (len(items) != 8 or set(texts) != {"T%02d" % n for n in range(1, 9)}
            or texts["T02"] != SHORT_TEXTS["T02"] or texts["T05"] != NAMES_ORIGINAL
            or texts["T06"] != MIXED_TEXT):
        raise ValueError("M05A3 requires the unchanged locked baseline text set")
    plans = []

    def add(group, text_id, display, spoken, seed, change, ranges):
        plans.append({"group": group, "text_id": text_id, "category": change,
                      "display_text": display, "spoken_text": spoken, "seed": seed,
                      "change": change, "replacements": [],
                      "origin": "hand_authored_tts_test", "official_dialogue": False,
                      "training_admission": False, "character_holdout": False,
                      "segments": [{"segment_id": "%s_%s_S%02d" % (group, text_id, index + 1),
                                    "segment_index": index, "char_start": start, "char_end": end,
                                    "display_text": display[start:end], "spoken_text": spoken[start:end]}
                                   for index, (start, end) in enumerate(ranges)]})

    for text_id, text in SHORT_TEXTS.items():
        for seed in SHORT_SEEDS:
            add("S%d" % seed, text_id, text, text, seed, "whole_short_seed_diagnostic", [(0, len(text))])
    add("P", "T05", NAMES_ORIGINAL, NAMES_PUNCTUATED, 20261117,
        "explicit_punctuation_only", [(0, len(NAMES_ORIGINAL))])
    boundary = MIXED_TEXT.index("，编号是") + 1
    add("G", "T06", MIXED_TEXT, MIXED_TEXT, 20261117,
        "explicit_semantic_groups", [(0, boundary), (boundary, len(MIXED_TEXT))])
    return plans
