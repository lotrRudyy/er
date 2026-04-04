const editorStatus = document.getElementById('editorStatus');

function showStatus(message, kind = 'info') {
  if (!editorStatus) return;
  editorStatus.hidden = false;
  editorStatus.textContent = message;
  editorStatus.className = 'viewer-message editor-status';
  if (kind === 'error') editorStatus.classList.add('viewer-error');
  if (kind === 'success') editorStatus.classList.add('viewer-success');
}

function normalizeDisplay(value) {
  return value === '' ? '—' : value;
}

function getEditorValue(cell) {
  const editor = cell.querySelector('textarea, input');
  if (editor) return editor.value;
  return cell.dataset.currentValue ?? cell.dataset.value ?? '';
}

function updateRowDirtyState(row) {
  const saveButton = row?.querySelector('.save-row-button');
  if (!saveButton) return;
  const dirty = Array.from(row.querySelectorAll('.editable-cell')).some((cell) => {
    const currentValue = getEditorValue(cell);
    return currentValue !== (cell.dataset.value ?? '');
  });
  saveButton.disabled = !dirty;
}

function autoResize(editor) {
  editor.style.height = 'auto';
  editor.style.height = `${Math.max(editor.scrollHeight, 38)}px`;
}

function openEditor(cell) {
  if (!cell || cell.dataset.readonly === 'true') return;
  if (cell.querySelector('textarea, input')) return;

  const value = cell.dataset.currentValue ?? cell.dataset.value ?? '';
  const useTextarea = value.includes('\n') || value.length > 60 || value.trim().startsWith('{') || value.trim().startsWith('[');
  const editor = document.createElement(useTextarea ? 'textarea' : 'input');
  editor.className = 'cell-editor';
  editor.value = value;
  if (!useTextarea) editor.type = 'text';
  cell.dataset.currentValue = value;
  cell.classList.add('is-editing');
  cell.textContent = '';
  cell.appendChild(editor);
  if (useTextarea) autoResize(editor);

  editor.addEventListener('input', () => {
    cell.dataset.currentValue = editor.value;
    if (useTextarea) autoResize(editor);
    updateRowDirtyState(cell.closest('tr'));
  });

  editor.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      cell.dataset.currentValue = cell.dataset.value ?? '';
      closeEditor(cell, false);
      updateRowDirtyState(cell.closest('tr'));
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      const row = cell.closest('tr');
      const saveButton = row?.querySelector('.save-row-button');
      saveButton?.click();
    }
  });

  editor.focus();
  if (editor.setSelectionRange) {
    const end = editor.value.length;
    editor.setSelectionRange(end, end);
  }
}

function closeEditor(cell, keepCurrent = true) {
  const editor = cell.querySelector('textarea, input');
  const nextValue = keepCurrent ? (editor ? editor.value : (cell.dataset.currentValue ?? cell.dataset.value ?? '')) : (cell.dataset.value ?? '');
  cell.dataset.currentValue = nextValue;
  if (editor) editor.remove();
  cell.classList.remove('is-editing');
  cell.textContent = normalizeDisplay(nextValue);
}

function bindViewerEditors(scope = document) {
  scope.querySelectorAll('.editable-cell').forEach((cell) => {
    if (cell.dataset.bound === 'true') return;
    cell.dataset.bound = 'true';
    cell.addEventListener('click', (event) => {
      if (event.target.closest('textarea, input')) return;
      openEditor(cell);
    });
  });

  scope.querySelectorAll('.save-row-button').forEach((button) => {
    if (button.dataset.bound === 'true') return;
    button.dataset.bound = 'true';
    button.addEventListener('click', () => {
      const row = button.closest('tr');
      if (row) saveRow(row);
    });
  });
}

async function refreshViewerFromServer() {
  const url = new URL(window.location.href);
  url.searchParams.set('_ts', String(Date.now()));
  const res = await fetch(url.toString(), { headers: { 'X-Requested-With': 'fetch' } });
  if (!res.ok) throw new Error('Aktualisieren der Ansicht fehlgeschlagen');
  const html = await res.text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  ['allGamesSection', 'gameSummarySection', 'riddlesSection', 'hintsSection', 'rawDbSection'].forEach((id) => {
    const next = doc.getElementById(id);
    const current = document.getElementById(id);
    if (current && next) current.replaceWith(next);
  });
  bindViewerEditors(document);
}

async function saveRow(row) {
  const button = row.querySelector('.save-row-button');
  const table = row.dataset.table;
  const rowid = row.dataset.rowid;
  const updates = {};
  const editableCells = Array.from(row.querySelectorAll('.editable-cell'));

  editableCells.forEach((cell) => {
    const currentValue = getEditorValue(cell);
    if (currentValue !== (cell.dataset.value ?? '')) {
      updates[cell.dataset.column] = currentValue;
    }
  });

  if (!Object.keys(updates).length) {
    showStatus('Keine Änderungen in dieser Zeile.', 'info');
    if (button) button.disabled = true;
    return;
  }

  if (button) {
    button.disabled = true;
    button.textContent = 'Speichert...';
  }

  try {
    const res = await fetch('/api/db/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ table, rowid, updates }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Speichern fehlgeschlagen');

    await refreshViewerFromServer();
    showStatus(`Zeile ${rowid} gespeichert und Seite aktualisiert.`, 'success');
  } catch (error) {
    showStatus(error.message || String(error), 'error');
  } finally {
    if (button && button.isConnected) {
      button.textContent = 'Speichern';
      updateRowDirtyState(row);
    }
  }
}

bindViewerEditors(document);
