const app = document.querySelector('#project-app');
const projectId = app.dataset.projectId;
let project = window.INITIAL_PROJECT;
let templateData = {valid:[], invalid:{}};
const resolutionData = window.RESOLUTION_OPTIONS||{};
let lastLlamaStatus = {busy:false};
let llmCompletionSuffix = '';
let llmCompletionUntil = 0;
let llamaStatusTimer = null;
let generationQueueJobs = [];
let generationQueueTimer = null;
const generationQueueMessages = new Map();
const roleLabels = {first_frame:'起始幀',last_frame:'結束幀',character:'人物',scene:'場景',object:'道具',style:'風格',composition:'構圖',action:'動作'};
const esc = value => String(value??'').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const lastErrorLines = value => String(value??'').split(/\r?\n/).map(line=>line.trim()).filter(Boolean).slice(-2).join('\n');

function toast(message, bad=false, persistent=false) {
  const el=document.querySelector('#toast');
  const text=String(message??'').trim();
  const shownText=!bad&&llmCompletionSuffix&&Date.now()<llmCompletionUntil&&!text.includes('tk/sec')?`${text} · ${llmCompletionSuffix}`:text;
  clearTimeout(window.toastTimer);
  if(!text){el.textContent='';el.hidden=true;return}
  el.textContent=shownText;el.hidden=false;
  el.style.background = bad ? '#a5362b' : '#171a1f';
  if(!persistent) window.toastTimer=setTimeout(()=>el.hidden=true,4200);
}
async function api(path, options={}) {
  const response = await fetch(path, options); let body;
  try { body = await response.json(); } catch { body = {detail:await response.text()}; }
  if (!response.ok) throw new Error(body.detail || `${response.status} ${response.statusText}`);
  return body;
}
const jsonOptions = (method, body) => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
async function refresh(preservePromptEdits=false) {
  const drafts=preservePromptEdits?[...document.querySelectorAll('.segment')].map(el=>{
    const input=el.querySelector('.prompt-box');
    const saved=project.segments.find(segment=>segment.id===el.dataset.segmentId)?.prompt||'';
    return input&&input.value!==saved?{id:el.dataset.segmentId,value:input.value,focused:document.activeElement===input,start:input.selectionStart,end:input.selectionEnd}:null;
  }).filter(Boolean):[];
  project = await api(`/api/projects/${projectId}`);
  render();
  for(const draft of drafts){
    const input=[...document.querySelectorAll('.segment')].find(el=>el.dataset.segmentId===draft.id)?.querySelector('.prompt-box');
    if(!input)continue;
    input.value=draft.value;
    input.dispatchEvent(new Event('input',{bubbles:true}));
    if(draft.focused){input.focus();input.setSelectionRange(draft.start,draft.end)}
  }
}

function queueSegmentLabel(job) {
  const segment=project.segments.find(item=>item.id===job.segment_id);
  return segment?`片段 ${String(segment.index).padStart(2,'0')} · ${segment.title||segment.id}`:job.segment_id;
}
function renderGenerationQueue() {
  const panel=document.querySelector('#generation-queue');
  const list=document.querySelector('#generation-queue-list');
  panel.hidden=!generationQueueJobs.length;
  document.body.classList.toggle('has-generation-queue',generationQueueJobs.length>0);
  document.querySelector('#generation-queue-count').textContent=String(generationQueueJobs.length);
  list.innerHTML=generationQueueJobs.map(job=>{
    const running=job.status==='running'||(job.status==='submitted'&&job.remote_state==='running');
    const remoteDetail={queued:`ComfyUI 排隊順位 ${job.remote_queue_position||'—'}`,running:'ComfyUI 執行中',missing:'遠端佇列與歷史均未找到；請檢查 ComfyUI',unknown:'等待 ComfyUI 狀態更新'}[job.remote_state];
    const detail=job.status==='submitted'?`${remoteDetail||'等待 ComfyUI 狀態更新'} · ID ${job.prompt_id||''}`:job.status==='connection_error'?`ComfyUI 連線異常 · ${job.error||''}`:generationQueueMessages.get(job.segment_id)||(running?'正在準備或等待 ComfyUI 完成':`等待順位 ${job.queue_position}`);
    const stateLabel=running?'處理中':job.status==='connection_error'?'連線異常':job.status==='queued'?'本地排隊中':job.remote_state==='missing'?'遠端工作已移除':job.remote_state==='queued'?'遠端排隊中':'等待遠端狀態';
    return `<article class="generation-queue-item ${running?'running':'queued'}" data-job-id="${esc(job.id)}"><div><b>${esc(queueSegmentLabel(job))}</b><small>${esc(detail)}</small></div><span class="queue-state">${stateLabel}</span>${running?'':`<button class="queue-cancel" type="button" title="取消並移除佇列工作" aria-label="取消並移除 ${esc(queueSegmentLabel(job))}">×</button>`}</article>`;
  }).join('');
  for(const button of list.querySelectorAll('.queue-cancel'))button.onclick=async()=>{
    button.disabled=true;
    try{const result=await api(`/api/jobs/${button.closest('.generation-queue-item').dataset.jobId}`,{method:'DELETE'});await refreshGenerationQueue();toast(result.remote_cancellation_unverified?'已從本地佇列移除；無法連線確認遠端是否取消':result.status==='canceled'?'已取消並移除佇列工作':'工作已完成，佇列已更新',!!result.remote_cancellation_unverified)}
    catch(error){toast(lastErrorLines(error.message),true);await refreshGenerationQueue()}
  };
}
function scheduleGenerationQueue(delay=1500) {
  clearTimeout(generationQueueTimer);
  generationQueueTimer=setTimeout(refreshGenerationQueue,delay);
}
async function refreshGenerationQueue(refreshProjectOnCompletion=true) {
  try{
    const previousJobs=generationQueueJobs;
    const result=await api(`/api/projects/${projectId}/jobs?active_only=true`);
    generationQueueJobs=result.jobs||[];
    renderGenerationQueue();
    renderActionAvailability();
    if(refreshProjectOnCompletion&&previousJobs.some(job=>!generationQueueJobs.some(current=>current.id===job.id)))await refresh(true);
  }catch(error){toast(lastErrorLines(error.message),true)}
  finally{scheduleGenerationQueue(generationQueueJobs.length?1500:5000)}
}

function collectSettings() {
  return {story:document.querySelector('#story').value,minimum_duration_seconds:Number(document.querySelector('#minimum-duration').value),segment_duration_seconds:Number(document.querySelector('#segment-duration').value),aspect_ratio:document.querySelector('#aspect-ratio').value,target_megapixels:Number(document.querySelector('#target-megapixels').value),style_category:document.querySelector('#style-category').value,style_notes:document.querySelector('#style-notes').value,expansion_mode:document.querySelector('#expansion-mode').value,official_preset:document.querySelector('#official-preset').value,create_dialogue:document.querySelector('#create-dialogue').checked,dialogue_language:document.querySelector('#dialogue-language').value,create_narration:document.querySelector('#create-narration').checked,narration_language:document.querySelector('#narration-language').value};
}
async function saveSettings(renderAfter=true) {
  project=await api(`/api/projects/${projectId}/settings`,jsonOptions('PATCH',collectSettings()));
  if(renderAfter) render();
  return project;
}

