from app.services.v2_elevenlabs_narration import _build_srt_cues


def test_srt_grouping_flushes_before_unsplittable_83_character_candidate() -> None:
    token_texts = [f"s1n5token{index}" for index in range(11, 18)]
    assert len(" ".join(token_texts)) == 83

    words = [
        {
            "index": index,
            "text": text,
            "start_ms": (index - 1) * 500,
            "end_ms": index * 500,
        }
        for index, text in enumerate(token_texts, start=1)
    ]

    cues = _build_srt_cues(words)

    assert [cue["word_start_index"] for cue in cues] == [1, 7]
    assert [cue["word_end_index"] for cue in cues] == [6, 7]
    assert all(len(line) <= 46 for cue in cues for line in cue["lines"])
    assert " ".join(
        line for cue in cues for line in cue["lines"]
    ) == " ".join(token_texts)


def test_short_final_cue_does_not_merge_past_duration_policy() -> None:
    words = [
        {"index": 1, "text": "one", "start_ms": 0, "end_ms": 5_800},
        {"index": 2, "text": "two", "start_ms": 6_300, "end_ms": 6_900},
    ]

    cues = _build_srt_cues(words)

    assert len(cues) == 2
    assert all(cue["end_ms"] - cue["start_ms"] <= 6_000 for cue in cues)
