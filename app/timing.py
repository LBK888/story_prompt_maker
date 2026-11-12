from __future__ import annotations

import math
import re


SPEED_FACTORS = {"slow": 0.82, "normal": 1.0, "fast": 1.18}
HAN = re.compile(r"[\u3400-\u9fff]")
ENGLISH_WORD = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")
MEGAPIXEL_OPTIONS = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.5, 1.8, 2.0)
CANONICAL_16_9 = {
    0.4: (864, 480), 0.5: (960, 544), 0.6: (1056, 608), 0.7: (1152, 640),
    0.8: (1216, 672), 0.9: (1280, 736), 1.0: (1376, 768), 1.1: (1440, 800), 1.2: (1504, 832),
    1.5: (1664, 928), 1.8: (1824, 1024), 2.0: (1920, 1088),
}


def estimate_speech_seconds(text: str, speed: str = "normal") -> float:
    factor = SPEED_FACTORS.get(speed, 1.0)
    han_count = len(HAN.findall(text))
    english_words = len(ENGLISH_WORD.findall(text))
    base = han_count / (3.5 * factor) + english_words / (2.5 * factor)
    comma_pauses = sum(text.count(mark) for mark in ",，、;；:") * 0.2
    sentence_pauses = sum(text.count(mark) for mark in ".。?!？！") * 0.4
    breath = 0.3 if text.strip() else 0.0
    return round(base + comma_pauses + sentence_pauses + breath, 2)


def partition_duration(minimum_seconds: int, preferred_max_segment: int = 15) -> list[int]:
    if not 4 <= preferred_max_segment <= 25:
        raise ValueError("Segment duration must be 4-25 seconds")
    if minimum_seconds < 4:
        minimum_seconds = 4
    maximum_seconds = max(minimum_seconds, math.ceil(minimum_seconds * 1.10))
    segment_count = max(1, math.ceil(minimum_seconds / preferred_max_segment))
    while segment_count <= 100:
        lower = 4 * segment_count
        upper = 25 * segment_count
        target = max(minimum_seconds, lower)
        if target <= min(maximum_seconds, upper):
            base, remainder = divmod(target, segment_count)
            parts = [base + (1 if index < remainder else 0) for index in range(segment_count)]
            if all(4 <= part <= 25 for part in parts):
                return parts
        segment_count += 1
    raise ValueError("Cannot partition duration into at most 100 segments")


def dimensions_for_ratio(aspect_ratio: str, megapixels: float = 1.0, multiple: int = 32) -> tuple[int, int]:
    from .domain import ASPECT_RATIOS

    if aspect_ratio not in ASPECT_RATIOS:
        raise ValueError(f"Unsupported aspect ratio: {aspect_ratio}")
    selected = next((option for option in MEGAPIXEL_OPTIONS if abs(megapixels - option) < 0.0001), None)
    if selected is not None and aspect_ratio == "16:9":
        return CANONICAL_16_9[selected]
    if selected is not None and aspect_ratio == "9:16":
        width, height = CANONICAL_16_9[selected]
        return height, width
    ratio_width, ratio_height = ASPECT_RATIOS[aspect_ratio]
    area = max(0.1, megapixels) * 1_000_000
    raw_height = math.sqrt(area * ratio_height / ratio_width)
    raw_width = raw_height * ratio_width / ratio_height
    if ratio_width >= ratio_height:
        height = max(multiple, math.ceil(raw_height / multiple) * multiple)
        width = max(multiple, math.ceil((height * ratio_width / ratio_height) / multiple) * multiple)
    else:
        width = max(multiple, math.ceil(raw_width / multiple) * multiple)
        height = max(multiple, math.ceil((width * ratio_height / ratio_width) / multiple) * multiple)
    return width, height


def resolution_options(aspect_ratio: str) -> list[dict[str, float | int]]:
    return [
        {"megapixels": megapixels, "width": width, "height": height}
        for megapixels in MEGAPIXEL_OPTIONS
        for width, height in [dimensions_for_ratio(aspect_ratio, megapixels)]
    ]