function updateLlmBusyUi(status) {
  const busy=!!status.busy;
  const wasBusy=!!lastLlamaStatus.busy;
  lastLlamaStatus=status;
  for(const button of document.querySelectorAll('#blueprint-button,#prompts-all,.prompt-one,.optimize-prompt,.suggest-ref-order')) button.disabled=busy;
  const blankBlueprint=document.querySelector('#blank-blueprint-button');
  if(blankBlueprint) {
    blankBlueprint.disabled=busy||project.segments.length>0;
    blankBlueprint.title=project.segments.length?'已有分鏡，為避免覆寫資料已停用':busy?'LLM 正在建立藍圖，請等待完成':'';
  }
  for(const button of document.querySelectorAll('.add-blank-segment,.delete-segment,.remove-error')) button.disabled=busy;
  for(const el of document.querySelectorAll('.segment'))updateStillButtons(el);
  if(busy) {
    const progress=Math.max(0,Math.min(99,Number(status.progress)||0));
    const elapsed=Number(status.elapsed_seconds||0).toFixed(0);
    const operation=esc(status.operation||'LLM 執行中');
    const stage=esc(status.stage||'處理中');
    const speed=Number(status.current_tokens_per_second||0);
    const speedText=speed>0?` · 約 ${speed.toFixed(1)} tk/sec`:' · 等待輸出 token';
    const el=document.querySelector('#toast');
    clearTimeout(window.toastTimer);
    el.hidden=false;
    el.style.background='#a5362b';
    el.innerHTML=`<strong>LLM 執行中 · ${progress}%</strong><span>${operation}</span><small>${stage} · ${elapsed}s${speedText}</small><div class="llm-progress"><i style="width:${progress}%"></i></div>`;
  } else if(wasBusy) {
    const average=Number(status.average_tokens_per_second||0);
    llmCompletionSuffix=average>0?`平均${status.speed_is_estimated?'約 ': ' '}${average.toFixed(1)} tk/sec`:'平均速度無法取得';
    llmCompletionUntil=Date.now()+6500;
    toast(`LLM 工作已完成 · 100% · ${llmCompletionSuffix}`);
  }
}

function scheduleLlamaStatus(delay=5000) {
  clearTimeout(llamaStatusTimer);
  llamaStatusTimer=setTimeout(refreshLlamaStatus,delay);
}

async function refreshLlamaStatus() {
  const pill=document.querySelector('#llm-pill');
  try {
    const status=await api('/api/llama/status');
    const provider=status.provider_label||'LLM';
    const model=status.model||'尚未選擇模型';
    const compactModel=model.length>32?`${model.slice(0,29)}…`:model;
    pill.textContent=status.busy?`${provider} 執行中 ${status.progress}%`:status.healthy?`${provider} · ${compactModel}`:status.preflight_errors.length?`${provider} 設定未就緒`:`${provider} 待連線`;
    pill.title=`${provider}\n${model}\n${status.base_url||''}`;
    pill.className='pill'+(status.healthy?' ok':status.preflight_errors.length?' bad':'');
    updateLlmBusyUi(status);
    scheduleLlamaStatus(status.busy?800:5000);
  } catch (error) {
    pill.textContent='LLM 狀態錯誤';
    pill.className='pill bad';
    scheduleLlamaStatus(5000);
  }
}

function beginLocalLlmOperation(operation) {
  llmCompletionSuffix='';
  updateLlmBusyUi({busy:true,operation,stage:'送出請求',progress:1,elapsed_seconds:0});
  scheduleLlamaStatus(400);
}

function renderResolutionOptions(aspectRatio,selectedMegapixels) {
  const select=document.querySelector('#target-megapixels');
  const options=resolutionData[aspectRatio]||[];
  select.innerHTML=options.map(option=>`<option value="${option.megapixels}">${Number(option.megapixels).toFixed(1)} MP · ${option.width} × ${option.height}</option>`).join('');
  const selected=Number(selectedMegapixels??1.0);
  select.value=String(options.some(option=>Number(option.megapixels)===selected)?selected:1.0);
}
function fillSettings() {
  const s=project.settings;
  document.querySelector('#story').value=s.story||'';
  document.querySelector('#minimum-duration').value=s.minimum_duration_seconds;
  document.querySelector('#segment-duration').value=s.segment_duration_seconds??15;
  document.querySelector('#aspect-ratio').value=s.aspect_ratio;
  renderResolutionOptions(s.aspect_ratio,s.target_megapixels??1.0);
  document.querySelector('#style-category').value=s.style_category;
  document.querySelector('#style-notes').value=s.style_notes||'';
  document.querySelector('#expansion-mode').value=s.expansion_mode;
  document.querySelector('#official-preset').value=s.official_preset||'AUTO';
  document.querySelector('#create-dialogue').checked=!!s.create_dialogue;
  document.querySelector('#dialogue-language').value=s.dialogue_language||'Chinese';
  document.querySelector('#create-narration').checked=!!s.create_narration;
  document.querySelector('#narration-language').value=s.narration_language||'Chinese';
}
function fileUrl(path) { return `/api/projects/${projectId}/files/${path.split('/').map(encodeURIComponent).join('/')}`; }
function assetMediaType(asset) {
  if(['image','video','audio','character'].includes(asset.media_type))return asset.media_type;
  const suffix=String(asset.filename||asset.original_name||'').split('.').pop().toLowerCase();
  if(['mp4','mov','mkv','webm','avi'].includes(suffix))return 'video';
  if(['wav','mp3','flac','m4a','aac','ogg'].includes(suffix))return 'audio';
  return 'image';
}
function assetPreview(asset) {
  const url=fileUrl('ref_img/'+asset.filename);
  const kind=assetMediaType(asset);
  const thumb=kind==='character'?`<img class="asset-media" src="${fileUrl('ref_img/'+(asset.headshot_filename||asset.filename))}" alt="">`:
    kind==='video'?`<video class="asset-media" muted preload="metadata" src="${url}"></video>`:
    kind==='audio'?'<span class="asset-media audio-media" aria-hidden="true">♪</span>':`<img class="asset-media" src="${url}" alt="">`;
  return `<button class="asset-preview-trigger" type="button" title="開啟 ${esc(asset.original_name)} 完整預覽" aria-label="預覽 ${esc(asset.original_name)}">${thumb}</button>`;
}
function openAssetPreview(asset) {
  const dialog=document.querySelector('#asset-preview-dialog');
  const content=document.querySelector('#asset-preview-content');
  const kind=assetMediaType(asset);
  document.querySelector('#asset-preview-title').textContent=asset.original_name;
  const path=name=>fileUrl('ref_img/'+name);
  if(kind==='character'){
    content.innerHTML=`<div class="asset-preview-images">${asset.headshot_filename?`<img src="${path(asset.headshot_filename)}" alt="${esc(asset.character_name||asset.original_name)}的大頭照">`:''}<img src="${path(asset.filename)}" alt="${esc(asset.character_name||asset.original_name)}三視圖"></div>${asset.audio_filename?`<audio controls preload="metadata" src="${path(asset.audio_filename)}"></audio>`:''}${asset.voice_line?`<p><b>台詞：</b>${esc(asset.voice_line)}</p>`:''}${asset.notes?`<pre class="asset-preview-info">${esc(asset.notes)}</pre>`:''}`;
  }else if(kind==='video')content.innerHTML=`<video controls preload="metadata" src="${path(asset.filename)}"></video>`;
  else if(kind==='audio')content.innerHTML=`<audio controls preload="metadata" src="${path(asset.filename)}"></audio><p>${esc(asset.notes||'')}</p>`;
  else content.innerHTML=`<img src="${path(asset.filename)}" alt="${esc(asset.original_name)}">${asset.notes?`<p>${esc(asset.notes)}</p>`:''}`;
  dialog.showModal();
  dialog.onclose=()=>content.querySelectorAll('audio,video').forEach(media=>media.pause());
}
function referencePreview(asset) {
  if(!asset)return '';
  const kind=assetMediaType(asset);
  if(kind==='image'||kind==='character')return `<img class="ref-slot-thumb" src="${fileUrl('ref_img/'+asset.filename)}" alt="">`;
  return `<span class="ref-slot-media ${kind}">${kind==='video'?'影片':'音訊'}</span>`;
}
function renderAssets() {
  const list=document.querySelector('#asset-list');
  list.innerHTML=project.assets.length?'':'<p class="muted">尚未加入參考影音圖檔。</p>';
  for (const asset of project.assets) {
    const card=document.createElement('article'); card.className='asset-card';
    const roles=Object.entries(roleLabels).map(([value,label])=>`<label><input type="checkbox" value="${value}" ${(asset.roles||[]).includes(value)?'checked':''}>${label}</label>`).join('');
    const audioPlayer=assetMediaType(asset)==='audio'?`<audio class="asset-audio" controls preload="metadata" src="${fileUrl('ref_img/'+asset.filename)}"></audio>`:'';
    card.innerHTML=`${assetPreview(asset)}<div><div class="asset-card-heading"><h4>${esc(asset.original_name)}</h4><button class="danger small delete-asset" type="button">刪除</button></div>${audioPlayer}<div class="role-grid">${roles}</div><input class="mini-input character" placeholder="人物名稱（人物參考適用）" value="${esc(asset.character_name||'')}"><input class="mini-input notes" placeholder="說明參考內容與限制…" value="${esc(asset.notes||'')}"><input class="mini-input bindings" placeholder="適用片段 ID，以逗號分隔；留空＝全片" value="${esc((asset.bindings||[]).join(','))}"></div>`;
    let timer, dirty=false, pending=Promise.resolve();
    card.flushSave=async()=>{
      clearTimeout(timer);
      await pending;
      if(!dirty)return;
      const payload={roles:[...card.querySelectorAll('.role-grid input:checked')].map(i=>i.value),character_name:card.querySelector('.character').value,notes:card.querySelector('.notes').value,bindings:card.querySelector('.bindings').value.split(',').map(v=>v.trim()).filter(Boolean)};
      dirty=false;
      pending=api(`/api/projects/${projectId}/assets/${asset.id}`,jsonOptions('PATCH',payload));
      try { await pending; toast('參考圖設定已儲存'); }
      catch(error){dirty=true;throw error;}
      finally{pending=Promise.resolve();}
    };
    card.addEventListener('input',()=>{dirty=true;clearTimeout(timer);timer=setTimeout(()=>card.flushSave().catch(error=>toast(error.message,true)),500)});
    card.querySelector('.asset-preview-trigger').onclick=()=>openAssetPreview(asset);
    card.querySelector('.delete-asset').onclick=async()=>{
      if(!window.confirm(`確定永久刪除參考檔案「${asset.original_name}」？相關片段的 template 映射會一併清除。`))return;
      try { const result=await api(`/api/projects/${projectId}/assets/${asset.id}`,{method:'DELETE'});project=result.project;render();toast(`已刪除 ${asset.original_name}`); }
      catch(error){toast(error.message,true)}
    };
    list.append(card);
  }
  document.querySelector('#blueprint-button').textContent=project.assets.length?'產生／重建影片藍圖':'產生／重建t2v影片藍圖';
}

