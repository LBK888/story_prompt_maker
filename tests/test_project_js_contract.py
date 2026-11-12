from pathlib import Path


SOURCE = Path("static/project.js").read_text(encoding="utf-8")
TEMPLATE = Path("templates/project.html").read_text(encoding="utf-8")


def test_blueprint_action_saves_current_form_first() -> None:
    handler = SOURCE.rsplit("document.querySelector('#blueprint-button')", 1)[1].split("document.querySelector('#prompts-all')", 1)[0]
    assert "saveSettings" in handler
    assert handler.index("saveSettings") < handler.index("/blueprint")


def test_blank_storyboard_saves_settings_and_is_disabled_after_segments_exist() -> None:
    assert 'id="blank-blueprint-button"' in TEMPLATE
    handler = SOURCE.split("document.querySelector('#blank-blueprint-button').onclick", 1)[1].split("document.querySelector('#prompts-all')", 1)[0]
    assert handler.index("saveSettings") < handler.index("/blueprint/blank")
    assert "blankBlueprint.disabled=project.segments.length>0||!!lastLlamaStatus.busy" in SOURCE
    assert "blankBlueprint.disabled=busy||project.segments.length>0" in SOURCE
    assert "已有分鏡，為避免覆寫資料已停用" in SOURCE


def test_llm_health_is_polled_after_page_load() -> None:
    assert "refreshLlamaStatus" in SOURCE
    assert "scheduleLlamaStatus" in SOURCE


def test_busy_llm_keeps_red_progress_notice_and_disables_llm_actions() -> None:
    assert "updateLlmBusyUi" in SOURCE
    assert "status.progress" in SOURCE
    assert "toastTimer" in SOURCE
    assert "button.disabled=busy" in SOURCE


def test_blank_persistent_status_cannot_leave_an_empty_toast_visible() -> None:
    toast_function = SOURCE.split("function toast", 1)[1].split("async function api", 1)[0]
    assert "const text=String(message??'').trim()" in toast_function
    assert "if(!text)" in toast_function
    assert "el.hidden=true" in toast_function


def test_hidden_toast_cannot_be_forced_visible_by_its_grid_layout() -> None:
    css = Path("static/app.css").read_text(encoding="utf-8")
    assert ".toast[hidden]{display:none!important}" in css


def test_video_buttons_are_gated_by_prompt_template_and_active_takes() -> None:
    assert "segmentCanGenerateVideo" in SOURCE
    assert "projectCanGenerateVideos" in SOURCE
    assert "activeVideoTakeCount" in SOURCE
    assert "generateAll.disabled" in SOURCE
    assert "mergeAll.disabled" in SOURCE


def test_reference_drop_zone_and_dynamic_t2v_blueprint_label_exist() -> None:
    assert "dragover" in SOURCE
    assert "drop" in SOURCE
    assert SOURCE.count("addEventListener(eventName,event=>") == 2
    assert "產生／重建t2v影片藍圖" in SOURCE
    assert "產生／重建影片藍圖" in SOURCE


def test_requested_action_labels_exist() -> None:
    assert "創作/重製所有prompts" in TEMPLATE
    assert "依序生成所有影片" in TEMPLATE
    assert "生成/重製本段影片" in SOURCE


def test_connection_dialog_can_interrupt_and_unload_llm() -> None:
    assert 'id="unload-llm"' in TEMPLATE
    assert "中斷LLM釋放vram" in TEMPLATE
    assert "'/api/llama/unload'" in SOURCE
    assert "lastLlamaStatus.busy" in SOURCE


def test_connection_dialog_identifies_the_active_provider_model_and_endpoint() -> None:
    for element_id in (
        "active-llm-provider", "active-llm-model", "active-llm-endpoint", "active-llm-key"
    ):
        assert f'id="{element_id}"' in TEMPLATE
    assert "renderActiveLlm" in SOURCE
    assert "active.provider_label" in SOURCE
    assert "active.api_key_set" in SOURCE
    assert "EXTERNAL API" in SOURCE
    assert "外部 API，不會載入本機模型" in SOURCE


def test_megapixel_selector_tracks_aspect_ratio() -> None:
    assert 'id="target-megapixels"' in TEMPLATE
    assert "RESOLUTION_OPTIONS" in TEMPLATE
    assert "renderResolutionOptions" in SOURCE
    assert "target_megapixels" in SOURCE


def test_prompt_action_saves_storyboard_edits_first() -> None:
    handler = SOURCE.split("el.querySelector('.prompt-one').onclick", 1)[1].split("el.querySelector('.save-prompt').onclick", 1)[0]
    assert "saveStoryboard" in handler
    assert handler.index("saveStoryboard") < handler.index("/prompt")


def test_generation_queue_is_visible_cancelable_and_does_not_disable_video_buttons() -> None:
    assert 'id="generation-queue"' in TEMPLATE
    assert "refreshGenerationQueue" in SOURCE
    assert "queue-cancel" in SOURCE
    assert "method:'DELETE'" in SOURCE
    assert "result.jobs.length" in SOURCE
    assert "generateAll.disabled=!projectCanGenerateVideos()" in SOURCE
    assert "button.disabled=!segmentCanGenerateVideo(segment)" in SOURCE
    assert "activeGenerationJobs" not in SOURCE


