'use strict';
const $ = selector => document.querySelector(selector);
const colors = ['#60a5fa', '#c084fc', '#2dd4bf', '#fb923c', '#facc15', '#f472b6', '#38bdf8', '#a3e635', '#818cf8', '#e879f9', '#f87171'];
const sectorNames = {XLK:'Technology', XLF:'Financials', XLE:'Energy', XLY:'Consumer discretionary', XLP:'Consumer staples', XLV:'Health care', XLI:'Industrials', XLB:'Materials', XLU:'Utilities', XLRE:'Real estate', XLC:'Communication'};
const svg = $('#chart'), legend = $('#legend'), status = $('#status'), tip = $('#tooltip');
const editor = $('#editor');
let config, data, draft, selectedId, saving = false, requestId = 0, retryTimer, managementToken = '';
let summaryText = '', summaryKey = '', summaryBusy = false;
let hidden = new Set(), focusedTicker = null, trailLength = 10, patterns;
try {
  const savedWindow = Number(localStorage.getItem('trailLength'));
  if ([10, 63, 126, 252, 504].includes(savedWindow)) trailLength = savedWindow;
} catch {}
$('#trail-length').value = String(trailLength);
try {
  const saved = JSON.parse(localStorage.getItem('hiddenSectors') || '[]');
  if (Array.isArray(saved)) hidden = new Set(saved.filter(item => typeof item === 'string'));
} catch {}
// API requests follow the public path, including a stripped /sectors prefix.
const base = location.pathname.replace(/\/$/, '');
const activeGroup = value => value?.groups.find(group => group.id === value.active_id);
const universeKey = value => JSON.stringify([activeGroup(value)?.id, activeGroup(value)?.tickers]);
const ns = 'http://www.w3.org/2000/svg';
function el(tag, attrs = {}, text) {
  const node = document.createElementNS(ns, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  svg.append(node);
  return node;
}
function colorFor(ticker) {
  const index = Object.keys(sectorNames).indexOf(ticker);
  if (index >= 0) return colors[index];
  let hash = 0;
  for (const char of ticker) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return `hsl(${hash % 360} 78% 70%)`;
}
const quadrantNames = {Leading:'領先', Improving:'改善', Lagging:'落後', Weakening:'轉弱'};
function quadrant(point) {
  return point.X >= 100 ? (point.Y >= 100 ? 'Leading' : 'Weakening') : (point.Y >= 100 ? 'Improving' : 'Lagging');
}
function description(ticker, point) {
  return `${ticker} · ${point.date} · ${quadrant(point)} ${quadrantNames[quadrant(point)]} · X ${point.X.toFixed(2)} / Y ${point.Y.toFixed(2)}`;
}
function showTip(event, node, text) {
  const rect = node.getBoundingClientRect();
  tip.textContent = text;
  tip.style.display = 'block';
  tip.style.left = Math.max(10, Math.min(event.clientX || rect.left, window.innerWidth - tip.offsetWidth - 10)) + 'px';
  tip.style.top = Math.max(10, Math.min((event.clientY || rect.top) + 18, window.innerHeight - tip.offsetHeight - 10)) + 'px';
}
function tooltip(node, ticker, point) {
  node.setAttribute('aria-label', description(ticker, point));
  const show = event => showTip(event, node, description(ticker, point));
  node.addEventListener('pointermove', show);
  node.addEventListener('focus', show);
  for (const event of ['pointerleave', 'blur']) node.addEventListener(event, () => tip.style.display = 'none');
}
function focusControl(node, ticker) {
  node.setAttribute('role', 'button');
  node.setAttribute('aria-pressed', String(focusedTicker === ticker));
  node.addEventListener('click', () => selectTicker(ticker));
  node.addEventListener('keydown', event => {
    if (['Enter', ' '].includes(event.key)) {event.preventDefault(); selectTicker(ticker);}
  });
}
function selectTicker(ticker) {
  focusedTicker = focusedTicker === ticker ? null : ticker;
  if (focusedTicker) hidden.delete(focusedTicker);
  saveVisibility(); buildLegend(); draw();
  // Preserve keyboard navigation when the SVG controls are rebuilt.
  const target = [...svg.querySelectorAll('.ticker-label')].find(node => node.dataset.ticker === ticker);
  target?.focus({preventScroll:true});
}
$('#clear-focus').onclick = () => {focusedTicker = null; buildLegend(); draw();};
svg.addEventListener('keydown', event => {
  if (event.key === 'Escape') {focusedTicker = null; buildLegend(); draw();}
});
function draw() {
  svg.replaceChildren();
  tip.style.display = 'none';
  if (focusedTicker && (hidden.has(focusedTicker) || !data?.trails[focusedTicker])) focusedTicker = null;
  legend.querySelectorAll('.legend-focus').forEach(button => {
    const selected = button.dataset.ticker === focusedTicker;
    button.setAttribute('aria-pressed', String(selected));
    button.parentElement.classList.toggle('selected', selected);
  });
  $('#clear-focus').disabled = !focusedTicker;
  $('#focus-status').textContent = focusedTicker ? `${focusedTicker} highlighted · ${data.trails[focusedTicker].length}/${trailLength} sessions · ${description(focusedTicker, data.trails[focusedTicker].at(-1))}` : `All visible trails · ${trailLength} sessions requested`;
  document.querySelectorAll('#pattern-rows tr').forEach(row => row.classList.toggle('selected', row.dataset.ticker === focusedTicker));
  const labels = Object.entries(data?.trails || {}).filter(([ticker]) => !hidden.has(ticker)).map(([ticker, trail]) => ({ticker, trail, point: trail.at(-1), color: colorFor(ticker)}));
  const sideCount = Math.max(...['left', 'right'].map(side => labels.filter(label => (label.point.X < 100 ? 'left' : 'right') === side).length));
  // Extra tickers grow the SVG instead of compressing labels into overlapping rows.
  const height = Math.max(740, 180 + sideCount * 26), top = 90, bottom = height - 90;
  const left = 170, right = 1030, midY = (top + bottom) / 2;
  const px = value => left + (value - 96) / 8 * (right - left);
  const py = value => bottom - (value - 96) / 8 * (bottom - top);
  svg.setAttribute('viewBox', `0 0 1200 ${height}`);
  for (const [x, y, color] of [[left,top,'#183b36'], [600,top,'#173843'], [left,midY,'#342b38'], [600,midY,'#352f24']]) {
    el('rect', {x, y, width:430, height:(bottom-top)/2, fill:color, opacity:.32});
  }
  for (let value = 96; value <= 104; value += 2) {
    const stroke = value === 100 ? '#64748b' : '#29364a';
    el('line', {x1:px(value), x2:px(value), y1:top, y2:bottom, stroke});
    el('line', {x1:left, x2:right, y1:py(value), y2:py(value), stroke});
    el('text', {x:px(value), y:bottom+23, fill:'#94a3b8', 'text-anchor':'middle', 'font-size':12}, value);
    el('text', {x:left-10, y:py(value)+4, fill:'#94a3b8', 'text-anchor':'end', 'font-size':12}, value);
  }
  for (const [x,y,text,color] of [[left,42,'02 IMPROVING 改善','#2dd4bf'], [right,42,'01 LEADING 領先','#38bdf8'], [left,height-22,'03 LAGGING 落後','#c084fc'], [right,height-22,'04 WEAKENING 轉弱','#facc15']]) {
    el('text', {x,y,fill:color,'font-size':15,'font-weight':600,'text-anchor':x===left?'start':'end',class:'quadrant-title'}, text);
  }
  if (!labels.length) {
    const message = !config ? 'Loading groups…' : !activeGroup(config).tickers.length ? 'This group is empty. Add tickers in Manage groups.' : !data ? 'Waiting for adjusted closing prices…' : !Object.keys(data.trails).length ? 'No eligible price history. See unavailable tickers below.' : 'All tickers hidden. Use the legend to show them.';
    el('text', {x:600,y:midY-16,fill:'#b2c0d2','font-size':16,'text-anchor':'middle'}, message);
  }
  // Paint the focused trail last. Age-banded paths keep two years x 50 tickers lightweight.
  labels.sort((a,b) => Number(a.ticker === focusedTicker) - Number(b.ticker === focusedTicker));
  for (const label of labels) {
    const {ticker, trail, point, color} = label;
    const selected = ticker === focusedTicker, dim = focusedTicker && !selected ? .12 : 1;
    const layer = el('g', {class:'trail-layer', 'data-ticker':ticker, opacity:dim});
    const pathFor = points => points.map((p,i) => `${i?'L':'M'}${px(p.X)},${py(p.Y)}`).join(' ');
    const add = (tag, attrs) => {const node = el(tag, attrs); layer.append(node); return node;};
    const segments = trail.length - 1, bands = Math.min(8, segments);
    for (let band = 0; band < bands; band++) {
      const from = Math.floor(band * segments / bands), to = Math.floor((band + 1) * segments / bands);
      const age = (band + 1) / bands;
      add('path', {d:pathFor(trail.slice(from, to + 1)), fill:'none', stroke:color,
        'stroke-width':selected?3:1.8, opacity:.12 + .78 * age, 'stroke-linecap':'round', 'stroke-linejoin':'round', 'pointer-events':'none'});
    }
    const hit = add('path', {d:pathFor(trail), fill:'none', stroke:'transparent', 'stroke-width':14,
      class:'trail-hit', 'data-ticker':ticker, 'pointer-events':'stroke'});
    hit.addEventListener('click', () => selectTicker(ticker));
    hit.addEventListener('pointermove', event => {
      const cursor = new DOMPoint(event.clientX, event.clientY).matrixTransform(svg.getScreenCTM().inverse());
      const closest = trail.reduce((best,p) => {
        const distance = (px(p.X)-cursor.x)**2 + (py(p.Y)-cursor.y)**2;
        return distance < best.distance ? {point:p,distance} : best;
      }, {point:point,distance:Infinity});
      showTip(event, hit, description(ticker, closest.point));
    });
    hit.addEventListener('pointerleave', () => tip.style.display = 'none');
    const first = trail[0];
    add('circle', {cx:px(first.X), cy:py(first.Y), r:3, fill:'none', stroke:color, opacity:.4, 'pointer-events':'none'});
    // Small arrow at the latest non-identical step makes travel direction visible.
    const prior = trail.slice(0,-1).reverse().find(p => Math.hypot(px(point.X)-px(p.X), py(point.Y)-py(p.Y)) > 10);
    if (prior) {
      const angle = Math.atan2(py(point.Y)-py(prior.Y), px(point.X)-px(prior.X));
      const x = px(point.X)-14*Math.cos(angle), y = py(point.Y)-14*Math.sin(angle);
      add('path', {d:`M ${x-5*Math.cos(angle-.65)} ${y-5*Math.sin(angle-.65)} L ${x} ${y} L ${x-5*Math.cos(angle+.65)} ${y-5*Math.sin(angle+.65)}`,
        fill:'none', stroke:color, 'stroke-width':2, 'pointer-events':'none'});
    }
    const circle = add('circle', {cx:px(point.X),cy:py(point.Y),r:selected?9:6,fill:color,stroke:'#10151f','stroke-width':2,
      tabindex:0,class:'current-point','data-ticker':ticker});
    tooltip(circle, ticker, point); focusControl(circle, ticker);
    Object.assign(label, {x:px(point.X), y:py(point.Y), side:point.X<100?'left':'right'});
  }
  for (const side of ['left', 'right']) {
    const group = labels.filter(label => label.side === side).sort((a,b) => a.y-b.y || a.ticker.localeCompare(b.ticker));
    group.forEach((label,index) => label.ly = Math.max(label.y, index?group[index-1].ly+26:top+8));
    if (group.length && group.at(-1).ly > bottom-8) {
      group.at(-1).ly = bottom-8;
      for (let index = group.length-2; index >= 0; index--) group[index].ly = Math.min(group[index].ly, group[index+1].ly-26);
    }
    for (const label of group) {
      const rail = side === 'left' ? 144 : 1056, end = side === 'left' ? 136 : 1064;
      el('polyline', {points:`${label.x},${label.y} ${rail},${label.ly} ${end},${label.ly}`,fill:'none',stroke:label.color,'stroke-width':1,opacity:focusedTicker && label.ticker !== focusedTicker ? .12 : .5, 'pointer-events':'none'});
      const text = el('text', {x:side==='left'?130:1070,y:label.ly+5,fill:label.color,'font-size':13,'font-weight':700,'text-anchor':side==='left'?'end':'start',tabindex:0,class:'ticker-label','data-ticker':label.ticker}, label.ticker);
      tooltip(text, label.ticker, label.point); focusControl(text, label.ticker);
      text.style.opacity = focusedTicker && label.ticker !== focusedTicker ? .4 : 1;
    }
  }
}
function saveVisibility() {
  try {localStorage.setItem('hiddenSectors', JSON.stringify([...hidden]));} catch {}
  const tickers = activeGroup(config)?.tickers || [];
  $('#toggle').disabled = !tickers.length;
  $('#toggle').textContent = tickers.length && tickers.every(ticker => hidden.has(ticker)) ? 'Show all' : 'Hide all';
}
function buildLegend() {
  legend.replaceChildren();
  for (const ticker of activeGroup(config)?.tickers || []) {
    const label = document.createElement('div');
    label.className = 'legend-item';
    label.classList.toggle('selected', focusedTicker === ticker);
    label.style.setProperty('--c', colorFor(ticker));
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !hidden.has(ticker);
    box.setAttribute('aria-label', `Show ${ticker}`);
    box.addEventListener('change', () => {
      box.checked?hidden.delete(ticker):hidden.add(ticker);
      if (hidden.has(focusedTicker)) focusedTicker = null;
      saveVisibility(); buildLegend(); draw();
    });
    const button = document.createElement('button');
    button.className = 'legend-focus';
    button.dataset.ticker = ticker;
    button.textContent = ticker + (sectorNames[ticker] ? ` · ${sectorNames[ticker]}` : '');
    button.setAttribute('aria-label', `Highlight ${ticker}`);
    button.setAttribute('aria-pressed', String(focusedTicker === ticker));
    button.disabled = !data?.trails[ticker];
    button.onclick = () => selectTicker(ticker);
    const dot = document.createElement('span');
    dot.className = 'swatch';
    label.append(box, dot, button);
    if (data?.excluded?.[ticker]) {
      button.textContent += ' · unavailable';
      label.title = data.excluded[ticker];
    }
    legend.append(label);
  }
  saveVisibility();
}
$('#toggle').onclick = () => {
  const tickers = activeGroup(config)?.tickers || [];
  const show = tickers.every(ticker => hidden.has(ticker));
  for (const ticker of tickers) show?hidden.delete(ticker):hidden.add(ticker);
  buildLegend(); draw();
};
function applyConfig(value) {
  if (universeKey(config) !== universeKey(value)) {data = undefined; focusedTicker = null; clearSummary(); clearPatterns();}
  config = value;
  const select = $('#active-group');
  select.replaceChildren(...config.groups.map(group => new Option(group.name, group.id)));
  select.value = config.active_id;
  select.disabled = saving;
  $('#manage').disabled = saving;
  $('#refresh').disabled = saving;
  $('#group-count').textContent = `${activeGroup(config).tickers.length} tickers`;
  document.querySelectorAll('.token-field').forEach(field => field.hidden = !config.auth_required);
  buildLegend(); draw();
  updateTemplateControls();
  $('#generate-summary').disabled = !data || saving || summaryBusy;
}
async function api(path, options = {}) {
  const headers = {...(options.body?{'Content-Type':'application/json'}:{}), ...(options.method?{'Authorization':`Bearer ${managementToken}`}:{})};
  const response = await fetch(base + path, {...options, headers, cache:'no-store', signal:AbortSignal.timeout(30000)});
  if (response.status === 204) return null;
  const body = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map(item => item.msg).join('; ') : body.detail;
    const error = new Error(detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}
function retrySoon(delay = 4000) {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(poll, delay);
}
async function poll() {
  if (saving) return;
  clearTimeout(retryTimer);
  const id = ++requestId;
  try {
    const nextConfig = await api('/api/groups');
    if (id !== requestId) return;
    applyConfig(nextConfig);
    const nextData = await api(`/api/rotation?trail_length=${trailLength}`);
    if (id !== requestId) return;
    if (nextData.config_revision !== config.revision) {retrySoon(300); return;}
    data = nextData;
    if (summaryKey && summaryKey !== mapKey()) clearSummary();
    $('#generate-summary').disabled = summaryBusy;
    status.textContent = data.as_of ? `As of ${data.as_of} · ${data.source}${data.stale?` · ${data.warning}; showing last successful data`:''}${data.refreshing?' · Refreshing…':''}` : `${activeGroup(config).name} is empty. Add tickers in Manage groups.`;
    const excluded = Object.entries(data.excluded || {});
    $('#excluded').replaceChildren(...excluded.map(([ticker, reason]) => {
      const item = document.createElement('li'); item.textContent = `${ticker}: ${reason}`; return item;
    }));
    $('#excluded-details').hidden = !excluded.length;
    buildLegend(); draw();
    await Promise.all([loadPatterns(id), refreshTemplates()]);
    if (data.refreshing) retrySoon();
  } catch (error) {
    if (id !== requestId) return;
    status.textContent = `${error.message}${data?' · Showing previously loaded data.':''}`;
    retrySoon(error.status === 503 ? 5000 : 15000);
  }
}
async function persist(value) {
  if (saving) throw new Error('Wait for the current save to finish.');
  saving = true;
  ++requestId; // Invalidate any poll that began before this mutation.
  clearTimeout(retryTimer);
  $('#active-group').disabled = $('#manage').disabled = $('#refresh').disabled = true;
  $('#generate-summary').disabled = true;
  updateTemplateControls();
  try {
    const saved = await api('/api/groups', {method:'PUT', body:JSON.stringify({revision:value.revision,active_id:value.active_id,groups:value.groups})});
    applyConfig(saved);
    return saved;
  } finally {
    saving = false;
    $('#active-group').disabled = $('#manage').disabled = $('#refresh').disabled = !config;
    updateTemplateControls();
  }
}
$('#active-group').onchange = async event => {
  if (!config) return;
  const next = structuredClone(config);
  next.active_id = event.target.value;
  try {
    await persist(next);
    $('#action-status').textContent = 'Active group saved. Refreshing prices…';
  } catch (error) {
    $('#action-status').textContent = error.message;
    event.target.value = config.active_id;
  }
  poll();
};
$('#refresh').onclick = async () => {
  $('#refresh').disabled = true;
  try {
    await api('/api/refresh', {method:'POST'});
    $('#action-status').textContent = 'Price refresh queued.';
    retrySoon(300);
  } catch (error) {$('#action-status').textContent = error.message;}
  finally {$('#refresh').disabled = false;}
};
for (const input of [$('#token'), $('#editor-token')]) input.oninput = event => {
  managementToken = event.target.value.trim();
  for (const other of [$('#token'), $('#editor-token')]) if (other !== input) other.value = managementToken;
};
function selectedGroup() {return draft.groups.find(group => group.id === selectedId);}
function editorMessage(message = '') {$('#editor-status').textContent = message;}
function renderEditor() {
  const list = $('#group-list');
  list.replaceChildren();
  for (const group of draft.groups) {
    const button = document.createElement('button');
    button.type = 'button';
    button.classList.toggle('selected', group.id === selectedId);
    button.setAttribute('aria-pressed', group.id === selectedId);
    const detail = document.createElement('small');
    detail.textContent = `${group.tickers.length} tickers${group.id === draft.active_id?' · Active':''}`;
    button.append(document.createTextNode(group.name), detail);
    button.onclick = () => {selectedId = group.id; renderEditor(); editorMessage();};
    list.append(button);
  }
  const group = selectedGroup();
  $('#group-name').value = group.name;
  $('#use-group').disabled = group.id === draft.active_id;
  $('#use-group').textContent = group.id === draft.active_id ? 'Active on chart' : 'Use on chart';
  $('#delete-group').disabled = draft.groups.length === 1;
  $('#ticker-count').textContent = `${group.tickers.length} / 50`;
  $('#ticker-list').replaceChildren();
  if (!group.tickers.length) {
    const empty = document.createElement('p');
    empty.textContent = 'No tickers yet. Add your first stock or ETF below.';
    $('#ticker-list').append(empty);
  }
  for (const ticker of group.tickers) {
    const chip = document.createElement('span');
    chip.className = 'ticker-chip';
    const remove = document.createElement('button');
    remove.textContent = '×';
    remove.setAttribute('aria-label', `Remove ${ticker}`);
    remove.onclick = () => {
      group.tickers = group.tickers.filter(value => value !== ticker);
      renderEditor();
      editorMessage(`${ticker} removed. Save to apply.`);
      ($('#ticker-list button') || $('#new-tickers')).focus();
    };
    chip.append(document.createTextNode(ticker), remove);
    $('#ticker-list').append(chip);
  }
}
$('#manage').onclick = () => {
  draft = structuredClone(config);
  selectedId = draft.active_id;
  $('#new-group').value = $('#new-tickers').value = '';
  editorMessage(); renderEditor(); editor.showModal();
};
for (const selector of ['#close-editor', '#cancel-editor']) $(selector).onclick = () => {if (!saving) editor.close();};
editor.addEventListener('cancel', event => {if (saving) event.preventDefault();});
$('#new-group-form').onsubmit = event => {
  event.preventDefault();
  const name = $('#new-group').value.trim();
  if (!name) return editorMessage('Enter a group name.');
  if (draft.groups.length >= 20) return editorMessage('You can save up to 20 groups.');
  if (draft.groups.some(group => group.name.toLowerCase() === name.toLowerCase())) return editorMessage('Choose a unique group name.');
  const id = `g-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,10)}`;
  draft.groups.push({id, name, tickers:[]});
  selectedId = id;
  $('#new-group').value = '';
  renderEditor(); editorMessage('Group added. Save to apply.'); $('#new-tickers').focus();
};
$('#rename-form').onsubmit = event => {
  event.preventDefault();
  const name = $('#group-name').value.trim();
  if (!name) return editorMessage('Enter a group name.');
  if (draft.groups.some(group => group.id !== selectedId && group.name.toLowerCase() === name.toLowerCase())) return editorMessage('Choose a unique group name.');
  selectedGroup().name = name;
  renderEditor(); editorMessage('Group renamed. Save to apply.');
};
$('#use-group').onclick = () => {draft.active_id = selectedId; renderEditor(); editorMessage('Active group changed. Save to apply.');};
$('#delete-group').onclick = () => {
  if (draft.groups.length === 1) return;
  draft.groups = draft.groups.filter(group => group.id !== selectedId);
  if (draft.active_id === selectedId) draft.active_id = draft.groups[0].id;
  selectedId = draft.active_id;
  renderEditor(); editorMessage('Group deleted from this draft. Save to apply, or Cancel to discard.');
};
$('#add-tickers-form').onsubmit = event => {
  event.preventDefault();
  const values = $('#new-tickers').value.toUpperCase().split(/[\s,]+/).filter(Boolean);
  if (!values.length) return editorMessage('Enter at least one ticker.');
  if (values.some(value => !/^[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,4})?$/.test(value))) return editorMessage('Use US ticker symbols, e.g. MAGS, AIPO, DRAM, AAPL or BRK-B.');
  const next = [...new Set([...selectedGroup().tickers, ...values])];
  if (next.length > 50) return editorMessage('A group can contain up to 50 tickers.');
  const added = next.length - selectedGroup().tickers.length;
  selectedGroup().tickers = next;
  $('#new-tickers').value = '';
  renderEditor(); editorMessage(added ? `${added} ticker${added===1?'':'s'} added. Save to apply.` : 'Those tickers are already in this group.');
};
$('#reload-groups').onclick = async () => {
  try {
    const latest = await api('/api/groups');
    draft = structuredClone(latest);
    selectedId = draft.active_id;
    renderEditor(); editorMessage('Loaded saved groups; unsaved edits discarded.');
  } catch (error) {editorMessage(error.message);}
};
$('#save-groups').onclick = async () => {
  // Include a name still being edited even if Rename was not pressed.
  selectedGroup().name = $('#group-name').value.trim();
  const controls = [...editor.querySelectorAll('button,input')];
  const disabled = controls.map(control => control.disabled);
  controls.forEach(control => control.disabled = true);
  try {
    await persist(draft);
    editor.close();
    $('#action-status').textContent = 'Groups saved. Refreshing the active group…';
  } catch (error) {editorMessage(error.message);}
  finally {
    controls.forEach((control,index) => control.disabled = disabled[index]);
    poll();
  }
};
function mapKey() {return JSON.stringify([data?.snapshot_id, data?.group, data?.as_of, data?.trails, data?.excluded, data?.stale, data?.config_revision]);}
function clearSummary() {
  summaryText = summaryKey = '';
  $('#summary-text').textContent = '';
  $('#summary-text').hidden = true;
  $('#copy-summary').disabled = true;
  $('#summary-status').textContent = 'Generate a short Cantonese brief from the current map.';
}

$('#generate-summary').onclick = async () => {
  summaryBusy = true;
  $('#generate-summary').disabled = true;
  $('#summary-status').textContent = 'Generating…';
  const key = mapKey(), revision = config.revision;
  try {
    const result = await api(`/api/summary?trail_length=${trailLength}`);
    if (key !== mapKey() || result.config_revision !== revision || result.snapshot_id !== data?.snapshot_id) {
      clearSummary();
      $('#summary-status').textContent = 'The map changed. Generate again after it finishes updating.';
      return;
    }
    summaryText = result.text; summaryKey = key;
    $('#summary-text').textContent = summaryText;
    $('#summary-text').hidden = false;
    $('#copy-summary').disabled = false;
    $('#summary-status').textContent = `Ready to paste into your Macro brief · ${result.as_of || 'Empty group'}`;
  } catch (error) {$('#summary-status').textContent = error.message;}
  finally {summaryBusy = false; $('#generate-summary').disabled = !data || saving;}
};
$('#copy-summary').onclick = async () => {
  try {
    await navigator.clipboard.writeText(summaryText);
    $('#summary-status').textContent = 'Markdown copied.';
  } catch {
    const selection = window.getSelection(), range = document.createRange();
    range.selectNodeContents($('#summary-text')); selection.removeAllRanges(); selection.addRange(range);
    $('#summary-status').textContent = 'Text selected. Press Ctrl+C / ⌘C to copy.';
  }
};
function updateTemplateControls() {
  const selected = Boolean($('#template-select').value);
  $('#template-select').disabled = saving;
  for (const id of ['load-template', 'overwrite-template', 'delete-template']) $(`#${id}`).disabled = !selected || saving;
  $('#save-template').disabled = !config || saving;
}
async function refreshTemplates() {
  const result = await api('/api/templates'), select = $('#template-select'), previous = select.value;
  select.replaceChildren(new Option(result.templates.length ? 'Choose a template' : 'No saved templates', ''),
    ...result.templates.map(template => new Option(template.name, template.id)));
  if (result.templates.some(template => template.id === previous)) select.value = previous;
  updateTemplateControls();
}
$('#template-select').onchange = updateTemplateControls;
async function templateAction(path, method, body, load = false) {
  if (saving) return;
  saving = true; ++requestId; clearTimeout(retryTimer);
  $('#active-group').disabled = $('#manage').disabled = $('#refresh').disabled = true;
  $('#generate-summary').disabled = true;
  updateTemplateControls();
  try {
    const result = await api(path, {method, ...(body ? {body:JSON.stringify(body)} : {})});
    if (load) {applyConfig(result); clearSummary();}
    await refreshTemplates();
    $('#action-status').textContent = load ? 'Template loaded. All groups and the active selection restored.' : method === 'DELETE' ? 'Template deleted.' : method === 'PUT' ? 'Template overwritten with the current setup.' : 'Template saved.';
    return result;
  } catch (error) {$('#action-status').textContent = error.message;}
  finally {
    saving = false;
    $('#active-group').disabled = $('#manage').disabled = $('#refresh').disabled = !config;
    $('#generate-summary').disabled = !data || summaryBusy;
    updateTemplateControls(); poll();
  }
}
$('#save-template-form').onsubmit = async event => {
  event.preventDefault();
  const result = await templateAction('/api/templates', 'POST', {name:$('#template-name').value.trim()});
  if (result) {$('#template-name').value = ''; $('#template-select').value = result.id; updateTemplateControls();}
};
$('#load-template').onclick = () => templateAction(`/api/templates/${encodeURIComponent($('#template-select').value)}/load`, 'POST', null, true);
$('#overwrite-template').onclick = () => templateAction(`/api/templates/${encodeURIComponent($('#template-select').value)}`, 'PUT');
$('#delete-template').onclick = async () => {
  await templateAction(`/api/templates/${encodeURIComponent($('#template-select').value)}`, 'DELETE');
};
function clearPatterns() {
  patterns = undefined;
  $('#copy-patterns').disabled = true;
  $('#patterns-text').textContent = '';
  $('#pattern-rows').replaceChildren();
  $('.pattern-table-wrap').hidden = true;
  $('#patterns-status').textContent = 'Waiting for the current map…';
}
const directionLabels = {clockwise:'↻ Clockwise', counterclockwise:'↺ Counterclockwise', mixed:'Mixed direction', no_clear_rotation:'No clear rotation'};
const shapeLabels = {expanding:'Expanding', tightening:'Tightening', steady:'Steady', insufficient_history:'Too few points'};
async function loadPatterns(id) {
  if (patterns?.snapshot_id === data?.snapshot_id) return;
  clearPatterns();
  try {
    const result = await api(`/api/patterns?trail_length=${trailLength}`);
    if (id !== requestId) return;
    if (result.snapshot_id !== data?.snapshot_id) {
      $('#patterns-status').textContent = 'Prices changed while analysing; updating the map…';
      retrySoon(300); return;
    }
    patterns = result;
    const rows = Object.entries(result.patterns);
    $('#patterns-status').textContent = `${trailLength}-session window · ${rows.length} eligible tickers · ${result.as_of || 'Empty group'}${result.stale?' · Last successful data (refresh failed)':''}${rows.some(([,r]) => r.partial)?' · Some trails have shorter history; see coverage below.':''}`;
    for (const [ticker, row] of rows) {
      const tr = document.createElement('tr'); tr.dataset.ticker = ticker;
      tr.classList.toggle('selected', focusedTicker === ticker);
      const first = document.createElement('td'), button = document.createElement('button');
      button.textContent = ticker; button.style.color = colorFor(ticker);
      button.setAttribute('aria-label', `Highlight ${ticker} from patterns`);
      button.onclick = () => selectTicker(ticker);
      const coverage = document.createElement('small');
      coverage.textContent = `${row.sessions}/${row.requested_sessions} sessions${row.partial?' · partial':''}`;
      first.append(button, coverage); tr.append(first);
      const direction = document.createElement('td');
      direction.textContent = directionLabels[row.direction];
      direction.title = `Net ${row.net_turns} turns · ${Math.round(row.coherence * 100)}% directional consistency · ${row.rotation_steps_ignored} steps near centre ignored`;
      tr.append(direction);
      const time = document.createElement('td'); time.className = 'quadrant-times';
      for (const [q, count] of Object.entries(row.quadrant_sessions)) {
        const span = document.createElement('span'); span.className = q.toLowerCase();
        span.textContent = `${q} ${count}`;
        span.title = `${quadrantNames[q]} · ${row.quadrant_percent[q]}% of closing points`;
        time.append(span);
      }
      tr.append(time);
      for (const value of [`${row.axis_crosses.X} / ${row.axis_crosses.Y}`, shapeLabels[row.path_shape]]) {
        const td = document.createElement('td'); td.textContent = value; tr.append(td);
      }
      coverage.title = `${row.start} to ${row.end}`;
      $('#pattern-rows').append(tr);
    }
    $('.pattern-table-wrap').hidden = !rows.length;
    $('#patterns-text').textContent = result.text;
    $('#copy-patterns').disabled = false;
  } catch (error) {
    if (id === requestId) $('#patterns-status').textContent = `Pattern notes unavailable: ${error.message}`;
  }
}
$('#trail-length').onchange = () => {
  trailLength = Number($('#trail-length').value);
  try {localStorage.setItem('trailLength', String(trailLength));} catch {}
  ++requestId; data = undefined; clearSummary(); clearPatterns(); buildLegend(); draw();
  $('#generate-summary').disabled = true;
  status.textContent = `Loading ${trailLength} sessions…`;
  poll();
};
$('#copy-patterns').onclick = async () => {
  try {
    await navigator.clipboard.writeText(patterns.text);
    $('#patterns-status').textContent = 'Pattern Markdown copied.';
  } catch {
    $('.pattern-copy').open = true;
    const selection = window.getSelection(), range = document.createRange();
    range.selectNodeContents($('#patterns-text')); selection.removeAllRanges(); selection.addRange(range);
    $('#patterns-status').textContent = 'Text selected. Press Ctrl+C / ⌘C to copy.';
  }
};
draw(); poll(); setInterval(poll, 60000);