function segmentHasReadyPrompt(segment) {
  return segment.prompt_status==='ready' && (segment.prompt_source==='manual' || !!String(segment.prompt||'').trim());
}
function segmentCanGenerateVideo(segment) {
  return segmentHasReadyPrompt(segment) && !!segment.template && templateMappingReady(segment.template);
}
function templateMappingReady(binding) {
  if(!binding)return false;
  const result=mapReferenceMedia(binding,project.assets);
  return !result.errors.length && !result.missing.length;
}
function projectCanGenerateVideos() {
  return project.segments.length>0 && project.segments.every(segmentCanGenerateVideo);
}
function activeVideoTakeCount() {
  return project.segments.filter(segment=>(segment.takes||[]).some(take=>take.id===segment.active_take_id&&take.status==='completed'&&take.local_path)).length;
}
function segmentGenerationReason(segment) {
  const missing=[];
  if(!segmentHasReadyPrompt(segment))missing.push('先創作有效的 prompt');
  if(!segment.template)missing.push('套用 workflow template JSON');
  else if(!templateMappingReady(segment.template))missing.push('完整設定 REF 參考媒體插槽');
  return missing.join('，');
}
function renderActionAvailability() {
  const generationBusy=generationQueueJobs.length>0;
  const blankBlueprint=document.querySelector('#blank-blueprint-button');
  blankBlueprint.disabled=project.segments.length>0||!!lastLlamaStatus.busy;
  blankBlueprint.title=project.segments.length?'已有分鏡，為避免覆寫資料已停用':lastLlamaStatus.busy?'LLM 正在建立藍圖，請等待完成':'';
  const promptsAll=document.querySelector('#prompts-all');
  promptsAll.disabled=!project.segments.length||!!lastLlamaStatus.busy;
  promptsAll.title=project.segments.length?'':'請先產生影片藍圖';
  const generateAll=document.querySelector('#generate-all');
  generateAll.disabled=!projectCanGenerateVideos();
  generateAll.title=generateAll.disabled?'每個片段都必須有 ready prompt 並套用 workflow template JSON':'';
  const mergeAll=document.querySelector('#merge-all');
  mergeAll.disabled=generationBusy||activeVideoTakeCount()<2;
  mergeAll.title=generationBusy?'影片工作完成後才能合併':mergeAll.disabled?'至少需要兩段有效的 active take 影片':'';
  const missingPromptCount=project.segments.filter(segment=>!segmentHasReadyPrompt(segment)).length;
  const missingTemplateCount=project.segments.filter(segment=>!segment.template).length;
  const hints=[];
  if(missingPromptCount)hints.push(`${missingPromptCount} 段尚缺 ready prompt`);
  if(missingTemplateCount)hints.push(`${missingTemplateCount} 段尚缺 workflow template JSON`);
  if(activeVideoTakeCount()<2)hints.push(`目前有 ${activeVideoTakeCount()} 段有效 active take，合併至少需要 2 段`);
  document.querySelector('#video-actions-hint').textContent=hints.join(' · ');
  for(const el of document.querySelectorAll('.segment')) {
    const segment=project.segments.find(item=>item.id===el.dataset.segmentId);
    const button=el.querySelector('.generate-one');
    button.disabled=!segmentCanGenerateVideo(segment);
    button.title=button.disabled?segmentGenerationReason(segment):'';
    el.querySelector('.video-readiness').textContent=button.disabled?`影片生成尚缺：${segmentGenerationReason(segment)}`:'已可送交 ComfyUI 生成影片';
    const structureBusy=generationBusy||!!lastLlamaStatus.busy;
    for(const structureButton of el.querySelectorAll('.add-blank-segment,.delete-segment,.remove-error')) {
      structureButton.disabled=structureBusy;
      structureButton.title=structureBusy?'生成工作進行中，暫時不能修改片段結構':'';
    }
  }
}
function assetDisplayName(asset,index) {
  const identity=String(asset.character_name||'').trim()||asset.original_name;
  const filename=identity===asset.original_name?'':` · ${asset.original_name}`;
  const roles=(asset.roles||[]).map(role=>roleLabels[role]||role).join('/');
  const kindLabel={image:'圖片',video:'影片',audio:'音訊',character:'內建角色'}[assetMediaType(asset)];
  return `#${index+1} · ${kindLabel} · ${identity}${filename}${roles?` · ${roles}`:''}`;
}
function assetOptions(selected, allowEmpty=true) {
  return `${allowEmpty?'<option value="">— 未指定 —</option>':''}`+project.assets.map((asset,index)=>`<option value="${esc(asset.id)}" ${asset.id===selected?'selected':''}>${esc(assetDisplayName(asset,index))}</option>`).join('');
}
function referenceTokens(binding) {return mediaReferenceTokens(binding);}
function renderReferenceOrder(binding) {
  const tokens=referenceTokens(binding);
  if(!binding.name)return '<div class="reference-order empty-reference-order">套用 template 後顯示 REF 參考媒體插槽。</div>';
  if(!tokens.length)return '<div class="reference-order empty-reference-order">此 template 沒有 REF 參考媒體插槽。</div>';
  const order=binding.image_order||[];
  const resolved=mapReferenceMedia(binding,project.assets);
  const slots=Array.from({length:Math.max(tokens.length,order.length)},(_,index)=>{
    const selected=order[index]||'';
    const asset=project.assets.find(item=>item.id===selected);
    const labels=Object.entries(resolved.mapping).filter(([token,id])=>id===selected).map(([token])=>`[[${token}]]`);
    return `<div class="ref-slot"><span class="ref-slot-number">${index+1}</span><div class="ref-slot-preview">${referencePreview(asset)}</div><label><b class="ref-token-label">${esc(labels.join(' + ')||'選取參考媒體（可留空）')}</b><select class="ref-order-select">${assetOptions(selected)}</select></label></div>`;
  }).join('');
  return `<section class="reference-order"><div class="reference-order-head"><div><b>REF參考媒體插槽</b><small>本段唯一的參考媒體設定，依媒體類型自動對應圖片、語音及影片；角色會接入三視圖＋語音。Drive_AUD 使用本段第一個語音或角色素材。多餘選單可留空。</small></div><span>${tokens.length} 個關鍵字</span></div>${slots}<p class="hint ref-mapping-status">${esc(resolved.errors.join('；')|| (resolved.missing.length?'尚缺：'+resolved.missing.map(t=>`[[${t}]]`).join('、'):'所有插槽已對應'))}</p><div class="button-row ref-order-actions"><button class="ghost small auto-ref-order">依檔案順序填入</button><button class="ghost small suggest-ref-order">LLM 建議順序</button><button class="primary small save-ref-order">儲存媒體順序</button></div><p class="hint ref-order-rationale">選取後請儲存媒體順序。</p></section>`;
}
function templateOptions(selected) {
  const available=templateData.valid.filter(t=>!/(?:ref2i|t2i)/i.test(t.name));
  const snapshotOnly=selected&&!available.some(t=>t.name===selected)?`<option value="${esc(selected)}" selected disabled>${esc(selected)}（來源檔未提供）</option>`:'';
  return '<option value="">— 選擇 template —</option>'+snapshotOnly+available.map(t=>`<option value="${esc(t.name)}" ${t.name===selected?'selected':''}>${esc(t.name)}</option>`).join('');
}
function showTemplateNote(el, binding={}) {
  const name=el.querySelector('.template-select').value;
  const listed=templateData.valid.find(item=>item.name===name);
  const note=listed?.note??(binding.name===name?binding.note:'');
  const panel=el.querySelector('.template-note');
  panel.textContent=note ? `Template note\n${note}` : '';
  panel.hidden=!note;
}
function renderTakes(segment) {
  return (segment.takes||[]).map(take=>{
    const videos=(take.outputs||[]).map((path,index)=>`<div class="take-output"><video class="take-video" controls preload="metadata" src="${fileUrl(path)}"></video><div class="button-row"><button class="ghost small select-output" data-take="${take.id}" data-output="${index}">${segment.active_take_id===take.id&&take.local_path===path?'✓ Active':'設為 active'}</button><button class="ghost small reload-output" type="button" hidden>重新載入影片</button><button class="danger small remove-output" data-take="${take.id}" data-output="${index}">從專案移除</button></div><small class="take-video-status hint" hidden>影片暫時無法載入，請按「重新載入影片」。</small></div>`).join('');
    const error=lastErrorLines(take.error);
    const errorRecord=error?`<div class="take-error-record"><pre class="take-error">${esc(take.status)} · ${esc(error)}</pre><button class="danger small remove-error" data-take="${take.id}">刪除錯誤紀錄</button></div>`:`<p class="muted">${esc(take.status)}</p>`;
    return `<article class="take"><div class="take-meta"><b>${take.id}</b><span>seed ${take.seed}</span></div>${videos||errorRecord}</article>`;
  }).join('');
}
function bindTakeVideoRecovery(video) {
  const output=video.closest('.take-output');
  const reloadButton=output.querySelector('.reload-output');
  const status=output.querySelector('.take-video-status');
  const source=video.getAttribute('src');
  let attempts=0;
  let retryTimer=null;
  let watchdogTimer=null;
  const scheduleRetry=delay=>{
    if(retryTimer||!video.isConnected)return;
    retryTimer=setTimeout(()=>{
      retryTimer=null;
      if(!video.isConnected)return;
      if(attempts>=3){reloadButton.hidden=false;status.hidden=false;return}
      attempts++;
      video.src=`${source}?reload=${Date.now()}-${attempts}`;
      video.load();
    },delay);
  };
  video.addEventListener('loadstart',()=>{
    clearTimeout(watchdogTimer);
    watchdogTimer=setTimeout(()=>{if(video.readyState===0)scheduleRetry(0)},15000);
  });
  video.addEventListener('loadedmetadata',()=>{
    if(!Number.isFinite(video.duration)||video.duration<=0){scheduleRetry(1000);return}
    clearTimeout(watchdogTimer);
    clearTimeout(retryTimer);
    retryTimer=null;
    attempts=0;
    reloadButton.hidden=true;
    status.hidden=true;
  });
  video.addEventListener('error',()=>scheduleRetry(1000));
  video.addEventListener('stalled',()=>{if(video.readyState===0)scheduleRetry(2000)});
  reloadButton.onclick=()=>{attempts=0;reloadButton.hidden=true;status.hidden=true;scheduleRetry(0)};
  if(video.error||(video.readyState>=1&&(!Number.isFinite(video.duration)||video.duration<=0)))scheduleRetry(0);
  else watchdogTimer=setTimeout(()=>{if(video.readyState===0)scheduleRetry(0)},15000);
}
function renderSegments() {
  const root=document.querySelector('#segments'); root.innerHTML='';
  const warning=document.querySelector('#project-warning');
  const timing=(project.blueprint&&project.blueprint.timing_conflicts)||[];
  const warningMessages=[];
  if(project.segments.length>12) warningMessages.push(`此專案有 ${project.segments.length} 個片段，生成時間與儲存空間成本較高。`);
  warningMessages.push(...timing);
  warning.hidden=!warningMessages.length; warning.innerHTML=warningMessages.map(esc).join('<br>');
  if (!project.segments.length) { root.innerHTML='<div class="empty">儲存故事設定後，先產生影片藍圖。</div>'; return; }
  for (const segment of project.segments) {
    const el=document.createElement('article'); el.className='segment'; el.dataset.segmentId=segment.id;
    const shots=(segment.shots||[]).map((shot,i)=>`<li><b>${Number(shot.start_seconds).toFixed(1)}s · ${esc(shot.framing)}</b> — ${esc(shot.visual)} ${esc(shot.action)}</li>`).join('');
    const binding=segment.template||{};
    el.innerHTML=`<aside class="segment-side"><div class="segment-index">${String(segment.index).padStart(2,'0')}</div><h3>${esc(segment.title)}</h3><p class="segment-summary">${esc(segment.summary)}</p><div class="status-line"><span>${segment.duration_seconds}s</span><span>·</span><span>${esc(segment.task_type)}</span></div><ol class="shot-list">${shots}</ol></aside>
      <div class="segment-main"><div class="status-line">Prompt <span class="status-badge ${segment.prompt_status}">${segment.prompt_status}</span> · 生成 <span class="status-badge ${segment.status}">${segment.status}</span>${segment.continuity_from_previous?' · 承接前段末幀':''}</div><div class="segment-structure-actions button-row"><button class="primary small add-blank-segment">在後方新增空白片段</button><button class="danger small delete-segment">刪除片段</button></div>
      ${segment.timing_warning?`<div class="warning">${esc(segment.timing_warning)}</div>`:''}
      <details class="storyboard-editor"><summary>編輯本段分鏡與對白</summary>
        <div class="form-grid two"><label>時長（4–25 秒）<input class="edit-duration" type="number" min="4" max="25" value="${segment.duration_seconds}"></label><label class="check" title="Workflow 需包含 [[FIRST_FRAME]]，且前段須有可用的 active 影片"><input class="edit-continuity" type="checkbox" ${segment.continuity_from_previous?'checked':''}>承接前段末幀 (I2VA或FL2VA不設定首幀時有效)</label></div>
        <label>片段摘要<textarea class="edit-summary" rows="2">${esc(segment.summary||'')}</textarea></label>
        <label>鏡頭 JSON<textarea class="edit-shots json-editor" rows="8">${esc(JSON.stringify(segment.shots||[],null,2))}</textarea></label>
        <label>對白／旁白 JSON<textarea class="edit-dialogue json-editor" rows="6">${esc(JSON.stringify(segment.dialogue||[],null,2))}</textarea></label>
        <button class="ghost save-storyboard">儲存本段變更</button>
      </details>
      <div class="template-box"><label>Workflow template<select class="template-select">${templateOptions(binding.name)}</select></label><label>H3 任務<select class="edit-task">${['T2VA','I2VA','FL2VA','L2VA','Ref2VA'].map(v=>`<option ${v===segment.task_type?'selected':''}>${v}</option>`).join('')}</select></label><button class="ghost assign-template">套用</button></div><div class="template-note warning" role="note" aria-live="polite" hidden></div>
      <div class="reference-media-box"><div class="mapping-grid" style="display:${['I2VA','FL2VA'].includes(segment.task_type)?'grid':'none'}"><label class="first-frame-field">起始幀<select class="first-frame">${assetOptions(binding.first_frame_asset_id||segment.generated_image_assets?.first_frame)}</select></label><label class="last-frame-field" style="display:${segment.task_type==='FL2VA'?'grid':'none'}">結束幀<select class="last-frame">${assetOptions(binding.last_frame_asset_id||segment.generated_image_assets?.last_frame)}</select></label><label class="scene-ref-field">場景 Ref<select class="scene-ref">${assetOptions(segment.scene_ref_asset_id)}</select></label></div>${renderReferenceOrder(binding)}</div>
      <div class="button-row"><button class="ghost prompt-one">生成新 prompt</button><button class="ghost optimize-prompt">修改/優化 prompt</button><button class="ghost save-prompt" disabled>儲存手動 prompt</button><button class="video-action generate-one">生成/重製本段影片</button><button class="image-action small generate-still" data-kind="first_frame" type="button">生成起始幀</button><button class="image-action small generate-still" data-kind="last_frame" type="button">生成結束幀</button><button class="image-action small generate-still" data-kind="scene" type="button">生成場景圖</button><button class="insert-template small" type="button">插入提示詞模板</button></div><small class="video-readiness hint"></small>
      <textarea class="prompt-box" spellcheck="false">${esc(segment.prompt||'')}</textarea>
      ${(segment.prompt_validation_errors||[]).length?`<div class="warning"><b>阻擋問題</b><br>${segment.prompt_validation_errors.map(esc).join('<br>')}</div>`:''}
      ${(segment.prompt_validation_warnings||[]).length?`<div class="warning validation-advisory"><b>提示（不阻擋生成）</b><br>${segment.prompt_validation_warnings.map(esc).join('<br>')}</div>`:''}
      <div class="takes">${renderTakes(segment)}</div></div>`;
    root.append(el);
  }
  bindSegmentActions();
}
function renderFinals() {
  const root=document.querySelector('#finals');
  root.innerHTML=(project.final_outputs||[]).slice().reverse().map(item=>`<article class="final-card"><p class="eyebrow">FINAL · ${Number(item.actual_duration_seconds).toFixed(2)}s</p><video controls src="${fileUrl(item.path)}"></video></article>`).join('');
}
function render(){document.querySelector('#project-name').textContent=project.name;fillSettings();renderAssets();renderSegments();renderFinals();renderActionAvailability();if(lastLlamaStatus.busy)updateLlmBusyUi(lastLlamaStatus)}

