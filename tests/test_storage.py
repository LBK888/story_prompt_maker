from pathlib import Path

from app.storage import ProjectStore


def test_project_round_trip_and_layout(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "Story")
    loaded = store.load(project["id"])
    assert loaded["name"] == "Test"
    assert loaded["settings"]["story"] == "Story"
    assert (store.project_dir(project["id"]) / "ref_img").is_dir()
    assert not store.project_file(project["id"]).with_suffix(".json.tmp").exists()


def test_load_migrates_legacy_muse_segment_without_resetting_generation_data(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Legacy", "Story")
    project["blueprint"] = {"task_type": "T2VA"}
    project["segments"] = [
        {
            "id": "segment_001",
            "duration_seconds": 8,
            "shots": [{"time_start": 10, "description": "Opening shot", "camera": "wide"}],
            "prompt": "",
            "takes": [{"id": "take_keep"}],
        }
    ]
    store.save(project)

    loaded = store.load(project["id"])
    segment = loaded["segments"][0]

    assert segment["task_type"] == "T2VA"
    assert segment["summary"] == "Opening shot"
    assert segment["shots"][0]["start_seconds"] == 0
    assert segment["takes"] == [{"id": "take_keep"}]


def test_import_and_delete_media_clears_files_and_template_references(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Media", "Story")
    source = tmp_path / "reference.mp4"
    source.write_bytes(b"fake video payload")
    asset = store.import_asset(project["id"], source, source.name)
    assert asset["media_type"] == "video"
    assert asset["mime_type"] == "video/mp4"

    project = store.load(project["id"])
    project["segments"] = [
        {
            "id": "segment_001",
            "index": 1,
            "title": "Segment",
            "duration_seconds": 8,
            "shots": [{"start_seconds": 0, "visual": "Shot", "framing": "wide"}],
            "dialogue": [],
            "prompt": "ready prompt",
            "prompt_status": "ready",
            "template": {
                "tokens": ["REF_IMG1"],
                "first_frame_asset_id": asset["id"],
                "last_frame_asset_id": None,
                "image_order": [asset["id"]],
            },
            "reference_asset_ids": [asset["id"]],
        }
    ]
    store.save(project)
    stored_path = store.project_dir(project["id"]) / "ref_img" / asset["filename"]
    assert stored_path.is_file()

    result = store.delete_asset(project["id"], asset["id"])
    segment = result["project"]["segments"][0]
    assert not stored_path.exists()
    assert result["project"]["assets"] == []
    assert segment["template"]["first_frame_asset_id"] is None
    assert segment["template"]["image_order"] == [None]
    assert segment["reference_asset_ids"] == []
    assert segment["prompt_status"] == "stale"


def test_load_reclassifies_i2va_first_frame_advisory_without_blocking(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.create("I2VA", "Story")
    prompt = """integrated_multimodal_description: [Shot 1] A room.

overall_soundscape: Quiet.

non_diegetic_music: N/A"""
    project["segments"] = [
        {
            "id": "segment_001",
            "index": 1,
            "title": "I2VA segment",
            "task_type": "I2VA",
            "duration_seconds": 8,
            "shots": [{"start_seconds": 0, "visual": "A room", "framing": "wide"}],
            "dialogue": [],
            "prompt": prompt,
            "prompt_status": "invalid",
            "prompt_validation_errors": ["I2VA prompt is missing the official first-frame instruction"],
            "template": {"name": "workflow", "tokens": [], "image_order": []},
        }
    ]
    store.save(project)

    segment = store.load(project["id"])["segments"][0]

    assert segment["prompt_status"] == "ready"
    assert segment["prompt_validation_errors"] == []
    assert segment["prompt_validation_warnings"] == [
        "I2VA prompt is missing the official first-frame instruction"
    ]
