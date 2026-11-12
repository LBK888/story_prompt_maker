from __future__ import annotations

try:
    from opencc import OpenCC
except ImportError:  # Allows the standard-library test suite to run before install.bat.
    OpenCC = None  # type: ignore[assignment]


_converter = OpenCC("t2s") if OpenCC is not None else None


def to_simplified(text: str) -> str:
    if _converter is None:
        return text
    return _converter.convert(text)

