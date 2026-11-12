from app.prompt_policy import (
    blueprint_messages,
    blueprint_schema,
    h3_prompt_warnings,
    h3_messages,
    normalize_blueprint,
    normalize_h3_format,
    validate_blueprint_content,
    validate_h3_prompt,
)
from app.readiness import generation_readiness
import json


def segment(task_type="T2VA"):
    return {
        "task_type": task_type,
        "duration_seconds": 8,
        "dialogue": [],
    }


def test_valid_t2va_prompt() -> None:
    prompt = """integrated_multimodal_description: [Shot 1] Live-action close shot. [Shot 2] At 00:03.500, the camera cuts to the doorway.

overall_soundscape: Quiet room tone and footsteps.

non_diegetic_music: N/A"""
    assert validate_h3_prompt(prompt, segment()) == []


def test_rejects_timestamp_after_duration() -> None:
    prompt = """integrated_multimodal_description: [Shot 1] A room. [Shot 2] At 00:09.000, another room.

overall_soundscape: Quiet.

non_diegetic_music: N/A"""
    assert any("outside" in error for error in validate_h3_prompt(prompt, segment()))


def test_dialogue_requires_locked_text_and_close_framing() -> None:
    value = segment()
    value["dialogue"] = [{"kind": "dialogue", "spoken_text": "你来了。"}]
    prompt = """integrated_multimodal_description: [Shot 1] A wide landscape.

overall_soundscape: Wind.

non_diegetic_music: N/A"""
    errors = validate_h3_prompt(prompt, value)
    assert any("Locked spoken text" in error for error in errors)
    assert any("close framing" in error for error in errors)


def test_i2va_missing_official_first_frame_instruction_does_not_block_generation() -> None:
    prompt = """integrated_multimodal_description: [Shot 1] A room.

overall_soundscape: Quiet.

non_diegetic_music: N/A"""
    errors = validate_h3_prompt(prompt, segment("I2VA"))
    item = {
        **segment("I2VA"),
        "title": "I2VA segment",
        "prompt": prompt,
        "prompt_status": "ready" if not errors else "invalid",
        "template": {"name": "workflow", "tokens": [], "image_order": []},
    }
    assert errors == []
    assert h3_prompt_warnings(prompt, item) == ["I2VA prompt is missing the official first-frame instruction"]
    assert generation_readiness([item]) == []


def test_normalize_blueprint_repairs_muse_legacy_segment_shape() -> None:
    blueprint = {
        "task_type": "T2VA",
        "segments": [
            {
                "duration_seconds": 8,
                "continuity_from_previous": False,
                "shots": [
                    {
                        "time_start": 0,
                        "time_end": 4,
                        "description": "A student enters a quiet classroom.",
                        "camera": "medium shot",
                    },
                    {
                        "time_start": 4,
                        "time_end": 8,
                        "description": "She greets the professor.",
                        "camera": "medium close-up",
                        "dialogue": "教授您好。",
                        "speaker": "學生",
                    },
                ],
            }
        ],
    }

    result = normalize_blueprint(blueprint)
    value = result["segments"][0]

    assert value["title"] == "片段 01"
    assert value["summary"]
    assert value["task_type"] == "T2VA"
    assert value["shots"][0]["start_seconds"] == 0
    assert value["shots"][1]["framing"] == "medium close-up"
    assert value["dialogue"][0]["spoken_text"] == "教授您好。"


def test_normalize_blueprint_accepts_observed_muse_description_aliases() -> None:
    blueprint = {
        "segments": [
            {
                "title": "教室",
                "summary": "學生在教室聽課。",
                "duration_seconds": 8,
                "task_type": "T2VA",
                "shots": [
                    {
                        "start_seconds": 0,
                        "duration_seconds": 4,
                        "visual_description": "陽光照進大學教室，教授站在講台前。",
                        "framing": "中景",
                        "camera_instruction": "手持攝影緩慢橫移",
                    },
                    {
                        "start_seconds": 4,
                        "duration_seconds": 4,
                        "visual_description": "學生抬頭看向教授。",
                        "framing": "近景",
                        "camera_instruction": "輕微推近",
                    },
                ],
                "dialogue": [],
            }
        ]
    }

    result = normalize_blueprint(blueprint)
    shots = result["segments"][0]["shots"]

    assert shots[0]["visual"] == "陽光照進大學教室，教授站在講台前。"
    assert shots[0]["camera"] == "手持攝影緩慢橫移"
    assert [shot["start_seconds"] for shot in shots] == [0, 4]
    assert validate_blueprint_content(result) == []


