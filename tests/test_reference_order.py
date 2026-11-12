import pytest

from app.reference_order import (
    bound_reference_asset_ids,
    picture_asset_ids,
    picture_slots,
    reference_candidates,
    reference_order_messages,
    reference_order_schema,
    reference_slots,
    validate_reference_order,
)


def assets() -> list[dict]:
    return [
        {
            "id": "asset_black",
            "character_name": "黑西裝男主角",
            "original_name": "man.png",
            "roles": ["character"],
            "notes": "保持黑西裝",
        },
        {
            "id": "asset_office",
            "character_name": "",
            "original_name": "office.png",
            "roles": ["scene"],
            "notes": "夜間辦公室",
        },
    ]


def test_reference_slots_use_template_numeric_order() -> None:
    assert reference_slots(["PROMPT", "REF_IMG10", "REF_IMG2", "REF_IMG1", "REF_IMG2"]) == [
        "REF_IMG1",
        "REF_IMG2",
        "REF_IMG10",
    ]


def test_reference_candidates_always_use_project_assets_for_slot_suggestions() -> None:
    project = {"assets": assets()}
    segment = {"reference_asset_ids": ["asset_office"]}
    assert reference_candidates(project, segment) == assets()
    assert reference_candidates(project, {"reference_asset_ids": []}) == assets()


def test_bound_reference_assets_are_derived_only_from_template_mapping() -> None:
    segment = {
        "reference_asset_ids": ["legacy_hidden_selection"],
        "template": {
            "first_frame_asset_id": "asset_first",
            "image_order": ["asset_character", "asset_first", "asset_scene"],
            "last_frame_asset_id": "asset_last",
        },
    }
    assert bound_reference_asset_ids(segment) == [
        "asset_first",
        "asset_character",
        "asset_scene",
        "asset_last",
    ]
    assert bound_reference_asset_ids({"reference_asset_ids": ["legacy_hidden_selection"]}) == []


def test_picture_numbers_follow_ref_img_input_order_from_one() -> None:
    segment = {
        "template": {
            "first_frame_asset_id": "asset_first",
            "image_order": ["asset_second", "asset_first"],
            "last_frame_asset_id": "asset_last",
        }
    }
    assert picture_asset_ids(segment) == ["asset_second", "asset_first"]


def test_picture_aliases_stay_sequential_when_template_tokens_have_gaps() -> None:
    segment = {
        "template": {
            "tokens": ["PROMPT", "REF_IMG3", "REF_IMG1"],
            "image_order": ["asset_first", "asset_second"],
        }
    }
    assert picture_slots(segment) == ["REF_IMG1", "REF_IMG3"]


def test_llm_contract_contains_visible_names_roles_and_exact_slot_count() -> None:
    project = {"settings": {"story": "A man enters an office."}}
    segment = {"id": "segment_001", "summary": "進入辦公室", "shots": [], "dialogue": []}
    messages = reference_order_messages(project, segment, ["REF_IMG1", "REF_IMG2"], assets())
    user_text = messages[-1]["content"]
    assert "黑西裝男主角" in user_text
    assert "visible_order" in user_text
    assert "asset_office" in user_text
    schema = reference_order_schema(["asset_black", "asset_office"], 2)
    assert schema["properties"]["asset_ids"]["minItems"] == 2
    assert schema["properties"]["asset_ids"]["maxItems"] == 2


def test_llm_order_is_validated_before_reaching_ui() -> None:
    assert validate_reference_order(
        {"asset_ids": ["asset_office", "asset_black"]},
        ["asset_black", "asset_office"],
        2,
    ) == ["asset_office", "asset_black"]
    with pytest.raises(ValueError, match="exactly 2"):
        validate_reference_order({"asset_ids": ["asset_black"]}, ["asset_black", "asset_office"], 2)
    with pytest.raises(ValueError, match="more than once"):
        validate_reference_order(
            {"asset_ids": ["asset_black", "asset_black"]},
            ["asset_black", "asset_office"],
            2,
        )