function collectStoryboardEdits(el) {
  const parseArray=selector=>{
    const input=el.querySelector(selector);
    if(!input.value.trim()){input.value='[]';return []}
    return JSON.parse(input.value);
  };
  return {duration_seconds:Number(el.querySelector('.edit-duration').value),continuity_from_previous:el.querySelector('.edit-continuity').checked,summary:el.querySelector('.edit-summary').value,shots:parseArray('.edit-shots'),dialogue:parseArray('.edit-dialogue')};
}
async function saveStoryboard(el,id,renderAfter=true) {
  const saved=await api(`/api/projects/${projectId}/segments/${id}`,jsonOptions('PATCH',collectStoryboardEdits(el)));
  const index=project.segments.findIndex(segment=>segment.id===id);
  if(index>=0)project.segments[index]=saved;
  if(renderAfter)render();
  return saved;
}
async function saveAllStoryboardEdits() {
  for(const el of document.querySelectorAll('.segment'))await saveStoryboard(el,el.dataset.segmentId,false);
}
async function saveChangedStoryboardEdits() {
  for(const el of document.querySelectorAll('.segment')) {
    const segment=project.segments.find(item=>item.id===el.dataset.segmentId);
    if(storyboardEditsChanged(el,segment))await saveStoryboard(el,el.dataset.segmentId,false);
  }
}
function storyboardEditsChanged(el,segment) {
  const edits=collectStoryboardEdits(el);
  const saved={duration_seconds:Number(segment.duration_seconds),continuity_from_previous:!!segment.continuity_from_previous,summary:segment.summary||'',shots:segment.shots||[],dialogue:segment.dialogue||[]};
  return JSON.stringify(edits)!==JSON.stringify(saved);
}