def test_h3_messages_tolerates_existing_segment_without_task_type() -> None:
    project = {
        "settings": {
            "aspect_ratio": "16:9",
            "official_preset": "AUTO",
            "expansion_mode": "balanced",
            "style_category": "live_action",
            "style_notes": "",
        },
        "assets": [],
        "blueprint": {"task_type": "T2VA"},
    }
    legacy = {"duration_seconds": 8, "shots": [], "dialogue": []}

    messages = h3_messages(project, legacy)

    assert "Task is T2VA" in messages[0]["content"]
    assert "Mandatory T2VA storytelling and continuity contract" in messages[0]["content"]
    assert "zero knowledge of any earlier or later clip" in messages[0]["content"]


def test_ref2va_messages_attach_compact_storytelling_supplement() -> None:
    project = {
        "settings": {
            "aspect_ratio": "16:9",
            "official_preset": "AUTO",
            "expansion_mode": "balanced",
            "style_category": "live_action",
            "style_notes": "",
        },
        "assets": [],
        "blueprint": {"task_type": "Ref2VA"},
    }
    value = {"duration_seconds": 8, "shots": [], "dialogue": [], "task_type": "Ref2VA"}

    system = h3_messages(project, value)[0]["content"]

    assert "Mandatory Ref2VA storytelling and continuity contract" in system
    assert "Character/speaker IDs never change with local speaking order" in system
    assert "maintain 100% absolute consistency in facial identity" in system
    assert "do not redescribe facial features" in system
    assert "fully restate in this prompt every principal character's stable facial traits" not in system
    assert "fully restate all visible principal characters, complete wardrobe" not in system
    assert "# 2. Treat Every Video Clip" not in system


def test_blueprint_prompt_requires_detailed_canonical_bibles() -> None:
    project = {
        "settings": {
            "story": "A recurring character crosses a recurring room.",
            "minimum_duration_seconds": 8,
            "aspect_ratio": "16:9",
            "official_preset": "AUTO",
            "expansion_mode": "balanced",
            "style_category": "live_action",
            "style_notes": "",
        },
        "assets": [],
    }

    system = blueprint_messages(project)[0]["content"]

    assert "canonical global character bible" in system
    assert "canonical location bible" in system
    assert "stand alone without access to another clip" in system


def test_rejects_cross_clip_continuity_shorthand() -> None:
    english = """integrated_multimodal_description: [Shot 1] The same room as the previous scene.

overall_soundscape: Quiet.

non_diegetic_music: N/A"""
    chinese = """integrated_multimodal_description: [Shot 1] 與上一段場景相同，角色走入房間。

overall_soundscape: Quiet.

non_diegetic_music: N/A"""

    assert any("continuity shorthand" in error for error in validate_h3_prompt(english, segment()))
    assert any("continuity shorthand" in error for error in validate_h3_prompt(chinese, segment()))


def test_later_segment_prompt_receives_prior_storyboard_and_ready_prompt() -> None:
    previous = {
        "id": "segment_001", "index": 1, "title": "登場", "duration_seconds": 8,
        "summary": "男主角穿黑色西裝進入大廳。",
        "shots": [{"start_seconds": 0, "visual": "黑色西裝男主角", "framing": "中景"}],
        "dialogue": [], "prompt": "A man in the same black suit enters the lobby.", "prompt_status": "ready",
    }
    current = {
        "id": "segment_002", "index": 2, "title": "會面", "duration_seconds": 8,
        "summary": "男主角繼續前進。", "shots": [], "dialogue": [], "task_type": "T2VA",
    }
    project = {
        "settings": {"story": "穿著西裝的男主角。", "aspect_ratio": "16:9", "official_preset": "AUTO", "expansion_mode": "balanced", "style_category": "live_action", "style_notes": ""},
        "assets": [], "blueprint": {"task_type": "T2VA"}, "segments": [previous, current],
    }

    messages = h3_messages(project, current)
    payload = json.loads(messages[1]["content"])

    assert payload["story"] == "穿著西裝的男主角。"
    assert payload["preceding_segments"][0]["summary"] == previous["summary"]
    assert payload["preceding_segments"][0]["prompt"] == previous["prompt"]
    assert "Preserve recurring subject identity" in messages[0]["content"]


