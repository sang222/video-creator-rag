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
    assert " ".join(line for cue in cues for line in cue["lines"]) == " ".join(
        token_texts
    )


def test_short_final_cue_does_not_merge_past_duration_policy() -> None:
    words = [
        {"index": 1, "text": "one", "start_ms": 0, "end_ms": 5_800},
        {"index": 2, "text": "two", "start_ms": 6_300, "end_ms": 6_900},
    ]

    cues = _build_srt_cues(words)

    assert len(cues) == 2
    assert all(cue["end_ms"] - cue["start_ms"] <= 6_000 for cue in cues)


def test_srt_grouping_repartitions_live_shaped_over_fast_boundary() -> None:
    samples = [
        ("Execution", 115_620, 116_440),
        ("happens", 116_740, 117_240),
        ("through", 117_260, 117_440),
        ("a", 117_500, 117_560),
        ("named", 117_660, 118_040),
        ("tool", 118_200, 118_680),
        ("or", 118_760, 118_900),
        ("API", 119_080, 119_820),
        ("with", 119_880, 120_000),
        ("defined", 120_040, 120_600),
        ("inputs.", 120_720, 121_340),
        ("The", 121_980, 122_070),
        ("model’s", 122_140, 122_500),
        ("language", 122_600, 122_980),
        ("understanding", 123_060, 123_780),
        ("is", 123_820, 123_920),
        ("useful", 124_040, 124_380),
        ("on", 124_420, 124_540),
        ("the", 124_580, 124_660),
        ("first", 124_700, 124_890),
        ("side", 124_960, 125_160),
        ("of", 125_220, 125_260),
        ("the", 125_300, 125_380),
        ("boundary,", 125_440, 125_920),
    ]
    words = [
        {"index": index, "text": text, "start_ms": start, "end_ms": end}
        for index, (text, start, end) in enumerate(samples, start=1)
    ]

    cues = _build_srt_cues(words)

    assert [cue["word_start_index"] for cue in cues] == [1, 11, 13]
    assert [cue["word_end_index"] for cue in cues] == [10, 12, 24]
    assert all(
        len(" ".join(cue["lines"])) / ((cue["end_ms"] - cue["start_ms"]) / 1_000) <= 20
        for cue in cues
    )
    assert all(800 <= cue["end_ms"] - cue["start_ms"] <= 6_000 for cue in cues)
    assert all(len(line) <= 46 for cue in cues for line in cue["lines"])
    assert " ".join(line for cue in cues for line in cue["lines"]) == " ".join(
        text for text, _start, _end in samples
    )
