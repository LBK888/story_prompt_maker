(() => {
  const button = document.querySelector('#import-project');
  const input = document.querySelector('#project-archive');
  button.onclick = () => input.click();
  input.onchange = async () => {
    if (!input.files.length) return;
    button.disabled = true;
    button.textContent = '正在上傳與匯入…';
    try {
      const body = new FormData();
      body.append('archive', input.files[0]);
      const response = await fetch('/api/project-import', {method:'POST', body});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || '匯入失敗');
      location.href = `/projects/${encodeURIComponent(result.id)}`;
    } catch (error) {
      showToast(error.message, true);
    } finally {
      input.value = '';
      button.disabled = false;
      button.textContent = '匯入專案包';
    }
  };
})();