def test_current_manual_summary_shots_and_dialogue_are_all_forwarded() -> None:
    current = {
        "id": "segment_001", "index": 1, "duration_seconds": 8, "task_type": "T2VA",
        "summary": "電影感，環境音與快節奏感的配樂",
        "shots": [{"start_seconds": 0, "visual": "手持鏡頭快速跟拍", "framing": "中景"}],
        "dialogue": [{"kind": "dialogue", "spoken_text": "快走！", "language": "Chinese"}],
    }
    project = {
        "settings": {"story": "追逐故事", "aspect_ratio": "16:9", "official_preset": "AUTO", "expansion_mode": "balanced", "style_category": "live_action", "style_notes": ""},
        "assets": [], "blueprint": {"task_type": "T2VA"}, "segments": [current],
    }

    payload = json.loads(h3_messages(project, current)[1]["content"])

    assert payload["segment"]["summary"] == current["summary"]
    assert payload["segment"]["shots"] == current["shots"]
    assert payload["segment"]["dialogue"] == current["dialogue"]


def test_h3_reference_assets_come_only_from_template_mapping() -> None:
    current = {
        "id": "segment_001",
        "index": 1,
        "duration_seconds": 8,
        "task_type": "Ref2VA",
        "summary": "主角 asset_character 進入辦公室。",
        "shots": [{"start_seconds": 0, "visual": "asset_character 走入 asset_unknown 的房間。", "framing": "中景"}],
        "dialogue": [],
        "reference_asset_ids": ["asset_legacy"],
        "template": {"image_order": ["asset_character"], "first_frame_asset_id": None, "last_frame_asset_id": None},
    }
    project = {
        "settings": {"story": "主角進入辦公室。", "aspect_ratio": "16:9", "official_preset": "AUTO", "expansion_mode": "balanced", "style_category": "live_action", "style_notes": ""},
        "assets": [
            {"id": "asset_legacy", "original_name": "old.png", "roles": ["character"]},
            {"id": "asset_character", "original_name": "hero.png", "roles": ["character"]},
        ],
        "blueprint": {"task_type": "Ref2VA"},
        "segments": [current],
    }

    payload = json.loads(h3_messages(project, current)[1]["content"])

    serialized = json.dumps(payload, ensure_ascii=False)
    assert "asset_character" not in serialized
    assert "asset_legacy" not in serialized
    assert payload["reference_assets"][0]["picture"] == "<Picture 1>"
    assert payload["reference_assets"][0]["template_slot"] == "REF_IMG1"
    assert "reference_asset_ids" not in payload["segment"]
    assert "template" not in payload["segment"]
    assert "<Picture 1>" in payload["segment"]["summary"]
    assert "the reference image" in payload["segment"]["shots"][0]["visual"]


def test_ref2va_prompt_rejects_internal_asset_ids_and_requires_picture_definitions() -> None:
    value = segment("Ref2VA")
    value["template"] = {"image_order": ["asset_woman", "asset_office"]}
    prompt = """subject_definitions:
<Subject 1>: fully_preserved from asset_woman, a young woman.

summary: She enters an office.

retention_analysis: Preserve her identity.

detailed_description: [Shot 1] <Subject 1> enters the office.

overall_soundscape: Quiet room tone.

non_diegetic_music: N/A"""

    errors = validate_h3_prompt(prompt, value)

    assert any("internal asset ID" in error for error in errors)
    assert any("<Picture 1>" in error for error in errors)
    assert any("<Picture 2>" in error for error in errors)


def test_ref2va_prompt_accepts_sequential_picture_and_subject_definitions() -> None:
    value = segment("Ref2VA")
    value["template"] = {"tokens": ["REF_IMG1", "REF_IMG2"], "image_order": ["internal_a", "internal_b"]}
    prompt = """subject_definitions:
<Subject 1>: the young woman shown in <Picture 1>, with long brown hair and a black camisole.
<Picture 1>: front portrait reference for <Subject 1>, defining her face, hair, shoulders, and black camisole.
<Picture 2>: modern office reference defining the room layout, desk, and warm window light.

summary: <Subject 1> enters the office shown in <Picture 2>.

retention_analysis: <Subject 1> is fully_preserved from <Picture 1>; the office is fully_preserved from <Picture 2>.

detailed_description: [Shot 1] <Subject 1> enters the office with a handheld medium shot.

overall_soundscape: Quiet room tone and footsteps.

non_diegetic_music: N/A"""

    assert validate_h3_prompt(prompt, value) == []


