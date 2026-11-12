(() => {
  const dialog = document.querySelector('#character-dialog');
  const grid = document.querySelector('#character-grid');
  const search = document.querySelector('#character-search');
  const category = document.querySelector('#character-category');
  const voiceOnly = document.querySelector('#character-voice-only');
  const selectedOnly = document.querySelector('#character-selected-only');
  const status = document.querySelector('#character-status');
  const add = document.querySelector('#character-add');
  const selected = new Set();
  let items = [], page = 0, loading = false;
  const stopAudio = () => grid.querySelectorAll('audio').forEach(audio => audio.pause());
  const mediaUrl = (item, name) => `/api/characters/${encodeURIComponent(item.id)}/files/${name}`;
  function renderLibrary() {
    stopAudio();
    const term = search.value.trim().toLocaleLowerCase();
    const matches = items.filter(item => (!term || `${item.name} ${item.profile} ${item.line}`.toLocaleLowerCase().includes(term)) &&
      (!category.value || item.category === category.value) && (!voiceOnly.checked || item.media.includes('voice.wav')) &&
      (!selectedOnly.checked || selected.has(item.id)));
    const pages = Math.max(1, Math.ceil(matches.length / 40));
    page = Math.min(page, pages - 1);
    status.textContent = loading ? '正在讀取角色…' : `找到 ${matches.length} / ${items.length} 位角色 · 已選 ${selected.size} 位`;
    grid.innerHTML = matches.slice(page * 40, (page + 1) * 40).map(item => `<article class="character-card ${selected.has(item.id) ? 'selected' : ''}">
      ${item.media.includes('headshot.png') ? `<img src="/api/characters/${item.id}/thumbnail" alt="${esc(item.name)}的大頭照" loading="lazy" decoding="async">` : ''}
      <div class="character-card-body"><label><input type="checkbox" data-id="${item.id}" ${!item.media.includes('turnaround.png')||!item.media.includes('voice.wav')?'disabled':''} ${selected.has(item.id) ? 'checked' : ''}>${esc(item.name)} <small>${esc(item.category)}</small></label>
      <p>${esc(item.introduction||'')}</p>
      ${item.media.includes('voice.wav') ? `<audio controls preload="none" src="${mediaUrl(item, 'voice.wav')}" aria-label="試聽${esc(item.name)}的語音"></audio>` : '<p>尚無語音</p>'}
      <details><summary>台詞與素材</summary>${item.media.includes('turnaround.png') ? `<img class="character-turnaround" src="/api/characters/${item.id}/thumbnail?view=turnaround" alt="${esc(item.name)}的三視圖" loading="lazy">` : '<p>缺少三視圖，無法加入</p>'}<p>${esc(item.line || '尚無台詞')}</p><p>${item.media.map(name => ({'headshot.png':'大頭照','turnaround.png':'三視圖','voice.wav':'語音'}[name])).join(' · ')}</p></details></div></article>`).join('') || '<p class="empty">沒有符合的角色，請調整搜尋或篩選。</p>';
    document.querySelector('#character-page').textContent = `${page + 1} / ${pages}`;
    document.querySelector('#character-prev').disabled = page === 0;
    document.querySelector('#character-next').disabled = page >= pages - 1;
    add.textContent = `加入所選角色（${selected.size}）`;
    add.disabled = loading || !selected.size;
  }
  document.querySelector('#choose-characters').onclick = async () => {
    dialog.showModal();
    if (items.length) return renderLibrary();
    loading = true; renderLibrary();
    try {
      items = await api('/api/characters');
      category.innerHTML = '<option value="">全部分類</option>' + [...new Set(items.map(item => item.category))].map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('');
      loading = false; renderLibrary(); search.focus();
    } catch (error) { loading = false; status.textContent = `讀取失敗：${error.message}，請關閉後重試。`; }
  };
  [search, category, voiceOnly, selectedOnly].forEach(input => input.addEventListener('input', () => {page = 0; renderLibrary();}));
  grid.addEventListener('change', event => {
    const input = event.target;
    if (!input.matches('input[data-id]')) return;
    if (input.checked && selected.size >= 30) {input.checked = false; status.textContent = '每次最多加入 30 人'; return;}
    if (input.checked) selected.add(input.dataset.id); else selected.delete(input.dataset.id);
    input.closest('.character-card').classList.toggle('selected', input.checked);
    add.textContent = `加入所選角色（${selected.size}）`; add.disabled = !selected.size;
    status.textContent = `已選 ${selected.size} 位角色`;
    if (selectedOnly.checked) renderLibrary();
  });
  grid.addEventListener('play', event => grid.querySelectorAll('audio').forEach(audio => {if (audio !== event.target) audio.pause();}), true);
  dialog.addEventListener('close', stopAudio);
  document.querySelector('#character-prev').onclick = () => {page--; renderLibrary(); dialog.scrollTop = 0;};
  document.querySelector('#character-next').onclick = () => {page++; renderLibrary(); dialog.scrollTop = 0;};
  add.onclick = async () => {
    add.disabled = true; add.textContent = '正在複製角色素材…';
    try {
      for (const card of document.querySelectorAll('.asset-card')) await card.flushSave();
      const result = await api(`/api/projects/${projectId}/characters`, jsonOptions('POST', {ids:[...selected]}));
      project.assets = result.assets;
      renderAssets(); selected.clear(); dialog.close(); toast('角色已加入為單一素材（三視圖＋語音）');
    } catch (error) { status.textContent = `加入失敗：${error.message}`; }
    finally { add.disabled = !selected.size; add.textContent = `加入所選角色（${selected.size}）`; }
  };
})();

document.querySelector('#export-project').onclick = async event => {
  const button = event.currentTarget;
  button.disabled = true; button.textContent = '正在儲存與打包…';
  try {
    for (const card of document.querySelectorAll('.asset-card')) await card.flushSave();
    await saveSettings(false);
    await saveChangedStoryboardEdits();
    for (const el of document.querySelectorAll('.segment')) {
      const saved = project.segments.find(item => item.id === el.dataset.segmentId);
      const prompt = el.querySelector('.prompt-box').value;
      if (prompt !== (saved.prompt || '')) await api(`/api/projects/${projectId}/segments/${saved.id}/prompt`, jsonOptions('PUT', {prompt}));
    }
    const response = await fetch(`/api/projects/${projectId}/export`);
    if (!response.ok) {const error = await response.json(); throw new Error(error.detail || '打包失敗');}
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url; link.download = `${projectId}.vap.zip`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    toast('專案包已下載；到另一台電腦的首頁選擇「匯入專案包」即可繼續編輯');
  } catch (error) {toast(error.message, true);}
  finally {button.disabled = false; button.textContent = '打包下載專案';}
};
