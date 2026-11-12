from app.timing import dimensions_for_ratio, estimate_speech_seconds, partition_duration, resolution_options
from app.domain import new_project


def test_chinese_speech_timing_includes_punctuation_and_breath() -> None:
    assert estimate_speech_seconds("你終於來了。") == 2.13


def test_english_speech_timing() -> None:
    assert estimate_speech_seconds("I finally found you.") == 2.3


def test_partition_avoids_illegal_short_tail() -> None:
    parts = partition_duration(32)
    assert sum(parts) >= 32
    assert sum(parts) <= 36
    assert all(4 <= value <= 15 for value in parts)


def test_dimensions_are_multiples_of_32() -> None:
    width, height = dimensions_for_ratio("9:16")
    assert width % 32 == 0
    assert height % 32 == 0
    assert height > width


def test_resolution_options_match_requested_16_by_9_sizes() -> None:
    expected = {
        0.4: (864, 480),
        0.7: (1152, 640),
        1.0: (1376, 768),
        1.1: (1440, 800),
        2.0: (1920, 1088),
    }
    options = {item["megapixels"]: (item["width"], item["height"]) for item in resolution_options("16:9")}
    assert {key: options[key] for key in expected} == expected


def test_new_projects_default_to_one_megapixel() -> None:
    assert new_project("test")["settings"]["target_megapixels"] == 1.0