def test_normalize_h3_format_repairs_missing_colons_and_dialogue_language() -> None:
    prompt = """integrated_multimodal_description
[Shot 1] A student (S1) says <d>教授您好。</d> in a medium close-up.

overall_soundscape
Quiet room tone.

non_diegetic_music
N/A"""

    repaired = normalize_h3_format(prompt)

    assert repaired.startswith("integrated_multimodal_description:")
    assert "overall_soundscape:" in repaired
    assert "non_diegetic_music:" in repaired
    assert "<d>[Chinese] 教授您好。</d>" in repaired
    assert validate_h3_prompt(repaired, {"task_type": "T2VA", "duration_seconds": 8, "dialogue": []}) == []


def test_blueprint_validation_rejects_empty_storyboard_skeleton() -> None:
    blueprint = {
        "segments": [
            {
                "id": "segment_001",
                "title": "片段 01",
                "summary": "片段 01",
                "task_type": "T2VA",
                "duration_seconds": 15,
                "shots": [
                    {"start_seconds": 0, "framing": "medium shot", "visual": "", "camera": "medium shot"},
                    {"start_seconds": 0, "framing": "medium shot", "visual": "", "camera": "medium shot"},
                ],
            }
        ]
    }

    errors = validate_blueprint_content(blueprint)

    assert any("summary" in error for error in errors)
    assert any("visual" in error for error in errors)
    assert any("strictly increasing" in error for error in errors)


def test_blueprint_validation_accepts_contentful_storyboard() -> None:
    blueprint = {
        "segments": [
            {
                "id": "segment_001",
                "title": "教室初遇",
                "summary": "學生在課後走向講台詢問教授問題。",
                "task_type": "T2VA",
                "duration_seconds": 8,
                "shots": [
                    {"start_seconds": 0, "framing": "wide shot", "visual": "學生從座位起身。", "camera": "locked"},
                    {"start_seconds": 3, "framing": "medium shot", "visual": "她走向講台。", "camera": "slow push-in"},
                ],
            }
        ]
    }

    assert validate_blueprint_content(blueprint) == []


def test_blueprint_validation_requires_dialogue_when_user_enabled_it() -> None:
    blueprint = {
        "segments": [
            {
                "id": "segment_001",
                "title": "課後提問",
                "summary": "學生走向教授並提出問題。",
                "task_type": "T2VA",
                "duration_seconds": 8,
                "shots": [
                    {"start_seconds": 0, "framing": "medium shot", "visual": "學生走向講台。", "camera": "slow push-in"}
                ],
                "dialogue": [],
            }
        ]
    }

    errors = validate_blueprint_content(blueprint, require_dialogue=True)

    assert any("dialogue" in error.lower() for error in errors)


def test_blueprint_validation_requires_voiceover_when_user_enabled_it() -> None:
    blueprint = {
        "segments": [
            {
                "id": "segment_001",
                "title": "街景",
                "summary": "清晨街道逐漸甦醒。",
                "task_type": "T2VA",
                "duration_seconds": 8,
                "shots": [
                    {"start_seconds": 0, "framing": "wide shot", "visual": "清晨街景。", "camera": "locked"}
                ],
                "dialogue": [{"kind": "dialogue", "spoken_text": "早安。"}],
            }
        ]
    }

    errors = validate_blueprint_content(blueprint, require_narration=True)

    assert any("voiceover" in error.lower() for error in errors)


def test_blueprint_schema_forbids_or_requires_audio_lines_from_user_choices() -> None:
    def dialogue_contract(schema: dict) -> dict:
        return schema["properties"]["segments"]["items"]["properties"]["dialogue"]

    disabled = dialogue_contract(blueprint_schema())
    assert disabled["maxItems"] == 0

    dialogue = dialogue_contract(blueprint_schema(require_dialogue=True, dialogue_language="Chinese"))
    assert dialogue["minItems"] == 1
    assert dialogue["items"]["properties"]["kind"]["enum"] == ["dialogue"]
    assert dialogue["items"]["properties"]["language"]["enum"] == ["Chinese"]

    narration = dialogue_contract(blueprint_schema(require_narration=True, narration_language="English"))
    assert narration["minItems"] == 1
    assert narration["items"]["properties"]["kind"]["enum"] == ["voiceover"]
    assert narration["items"]["properties"]["language"]["enum"] == ["English"]

    both = dialogue_contract(
        blueprint_schema(
            require_dialogue=True,
            require_narration=True,
            dialogue_language="Chinese",
            narration_language="English",
        )
    )
    assert both["minItems"] == 2
    assert both["items"]["properties"]["kind"]["enum"] == ["dialogue", "voiceover"]
