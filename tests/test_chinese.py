from app.chinese import to_simplified


def test_traditional_chinese_is_converted_for_h3_prompt() -> None:
    assert to_simplified("她在車站說：你終於來了。") == "她在车站说：你终于来了。"

