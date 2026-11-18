import json

import pytest


@pytest.fixture
def still_templates(tmp_path, monkeypatch):
    monkeypatch.setattr("app.segment_images.TEMPLATES_DIR", tmp_path)
    nodes = {
        "1": {"class_type": "SaveImageAdvanced", "inputs": {"filename_prefix": "[[OUTPUT_PREFIX]]"}},
        "2": {"class_type": "EmptyLatentImage", "inputs": {"batch_size": 2}},
    }
    (tmp_path / "qimg_2_1_t2i_template.json").write_text(json.dumps(nodes))
    nodes["3"] = {"class_type": "LoadImage", "inputs": {"image": "[[REF_IMG0]]"}}
    (tmp_path / "qimg_2_1_edit_1ref2i_template.json").write_text(json.dumps(nodes))
