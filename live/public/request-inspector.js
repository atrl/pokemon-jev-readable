// Read-only projection of recorded request events. Never rebuild model input
// from the current observation, configuration, or prompt source code.
const own = (value, key) => value !== null && typeof value === 'object' && Object.hasOwn(value, key);
const json = (value) => JSON.stringify(value, null, 2);
export const requestEvents = (events) => events.filter((event) => event?.type === 'jev_request');
export function selectRequest(events, { selectedId = null, followLatest = true, pinnedEvent = null } = {}) {
  const requests = requestEvents(events);
  if (followLatest) return requests.at(-1) ?? null;
  return requests.find((event) => event.id === selectedId)
    ?? (pinnedEvent?.id === selectedId ? pinnedEvent : null)
    ?? requests.at(-1) ?? null;
}
export function stateValueType(value) {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'array';
  return typeof value;
}
const childPath = (path, key, isArray = false) => isArray ? `${path}[${key}]`
  : /^[A-Za-z_$][\w$]*$/.test(key) ? `${path}.${key}` : `${path}[${JSON.stringify(key)}]`;
export function flattenState(value, path = '$.state') {
  const rows = [];
  const visit = (current, currentPath, depth) => {
    const type = stateValueType(current);
    rows.push({ path: currentPath, type, value: current, depth });
    if (current !== null && typeof current === 'object') {
      for (const [key, child] of Object.entries(current)) visit(child, childPath(currentPath, key, Array.isArray(current)), depth + 1);
    }
  };
  visit(value, path, 0);
  return rows;
}
function element(tag, className = '', text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = String(text);
  return result;
}
function scalar(value) {
  return value === undefined ? '未提供（missing）' : json(value);
}
function readable(value) {
  return typeof value === 'string' && value !== '' ? value : scalar(value);
}
function fieldValue(row) {
  if (row.type === 'array') return row.value.length ? `[ ${row.value.length} items ]` : '[]';
  if (row.type === 'object') {
    const count = Object.keys(row.value).length;
    return count ? `{ ${count} fields }` : '{}';
  }
  return scalar(row.value);
}
function timeLabel(value) {
  if (value === null || value === undefined) return '时间未提供';
  const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', { hour12: false }) : String(value);
}
function eventLabel(event) {
  return `第 ${event.step ?? '未提供'} 步 · 尝试 ${event.attempt ?? '未提供'} · ${timeLabel(event.time)}`;
}
function block(title, value, className = '') {
  const section = element('section', `input-block ${className}`);
  section.append(element('h4', '', title), element('pre', 'input-text', readable(value)));
  return section;
}