async function openPromptTemplateDialog(promptBox) {
  const dialog=document.querySelector('#prompt-template-dialog');
  const body=document.querySelector('#prompt-template-body');
  const status=document.querySelector('#prompt-template-status');
  const caretStart=promptBox.selectionStart;
  const caretEnd=promptBox.selectionEnd;
  body.innerHTML='';
  status.textContent='讀取提示詞模板…';
  dialog.showModal();
  try {
    const data=await api('/api/prompt-templates');
    status.textContent=data.errors?.length?data.errors.join('；'):(data.templates.length?'選擇一筆模板插入游標位置。':'目前沒有提示詞模板。');
    body.innerHTML=data.templates.map((item,index)=>`<tr><td>${esc(item.Name)}</td><td>${esc(item.Type)}</td><td>${esc(item.Note)}</td><td class="prompt-template-preview">${esc(item.Prompt)}</td><td><button class="insert-template small" type="button" data-index="${index}">插入</button></td></tr>`).join('');
    for(const button of body.querySelectorAll('button'))button.onclick=()=>{
      const item=data.templates[Number(button.dataset.index)];
      promptBox.setRangeText(item.Prompt,caretStart,caretEnd,'end');
      promptBox.dispatchEvent(new Event('input',{bubbles:true}));
      promptBox.focus();
      dialog.close();
      toast(`已插入「${item.Name}」；請儲存手動 prompt`);
    };
  } catch(error) {status.textContent=`讀取模板失敗：${error.message}`}
}

function updateStillButtons(el) {
  let ready=!!el.querySelector('.edit-summary').value.trim();
  try {ready=ready&&Array.isArray(JSON.parse(el.querySelector('.edit-shots').value))&&JSON.parse(el.querySelector('.edit-shots').value).length>0}
  catch {ready=false}
  for(const button of el.querySelectorAll('.generate-still')){
    button.disabled=!ready||!!lastLlamaStatus.busy;
    button.title=ready?'可選功能：生成本段參考圖片':'請先填寫片段摘要與鏡頭 JSON';
  }
}

