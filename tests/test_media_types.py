from app.media_types import media_mime_type, media_size_limit, media_type


def test_supported_reference_media_types_and_limits() -> None:
    assert media_type("portrait.webp") == "image"
    assert media_type("motion.MP4") == "video"
    assert media_type("voice.flac") == "audio"
    assert media_type("notes.txt") is None
    assert media_mime_type("motion.mp4") == "video/mp4"
    assert media_size_limit("motion.mp4") > media_size_limit("portrait.png")