export function createRequestInspector(root) {
  let runId = null;
  let requests = [];
  let selected = null;
  let pinned = null;
  let followLatest = true;
  let rendered = null;
  let flattened = [];
  const $ = (name) => root.querySelector(`[data-input="${name}"]`);
  const picker = $('select');
  const follow = $('follow');
  const filter = $('filter');

  function renderFields() {
    const search = filter.value.trim().toLocaleLowerCase();
    const matching = flattened.filter((row) => !search || `${row.path} ${row.type} ${fieldValue(row)}`.toLocaleLowerCase().includes(search));
    const fragment = document.createDocumentFragment();
    for (const row of matching) {
      const tr = element('tr', `input-field input-type-${row.type}`);
      const path = element('td');
      path.append(element('code', '', row.path));
      tr.append(path, element('td', 'input-type', row.type));
      const value = element('td');
      value.append(element('pre', 'input-field-value', fieldValue(row)));
      tr.append(value);
      fragment.append(tr);
    }
    $('fields').replaceChildren(fragment);
    $('field-count').textContent = `显示 ${matching.length} / ${flattened.length} 项（含容器）`;
    $('field-empty').hidden = matching.length !== 0;
  }
  function renderQuestions(request) {
    const fragment = document.createDocumentFragment();
    const questions = request?.questions;
    if (!questions || typeof questions !== 'object' || !Object.keys(questions).length) {
      fragment.append(block('questions', questions));
    } else {
      for (const [questionId, question] of Object.entries(questions)) {
        const card = element('section', 'input-question');
        card.append(element('h4', 'input-question-title', `问题 ${questionId} · type: ${scalar(question?.type)}`));
        card.append(block('instructions · 完整提示词原文', question?.instructions));
        const criteria = question?.criteria;
        const candidates = element('details', 'input-candidates');
        candidates.open = true;
        candidates.append(element('summary', '', 'criteria · 候选 ID 与完整描述原文'));
        if (criteria !== null && typeof criteria === 'object' && Object.keys(criteria).length) {
          const list = element('dl', 'input-candidate-list');
          for (const [id, description] of Object.entries(criteria)) {
            const pair = element('div');
            pair.append(element('dt', 'mono', id));
            const dd = element('dd');
            dd.append(element('pre', 'input-text', readable(description)));
            pair.append(dd);
            list.append(pair);
          }
          candidates.append(list);
        } else candidates.append(element('pre', 'input-text', readable(criteria)));
        card.append(candidates);
        // Other question schemas remain inspectable without pretending they
        // have the button-choice schema used by the current Pokémon policy.
        const other = Object.fromEntries(Object.entries(question && typeof question === 'object' ? question : {})
          .filter(([key]) => !['type', 'instructions', 'criteria'].includes(key)));
        if (Object.keys(other).length) card.append(block('该问题的其他原始字段', other));
        fragment.append(card);
      }
    }
    $('questions').replaceChildren(fragment);
  }
  function renderState(request) {
    const state = request?.state;
    const groups = element('dl', 'input-groups');
    const keys = [...new Set(['game', 'feedback', 'recent_actions', 'temporal_context',
      ...Object.keys(state !== null && typeof state === 'object' ? state : {}).filter((key) => !['goal', 'current_focus'].includes(key))])];
    for (const key of keys) {
      const pair = element('div');
      pair.append(element('dt', 'mono', key));
      const exists = own(state, key);
      const value = exists ? state[key] : undefined;
      const kind = stateValueType(value);
      const label = !exists ? '未提供（missing）' : kind === 'object' ? `object · ${Object.keys(value).length} 个字段`
        : kind === 'array' ? `array · ${value.length} 项` : `${kind} · ${scalar(value)}`;
      pair.append(element('dd', exists ? '' : 'input-missing', label));
      groups.append(pair);
    }
    $('groups').replaceChildren(groups);
    $('unavailable').replaceChildren(block('state.game.unavailable · 请求声明的信息缺口', state?.game?.unavailable));
    flattened = own(request, 'state') ? flattenState(state) : [];
    renderFields();

    const temporal = state?.temporal_context;
    const transitions = temporal?.transitions;
    const fragment = document.createDocumentFragment();
    $('transition-count').textContent = Array.isArray(transitions) ? `${transitions.length} 组实际记录` : '未提供转换列表';
    if (Array.isArray(transitions) && transitions.length) {
      fragment.append(block('temporal_context · 列表顺序与含义原文', Object.fromEntries(Object.entries(temporal).filter(([key]) => key !== 'transitions'))));
      const cards = element('div', 'input-transitions');
      transitions.forEach((transition, index) => {
        const card = element('article', 'input-transition');
        card.append(element('h4', '', `记录 ${index + 1} · step ${scalar(transition?.step)} · button ${scalar(transition?.button)}`));
        card.append(block('before · 按键前', transition?.before), block('after · 按键后', transition?.after), block('outcome · 已记录效果', transition?.outcome));
        cards.append(card);
      });
      fragment.append(cards);
    } else fragment.append(block('temporal_context · 完整原文', temporal));
    $('transitions').replaceChildren(fragment);
  }
  function renderSelected() {
    $('copy').disabled = !selected || !own(selected, 'request');
    $('download').disabled = !selected || !own(selected, 'request');
    $('empty').hidden = Boolean(selected);
    $('body').hidden = !selected;
    $('status').textContent = '';
    if (!selected) {
      $('source').textContent = '等待本轮真实 jev_request 事件';
      rendered = null;
      return;
    }
    const request = selected.request;
    $('source').textContent = `model: ${scalar(request?.model)} · ${eventLabel(selected)} · event: ${selected.id}`;
    $('goals').replaceChildren(block('state.goal · 总目标原文', request?.state?.goal), block('state.current_focus · 当前重点原文', request?.state?.current_focus));
    renderQuestions(request);
    renderState(request);
    $('raw').textContent = readable(request);
    rendered = selected;
  }
  function renderSelection() {
    const retained = selected && requests.some((event) => event.id === selected.id);
    const options = [...requests].reverse();
    if (selected && !retained) options.unshift(selected);
    const signature = options.map((event) => event.id).join('|');
    if (picker.dataset.signature !== signature) {
      const fragment = document.createDocumentFragment();
      for (const event of options) {
        const option = element('option', '', `${eventLabel(event)}${event === selected && !retained ? ' · 已固定，超出窗口' : ''}`);
        option.value = event.id;
        fragment.append(option);
      }
      if (!options.length) fragment.append(element('option', '', '尚无真实请求'));
      picker.replaceChildren(fragment);
      picker.dataset.signature = signature;
    }
    picker.disabled = options.length === 0;
    if (selected) picker.value = selected.id;
    follow.setAttribute('aria-pressed', String(followLatest));
    follow.textContent = followLatest ? '跟随最新请求 · 点击固定' : '已固定此请求 · 恢复跟随';
    $('video-note').textContent = followLatest
      ? '正在跟随最新请求；视频有传输延迟，请求时间与当前画面不保证逐帧对应。'
      : '已固定阅读这一条历史请求；上方视频保持当前所选运行的播放位置，不会随历史请求回放。';
    $('window').textContent = `历史选择来自最近最多 2,000 条事件窗口，当前窗口含 ${requests.length} 次请求。${selected && !retained ? '此固定请求已超出窗口，当前页面仍保留这一份。' : ''}选择历史请求会固定阅读；较早的本机日志不在此选择器内。`;
    if (rendered !== selected) renderSelected();
  }
  picker.addEventListener('change', () => {
    followLatest = false;
    selected = requests.find((event) => event.id === picker.value) ?? pinned;
    pinned = selected;
    renderSelection();
  });
  follow.addEventListener('click', () => {
    followLatest = !followLatest;
    pinned = followLatest ? null : selected;
    selected = selectRequest(requests, { selectedId: selected?.id, followLatest, pinnedEvent: pinned });
    renderSelection();
  });
  filter.addEventListener('input', renderFields);
  $('copy').addEventListener('click', async () => {
    if (!selected || !own(selected, 'request')) return;
    const value = json(selected.request);
    try {
      await navigator.clipboard.writeText(value);
      $('status').textContent = '已复制所选请求的完整 JSON';
    } catch {
      $('raw-details').open = true;
      const range = document.createRange();
      range.selectNodeContents($('raw'));
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      $('status').textContent = '浏览器未允许直接复制；完整 JSON 已选中，可手动复制。';
    }
  });
  $('download').addEventListener('click', () => {
    if (!selected || !own(selected, 'request')) return;
    const url = URL.createObjectURL(new Blob([json(selected.request)], { type: 'application/json' }));
    const link = element('a');
    link.href = url;
    link.download = `${runId}-step-${selected.step ?? 'unknown'}-attempt-${selected.attempt ?? 'unknown'}.json`.replace(/[^\w.-]/g, '_');
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $('status').textContent = '已下载所选请求的完整 JSON';
  });
  return {
    update(events, run = {}) {
      if (runId !== run.id) {
        runId = run.id;
        selected = null;
        pinned = null;
        followLatest = true;
        rendered = undefined;
        filter.value = '';
      }
      requests = requestEvents(events);
      selected = selectRequest(requests, { selectedId: selected?.id, followLatest, pinnedEvent: pinned });
      if (!followLatest) pinned = selected;
      renderSelection();
    },
  };
}