async function openSegmentImageDialog(el,segmentId,kind) {
  const dialog=document.querySelector('#segment-image-dialog');
  const status=document.querySelector('#segment-image-status');
  try {if(storyboardEditsChanged(el,project.segments.find(item=>item.id===segmentId)))await saveStoryboard(el,segmentId,false)}
  catch(error){toast(`請先儲存有效分鏡：${error.message}`,true);return}
  const labels={first_frame:'起始幀',last_frame:'結束幀',scene:'場景圖'};
  document.querySelector('#segment-image-title').textContent=`生成${labels[kind]} · ${segmentId}`;
  const assets=project.assets.filter(asset=>['image','character','video'].includes(assetMediaType(asset)));
  document.querySelector('#segment-image-assets').innerHTML=assets.map(asset=>`<label class="segment-image-asset"><input type="checkbox" value="${esc(asset.id)}"><span>${esc(asset.original_name)}</span></label>`).join('')||'<p class="hint">目前沒有可用素材，可直接文字生圖。</p>';
  const promptInput=dialog.querySelector('#segment-image-prompt');
  const rewrite=dialog.querySelector('#segment-image-rewrite');
  const generate=dialog.querySelector('#segment-image-generate');
  const batch=dialog.querySelector('#segment-image-batch');
  const results=dialog.querySelector('#segment-image-results');
  rewrite.disabled=true;generate.disabled=true;
  promptInput.value='';
  batch.value='1';
  status.textContent='';
  results.innerHTML='';
  dialog.showModal();
  const selected=()=>[...dialog.querySelectorAll('.segment-image-asset input:checked')].map(input=>input.value);
  dialog.querySelector('#segment-image-assets').onchange=event=>{
    if(selected().length>3){event.target.checked=false;status.textContent='最多可選 3 個素材';}
    else {promptInput.value='';status.textContent='參考素材已變更，請重新生成或填寫提示詞'}
  };
  const payload=()=>({kind,asset_ids:selected()});
  let candidates=[];
  const renderCandidates=()=>{
    const segment=project.segments.find(item=>item.id===segmentId);
    const selectedId=segment?.selected_image_candidate_ids?.[kind];
    generate.textContent=candidates.length?'重新生成':'生成圖';
    results.innerHTML=candidates.length?`<h3>生成結果 · ${candidates.length} 張</h3>`+candidates.slice().reverse().map(candidate=>`<article class="segment-image-result"><img src="${fileUrl(candidate.path)}" alt="${esc(candidate.filename)}"><div><b>${esc(candidate.filename)}</b><small>Seed ${esc(candidate.seed)}</small><button class="primary small adopt-image" type="button" data-id="${esc(candidate.id)}" ${candidate.id===selectedId?'disabled':''}>${candidate.id===selectedId?'✓ 已採用':'採用此圖'}</button></div></article>`).join(''):'<p class="hint">生成後可在這裡比較結果，再選一張放入專案。</p>';
    for(const button of results.querySelectorAll('.adopt-image:not(:disabled)'))button.onclick=async()=>{
      button.disabled=true;status.textContent='正在將圖片加入專案…';
      try{const adopted=await api(`/api/projects/${projectId}/segments/${segmentId}/images/${button.dataset.id}/adopt`,{method:'POST'});await refresh();candidates=candidates.map(item=>item.id===adopted.candidate.id?adopted.candidate:item);renderCandidates();status.textContent=`已採用 ${adopted.asset.original_name}，並更新本段${labels[kind]}`}
      catch(error){button.disabled=false;status.textContent=`採用失敗：${error.message}`}
    };
  };
  try{
    const listed=await api(`/api/projects/${projectId}/segments/${segmentId}/images?kind=${encodeURIComponent(kind)}`);
    candidates=listed.candidates||[];
    const latest=candidates.at(-1);
    if(latest){
      const wanted=latest.reference_asset_ids||[];
      for(const input of dialog.querySelectorAll('.segment-image-asset input'))input.checked=wanted.includes(input.value);
      if(selected().length===wanted.length)promptInput.value=latest.prompt||'';
      else status.textContent='部分參考素材已移除，請重新生成提示詞';
    }
    renderCandidates();
  }
  catch(error){status.textContent=`讀取先前結果失敗：${error.message}`}
  finally{rewrite.disabled=false;generate.disabled=false}
  const createPrompt=async()=>{
    status.textContent='LLM 正在生成提示詞…';beginLocalLlmOperation(`生成 ${labels[kind]} 提示詞`);
    try{const result=await api(`/api/projects/${projectId}/segments/${segmentId}/images/prompt`,jsonOptions('POST',payload()));promptInput.value=result.prompt;status.textContent='提示詞已生成，可以修改或直接生成圖片';return result.prompt}
    finally{await refreshLlamaStatus()}
  };
  rewrite.onclick=async()=>{
    rewrite.disabled=true;generate.disabled=true;
    try{await createPrompt()}
    catch(error){status.textContent=`提示詞生成失敗：${error.message}`}
    finally{rewrite.disabled=false;generate.disabled=false}
  };
  generate.onclick=async()=>{
    generate.disabled=true;rewrite.disabled=true;batch.disabled=true;
    try{
      if(!promptInput.value.trim())await createPrompt();
      const count=Number(batch.value);
      status.textContent=`LLM 正在取檔名，接著送出 ${count} 個 ComfyUI 生圖工作…`;
      const result=await api(`/api/projects/${projectId}/segments/${segmentId}/images`,jsonOptions('POST',{...payload(),prompt:promptInput.value,batch:count}));
      candidates.push(...(result.candidates||[]));renderCandidates();
      const warnings=[...(result.errors||[])];
      if(result.memory_release_error)warnings.push(`ComfyUI 記憶體釋放失敗：${result.memory_release_error}`);
      status.textContent=`已生成 ${result.candidates.length}/${count} 張；請從下方選擇要採用的圖片。${warnings.length?' '+warnings.join('；'):''}`;
    }
    catch(error){status.textContent=`圖片生成失敗：${error.message}`;try{const listed=await api(`/api/projects/${projectId}/segments/${segmentId}/images?kind=${encodeURIComponent(kind)}`);candidates=listed.candidates||[];renderCandidates()}catch{}}
    finally{generate.disabled=false;rewrite.disabled=false;batch.disabled=false}
  };
}