def test_image_dialog_batch_select_submits_multiple_workflows_without_closing_results() -> None:
    assert 'id="segment-image-batch"' in TEMPLATE
    assert '<option value="1" selected>' in TEMPLATE
    assert '<option value="4">4</option>' in TEMPLATE
    assert "prompt:promptInput.value,batch:count" in SOURCE
    assert "candidates.push(...(result.candidates||[]))" in SOURCE


def test_reference_order_uses_visible_slot_selectors_instead_of_raw_asset_ids() -> None:
    assert "renderReferenceOrder" in SOURCE
    assert "ref-order-select" in SOURCE
    assert "assetDisplayName" in SOURCE
    assert "character_name" in SOURCE
    assert "REF參考媒體插槽" in SOURCE
    assert 'class="image-order"' not in SOURCE


def test_reference_order_supports_manual_and_llm_suggestions_before_save() -> None:
    assert "依檔案順序填入" in SOURCE
    assert "LLM 建議順序" in SOURCE
    assert "/reference-order/suggest" in SOURCE
    assert "儲存媒體順序" in SOURCE
    assert SOURCE.index("result.image_order") < SOURCE.index("LLM 建議已填入，尚未儲存")


def test_segment_reference_images_have_one_canonical_slot_interface() -> None:
    assert "本段唯一的參考媒體設定" in SOURCE
    assert "segment-assets" not in SOURCE
    assert "本段參考圖（勾選後再調整 template 插槽）" not in SOURCE


def test_reference_media_upload_delete_and_previews_are_supported() -> None:
    assert "參考影音圖檔" in TEMPLATE
    for suffix in (".mp4", ".mov", ".wav", ".mp3", ".flac"):
        assert suffix in TEMPLATE
    assert "assetMediaType" in SOURCE
    assert "<video class=\"asset-media\"" in SOURCE
    assert "<audio class=\"asset-audio\"" in SOURCE
    assert "delete-asset" in SOURCE
    assert "method:'DELETE'" in SOURCE


def test_generated_video_previews_are_limited_to_500_pixels() -> None:
    css = Path("static/app.css").read_text(encoding="utf-8")
    assert ".take video,.final-card video{width:auto;max-width:500px;max-height:500px" in css


def test_generated_videos_can_be_detached_without_deleting_files() -> None:
    assert "remove-output" in SOURCE
    assert "/outputs/${button.dataset.output}" in SOURCE
    assert "硬碟上的檔案會保留" in SOURCE


def test_comfy_errors_are_limited_to_the_last_two_lines() -> None:
    assert "lastErrorLines" in SOURCE
    assert ".slice(-2).join('\\n')" in SOURCE
    assert "lastErrorLines(take.error)" in SOURCE
    assert "lastErrorLines(data.message)" in SOURCE


def test_segment_content_stays_bounded_and_take_cards_wrap() -> None:
    css = Path("static/app.css").read_text(encoding="utf-8")
    assert ".segment-main{box-sizing:border-box;min-width:0;max-width:100%;overflow:hidden}" in css
    assert "min-width:min(600px,100%)" in css
    assert "grid-template-columns:repeat(auto-fill,minmax(280px,500px))" in css


def test_segments_can_add_and_irreversibly_delete_records() -> None:
    assert "add-blank-segment" in SOURCE
    assert "delete-segment" in SOURCE
    assert "after_segment_id:id" in SOURCE
    assert "無法復原" in SOURCE
    assert "硬碟上的生成檔案不會刪除" in SOURCE


def test_comfy_error_record_is_bounded_and_deletable() -> None:
    css = Path("static/app.css").read_text(encoding="utf-8")
    assert "max-width:500px;max-height:500px;overflow:auto" in css
    assert "overflow-wrap:anywhere" in css
    assert "remove-error" in SOURCE
    assert "/takes/${button.dataset.take}/error" in SOURCE
    assert "刪除錯誤紀錄" in SOURCE


def test_template_and_h3_task_share_one_apply_action() -> None:
    assert "Workflow template<select class=\"template-select\"" in SOURCE
    assert "H3 任務<select class=\"edit-task\"" in SOURCE
    handler = SOURCE.split("el.querySelector('.assign-template').onclick", 1)[1].split("const collectReferenceOrder", 1)[0]
    assert "{name,task_type}" in handler
    editor = SOURCE.split('<details class="storyboard-editor"', 1)[1].split('</details>', 1)[0]
    assert "H3 任務" not in editor


def test_prompt_advisories_are_shown_as_non_blocking_warnings() -> None:
    assert "prompt_validation_warnings" in SOURCE
    assert "提示（不阻擋生成）" in SOURCE


def test_blank_manual_prompt_is_treated_as_ready_for_generation() -> None:
    assert "segmentHasReadyPrompt" in SOURCE
    assert "segment.prompt_source==='manual'" in SOURCE