function bindSegmentActions() {
  for (const el of document.querySelectorAll('.segment')) {
    const id=el.dataset.segmentId;
    const segment=project.segments.find(item=>item.id===id);
    for(const video of el.querySelectorAll('.take-video'))bindTakeVideoRecovery(video);
    const promptBox=el.querySelector('.prompt-box');
    const manualSave=el.querySelector('.save-prompt');
    const updateManualSave=()=>{manualSave.disabled=promptBox.value===String(segment.prompt||'')};
    promptBox.addEventListener('input',updateManualSave);
    updateManualSave();
    showTemplateNote(el,segment.template||{});
    el.querySelector('.template-select').addEventListener('change',()=>showTemplateNote(el,segment.template||{}));
    const updateMappingVisibility=()=>{
      const task=el.querySelector('.edit-task').value;
      el.querySelector('.mapping-grid').style.display=['I2VA','FL2VA'].includes(task)?'grid':'none';
      el.querySelector('.last-frame-field').style.display=task==='FL2VA'?'grid':'none';
    };
    el.querySelector('.edit-task').addEventListener('change',updateMappingVisibility);
    el.querySelector('.save-storyboard').onclick=async()=>{try{await saveStoryboard(el,id);toast('分鏡已儲存，prompt 已標記過期')}catch(e){toast(`分鏡未儲存：${e.message}`,true)}};
    for(const button of el.querySelectorAll('.generate-still'))button.onclick=()=>openSegmentImageDialog(el,id,button.dataset.kind);
    el.querySelector('.insert-template').onclick=()=>openPromptTemplateDialog(el.querySelector('.prompt-box'));
    for(const input of el.querySelectorAll('.edit-summary,.edit-shots'))input.addEventListener('input',()=>updateStillButtons(el));
    updateStillButtons(el);
    el.querySelector('.assign-template').onclick=async()=>{try{const name=el.querySelector('.template-select').value;const task_type=el.querySelector('.edit-task').value;if(!name)throw new Error('請先選擇 template');await api(`/api/projects/${projectId}/segments/${id}/template`,jsonOptions('POST',{name,task_type}));await refresh();toast('Template 與 H3 任務已套用')}catch(e){toast(e.message,true)}};
    el.querySelector('.add-blank-segment').onclick=async()=>{try{await saveChangedStoryboardEdits();project=await api(`/api/projects/${projectId}/segments`,jsonOptions('POST',{after_segment_id:id}));render();toast('已在本段後方新增空白片段')}catch(e){toast(e.message,true)}};
    el.querySelector('.delete-segment').onclick=async()=>{if(!window.confirm('片段的分鏡、prompt、template 與 take 紀錄將從專案永久刪除，無法復原；尚未儲存的畫面編輯也會遺失。硬碟上的生成檔案不會刪除。確定繼續？'))return;try{project=await api(`/api/projects/${projectId}/segments/${id}`,{method:'DELETE'});render();toast('片段已永久從專案刪除')}catch(e){toast(e.message,true)}};
    const collectReferenceOrder=()=>[...el.querySelectorAll('.ref-order-select')].map(input=>input.value).filter(Boolean);
    const saveMapping=async(includeReferenceOrder=true)=>{if(!segment.template)return segment;const payload={first_frame_asset_id:el.querySelector('.first-frame').value||null,last_frame_asset_id:el.querySelector('.last-frame').value||null,scene_ref_asset_id:el.querySelector('.scene-ref').value||null};if(includeReferenceOrder)payload.image_order=collectReferenceOrder();return api(`/api/projects/${projectId}/segments/${id}/template`,jsonOptions('PATCH',payload))};
    for(const input of el.querySelectorAll('.first-frame,.last-frame,.scene-ref')) input.addEventListener('change',()=>saveMapping(false).then(()=>toast('Template 參考圖片已儲存')).catch(e=>toast(e.message,true)));
    const updateReferenceLabels=()=>{
      const result=mapReferenceMedia({...segment.template,image_order:collectReferenceOrder()},project.assets);
      for(const input of el.querySelectorAll('.ref-order-select')) {
        const asset=project.assets.find(item=>item.id===input.value);
        const row=input.closest('.ref-slot');
        row.querySelector('.ref-slot-preview').innerHTML=referencePreview(asset);
        row.querySelector('.ref-token-label').textContent=Object.entries(result.mapping).filter(([token,id])=>id===input.value).map(([token])=>`[[${token}]]`).join(' + ')||'選取參考媒體（可留空）';
      }
      const status=el.querySelector('.ref-mapping-status');
      if(status)status.textContent=result.errors.join('；')||(result.missing.length?'尚缺：'+result.missing.map(t=>`[[${t}]]`).join('、'):'所有插槽已對應');
    };
    for(const input of el.querySelectorAll('.ref-order-select')) input.addEventListener('change',updateReferenceLabels);
    const saveRefOrder=el.querySelector('.save-ref-order');
    if(saveRefOrder)saveRefOrder.onclick=async()=>{try{const result=mapReferenceMedia({...segment.template,image_order:collectReferenceOrder()},project.assets);if(result.errors.length||result.missing.length)throw new Error(result.errors.join('；')||'尚缺：'+result.missing.join('、'));await saveMapping();await refresh();toast('參考媒體順序已儲存')}catch(e){toast(e.message,true)}};
    const autoRefOrder=el.querySelector('.auto-ref-order');
    if(autoRefOrder)autoRefOrder.onclick=()=>{const order=[];for(const asset of project.assets){const trial=mapReferenceMedia({...segment.template,image_order:[...order,asset.id]},project.assets);if(!trial.errors.length)order.push(asset.id);}el.querySelectorAll('.ref-order-select').forEach((input,index)=>input.value=order[index]||'');updateReferenceLabels();};
    const suggestRefOrder=el.querySelector('.suggest-ref-order');
    if(suggestRefOrder)suggestRefOrder.onclick=async()=>{try{beginLocalLlmOperation(`建議 ${id} 參考圖順序`);const result=await api(`/api/projects/${projectId}/segments/${id}/reference-order/suggest`,{method:'POST'});const selects=[...el.querySelectorAll('.ref-order-select')];selects.forEach(input=>input.value='');result.image_order.forEach((assetId,index)=>{if(selects[index]){selects[index].value=assetId;selects[index].dispatchEvent(new Event('change'))}});el.querySelector('.ref-order-rationale').textContent=result.rationale||'LLM 已填入建議順序；請確認後儲存。';await refreshLlamaStatus();toast('LLM 建議已填入，尚未儲存')}catch(e){await refreshLlamaStatus();toast(e.message,true)}};
    el.querySelector('.prompt-one').onclick=async()=>{try{await saveStoryboard(el,id,false);beginLocalLlmOperation(`產生 ${id} prompt`);await api(`/api/projects/${projectId}/segments/${id}/prompt`,{method:'POST'});await refresh();await refreshLlamaStatus();toast('本段編輯已儲存，Prompt 已更新')}catch(e){await refreshLlamaStatus();toast(e.message,true)}};
    el.querySelector('.optimize-prompt').onclick=async()=>{try{const prompt=el.querySelector('.prompt-box').value;if(!prompt.trim())throw new Error('請先輸入目前的 prompt');const task=el.querySelector('.edit-task').value;const frameTask=['I2VA','FL2VA'].includes(task);const reference_selection={first_frame_asset_id:frameTask?el.querySelector('.first-frame').value||null:null,last_frame_asset_id:task==='FL2VA'?el.querySelector('.last-frame').value||null:null,scene_ref_asset_id:frameTask?el.querySelector('.scene-ref').value||null:null,image_order:collectReferenceOrder()};beginLocalLlmOperation(`修改/優化 ${id} prompt`);const result=await api(`/api/projects/${projectId}/segments/${id}/prompt/optimize`,jsonOptions('POST',{prompt,reference_selection}));await refresh();await refreshLlamaStatus();toast(result.prompt_status==='ready'?'Prompt 已優化並儲存':'Prompt 已儲存，但格式仍有問題',result.prompt_status!=='ready')}catch(e){await refreshLlamaStatus();toast(e.message,true)}};
    manualSave.onclick=async()=>{try{manualSave.disabled=true;await api(`/api/projects/${projectId}/segments/${id}/prompt`,jsonOptions('PUT',{prompt:promptBox.value}));await refresh();toast('手動 prompt 已驗證並儲存')}catch(e){updateManualSave();toast(e.message,true)}};
    el.querySelector('.generate-one').onclick=async event=>{const button=event.currentTarget;try{if(!segmentCanGenerateVideo(segment))throw new Error(segmentGenerationReason(segment));button.disabled=true;await saveMapping();const result=await api(`/api/projects/${projectId}/segments/${id}/generate`,{method:'POST'});await refreshGenerationQueue();toast(`已加入 ${result.jobs.length} 筆影片工作`)}catch(e){toast(e.message,true)}finally{renderActionAvailability()}};
    for(const button of el.querySelectorAll('.select-output')) button.onclick=async()=>{try{await api(`/api/projects/${projectId}/segments/${id}/takes/${button.dataset.take}/select`,jsonOptions('POST',{output_index:Number(button.dataset.output)}));await refresh()}catch(e){toast(e.message,true)}};
    for(const button of el.querySelectorAll('.remove-output')) button.onclick=async()=>{if(!window.confirm('只會從專案移除此影片，硬碟上的檔案會保留。確定繼續？'))return;try{await api(`/api/projects/${projectId}/segments/${id}/takes/${button.dataset.take}/outputs/${button.dataset.output}`,{method:'DELETE'});await refresh();toast('影片已從專案移除，硬碟檔案仍保留')}catch(e){toast(lastErrorLines(e.message),true)}};
    for(const button of el.querySelectorAll('.remove-error')) button.onclick=async()=>{if(!window.confirm('永久刪除這筆 ComfyUI 錯誤紀錄？此操作無法復原。'))return;try{await api(`/api/projects/${projectId}/segments/${id}/takes/${button.dataset.take}/error`,{method:'DELETE'});await refresh();toast('錯誤紀錄已刪除')}catch(e){toast(lastErrorLines(e.message),true)}};
  }
}
document.querySelector('#story-form').addEventListener('submit',async(event)=>{event.preventDefault();try{await saveSettings();toast('設定已儲存')}catch(e){toast(e.message,true)}});
document.querySelector('#aspect-ratio').addEventListener('change',event=>renderResolutionOptions(event.target.value,document.querySelector('#target-megapixels').value));
async function uploadAssets(files) {
  for(const file of files){const form=new FormData();form.append('image',file);try{await api(`/api/projects/${projectId}/assets`,{method:'POST',body:form})}catch(e){toast(`${file.name}: ${e.message}`,true)}}
  await refresh();
}
document.querySelector('#asset-upload').addEventListener('change',async event=>{await uploadAssets(event.target.files);event.target.value=''});
const assetDropZone=document.querySelector('#asset-drop-zone');
for(const eventName of ['dragenter','dragover'])assetDropZone.addEventListener(eventName,event=>{event.preventDefault();assetDropZone.classList.add('drag-active')});
for(const eventName of ['dragleave','drop'])assetDropZone.addEventListener(eventName,event=>{event.preventDefault();assetDropZone.classList.remove('drag-active')});
assetDropZone.addEventListener('drop',event=>uploadAssets(event.dataTransfer.files));
document.querySelector('#scan-assets').onclick=()=>api(`/api/projects/${projectId}/assets/scan`,{method:'POST'}).then(refresh).catch(e=>toast(e.message,true));
document.querySelector('#blueprint-button').onclick=async()=>{try{toast('正在儲存設定…');await saveSettings(false);beginLocalLlmOperation('產生影片藍圖');project=await api(`/api/projects/${projectId}/blueprint`,{method:'POST'});render();await refreshLlamaStatus();toast('影片藍圖已建立')}catch(e){await refreshLlamaStatus();toast(e.message,true)}};
document.querySelector('#blank-blueprint-button').onclick=async event=>{const button=event.currentTarget;try{if(project.segments.length)throw new Error('已有分鏡，不能建立空白分鏡');button.disabled=true;toast('正在儲存設定…');await saveSettings(false);if(project.segments.length)throw new Error('已有分鏡，不能建立空白分鏡');project=await api(`/api/projects/${projectId}/blueprint/blank`,{method:'POST'});render();toast(`已建立 ${project.segments.length} 個空白分鏡`)}catch(e){toast(e.message,true)}finally{renderActionAvailability()}};
document.querySelector('#prompts-all').onclick=async()=>{try{await saveAllStoryboardEdits();beginLocalLlmOperation('依序產生全部 prompts');project=await api(`/api/projects/${projectId}/prompts`,{method:'POST'});render();await refreshLlamaStatus();toast('所有分鏡編輯已儲存，Prompts 已更新')}catch(e){await refreshLlamaStatus();toast(e.message,true)}};
document.querySelector('#generate-all').onclick=async event=>{const button=event.currentTarget;try{if(!projectCanGenerateVideos())throw new Error('每個片段都必須先有 ready prompt 並套用 workflow template JSON');button.disabled=true;const result=await api(`/api/projects/${projectId}/generate`,{method:'POST'});await refreshGenerationQueue();toast(`已加入 ${result.jobs.length} 筆影片工作；遠端工作會連續送交 ComfyUI`)}catch(e){toast(e.message,true)}finally{renderActionAvailability()}};
document.querySelector('#merge-all').onclick=async()=>{try{toast('FFmpeg 正在合併 active takes…');await api(`/api/projects/${projectId}/merge`,{method:'POST'});await refresh();toast('最終影片已完成')}catch(e){toast(e.message,true)}};

const dialog=document.querySelector('#connection-dialog');
function renderActiveLlm(llm){
  const active=llm.active;
  document.querySelector('#active-llm-kind').textContent=active.is_local?'LOCAL RUNTIME':'EXTERNAL API';
  document.querySelector('#active-llm-provider').textContent=active.provider_label;
  document.querySelector('#active-llm-model').textContent=active.model||'尚未選擇模型';
  document.querySelector('#active-llm-endpoint').textContent=`${active.is_local?'本機服務':'API endpoint'} · ${active.base_url}`;
  document.querySelector('#active-llm-key').textContent=active.is_local?'此模式不使用 API Key':active.api_key_set?'API Key · 已從 .env 載入':'API Key · 尚未設定';
  const unload=document.querySelector('#unload-llm');
  unload.hidden=!active.is_local;
  document.querySelector('#unload-llm-hint').textContent=active.is_local?'若 LLM 正在生成，手動釋放 VRAM 會立即中斷該次工作；下次使用 LLM 時會自動重新載入。':'目前使用外部 API，不會載入本機模型或占用本機 VRAM。';
  document.querySelector('#shared-gpu-hint').textContent=active.is_local?'共用 GPU 時，送件前只會關閉本 app 啟動的 llama.cpp；不檢查或管理其他程序的 VRAM。若 ComfyUI OOM，錯誤會由 ComfyUI 回報。':'目前 LLM 使用外部 API；產生影片時不需要卸載本機 LLM，會直接送交 ComfyUI。';
}
document.querySelector('#settings-button').onclick=async()=>{const c=await api('/api/config');document.querySelector('#comfy-url').value=c.comfy_url;document.querySelector('#shared-gpu').checked=c.comfy_shared_gpu;renderActiveLlm(c.llm);dialog.showModal()};
document.querySelector('#rename-button').onclick=async()=>{const name=window.prompt('新專案名稱',project.name);if(!name)return;try{project=await api(`/api/projects/${projectId}`,jsonOptions('PATCH',{name}));render();toast('專案已重新命名')}catch(e){toast(e.message,true)}};
document.querySelector('#archive-button').onclick=async()=>{if(!window.confirm('封存此專案？檔案與生成結果都會保留。'))return;try{await api(`/api/projects/${projectId}`,jsonOptions('PATCH',{archived:true}));location.href='/'}catch(e){toast(e.message,true)}};
document.querySelector('#save-config').onclick=async()=>{try{const c=await api('/api/config',jsonOptions('PATCH',{comfy_url:document.querySelector('#comfy-url').value,comfy_shared_gpu:document.querySelector('#shared-gpu').checked}));renderActiveLlm(c.llm);toast('連線設定已儲存')}catch(e){toast(e.message,true)}};
document.querySelector('#test-comfy').onclick=async()=>{const out=document.querySelector('#connection-result');out.textContent='測試中…';try{const result=await api('/api/comfy/test',{method:'POST'});out.textContent=JSON.stringify(result,null,2);document.querySelector('#comfy-pill').textContent='ComfyUI 已連線';document.querySelector('#comfy-pill').className='pill ok'}catch(e){out.textContent=lastErrorLines(e.message);document.querySelector('#comfy-pill').textContent='ComfyUI 錯誤';document.querySelector('#comfy-pill').className='pill bad'}};
document.querySelector('#unload-llm').onclick=async event=>{const button=event.currentTarget;const out=document.querySelector('#connection-result');if(lastLlamaStatus.busy&&!window.confirm('LLM 正在生成，確定要立即中斷並釋放 VRAM？'))return;button.disabled=true;out.textContent='正在中斷 LLM 並釋放 VRAM…';try{const result=await api('/api/llama/unload',{method:'POST'});lastLlamaStatus={busy:false};await refreshLlamaStatus();out.textContent=result.message;toast(result.message)}catch(e){out.textContent=e.message;toast(e.message,true)}finally{button.disabled=false}};

async function init(){document.querySelector('#llm-pill').textContent='LLM 檢查中…';const results=await Promise.all([api('/api/templates'),refreshLlamaStatus()]);templateData=results[0];render();await refreshGenerationQueue();const source=new EventSource(`/api/projects/${projectId}/events`);source.onmessage=async event=>{const data=JSON.parse(event.data);const segmentId=data.details&&data.details.segment_id;if(data.kind.startsWith('generation_')){if(segmentId)generationQueueMessages.set(segmentId,data.message);await refreshGenerationQueue(false);if(data.kind.includes('completed')||data.kind.includes('error')){if(segmentId)generationQueueMessages.delete(segmentId);await refresh(true);toast(data.kind.includes('error')?lastErrorLines(data.message):data.message,data.kind.includes('error'))}return}toast(data.message,data.kind.includes('error'),data.kind==='llm_progress')}}
init().catch(e=>toast(e.message,true));
