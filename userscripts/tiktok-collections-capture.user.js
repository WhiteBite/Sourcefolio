// ==UserScript==
// @name         TikTok Collections Capture
// @namespace    evidence-vault
// @version      2.0.0
// @match        https://www.tiktok.com/*
// @grant        GM_setValue
// @grant        GM_getValue
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const AID = '1988';
  const BATCH_PAUSE = 1000;
  const BATCH_SIZE = 10;
  const MAX_RETRIES = 3;
  const RETRY_BACKOFF = 5000;
  const TIMEOUT = 15000;
  const DEFAULT_DELAY = 300;
  const COMMENT_LIMIT = 20;

  const STORE_SETTINGS = 'ev_tiktok_settings_v2';
  const STORE_BASELINE = 'ev_tiktok_baseline_v2';
  const STORE_PROGRESS = 'ev_tiktok_progress_v2';
  const STORE_UI = 'ev_tiktok_ui_v2';
  const STORE_HISTORY = 'ev_tiktok_history_v2';

  const CSV_HEADER =
    'collection,collection_id,video_id,type,url,author,desc,created_at,plays,likes,comments,shares,images_count';

  let requestCount = 0;
  let running = false;

  const control = { paused: false, stopped: false };

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function gmGet(key, fallback) {
    try {
      const raw = GM_getValue(key, null);
      if (raw === null || raw === undefined) return fallback;
      if (typeof raw === 'string') return JSON.parse(raw);
      return raw;
    } catch {
      return fallback;
    }
  }

  function gmSet(key, value) {
    try {
      GM_setValue(key, JSON.stringify(value));
    } catch {
      return;
    }
  }

  function gmDel(key) {
    try {
      GM_setValue(key, '');
    } catch {
      return;
    }
  }

  const settings = Object.assign({ delay: DEFAULT_DELAY, format: 'json' }, gmGet(STORE_SETTINGS, {}));

  function saveSettings() {
    gmSet(STORE_SETTINGS, settings);
  }

  function findKey(obj, key, depth) {
    if (depth > 12 || obj === null || typeof obj !== 'object') return null;
    if (Array.isArray(obj)) {
      for (const value of obj) {
        const found = findKey(value, key, depth + 1);
        if (found !== null) return found;
      }
      return null;
    }
    if (Object.prototype.hasOwnProperty.call(obj, key)) {
      const value = obj[key];
      if (typeof value === 'string' || typeof value === 'number') return String(value);
    }
    for (const name of Object.keys(obj)) {
      const found = findKey(obj[name], key, depth + 1);
      if (found !== null) return found;
    }
    return null;
  }

  function resolveAccount() {
    const data = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
    let secUid = data ? findKey(data, 'secUid', 0) : null;
    let uniqueId = data ? findKey(data, 'uniqueId', 0) : null;
    let uid = data ? findKey(data, 'uid', 0) : null;
    const html = document.documentElement.innerHTML;
    if (!secUid) {
      const match = html.match(/"secUid":"([^"]+)"/);
      if (match) secUid = match[1];
    }
    if (!uniqueId) {
      const match = html.match(/"uniqueId":"([^"]+)"/);
      if (match) uniqueId = match[1];
    }
    if (!uid) {
      const match = html.match(/"uid":"([^"]+)"/);
      if (match) uid = match[1];
    }
    return { secUid: secUid, uniqueId: uniqueId, uid: uid };
  }

  async function checkpoint() {
    while (control.paused && !control.stopped) await sleep(200);
    if (control.stopped) throw new Error('Остановлено пользователем');
  }

  async function throttle() {
    requestCount += 1;
    if (requestCount % BATCH_SIZE === 0) await sleep(BATCH_PAUSE);
    else await sleep(settings.delay);
  }

  async function apiGet(url) {
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), TIMEOUT);
      try {
        const response = await fetch(url, { credentials: 'include', signal: controller.signal });
        clearTimeout(timer);
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return await response.json();
      } catch (error) {
        clearTimeout(timer);
        if (attempt === MAX_RETRIES) throw error;
        await sleep(RETRY_BACKOFF);
      }
    }
    return null;
  }

  async function fetchCollections(secUid) {
    const collections = [];
    let cursor = 0;
    let hasMore = true;
    while (hasMore) {
      await checkpoint();
      await throttle();
      const url =
        'https://www.tiktok.com/api/user/collection_list/?secUid=' +
        encodeURIComponent(secUid) +
        '&aid=' +
        AID +
        '&count=30&cursor=' +
        cursor;
      const data = await apiGet(url);
      const list = (data && data.collectionList) || [];
      for (const collection of list) collections.push(collection);
      const next = data ? data.cursor : null;
      hasMore = !!(data && data.hasMore) && next !== undefined && next !== null && next !== cursor;
      cursor = next;
    }
    return collections;
  }

  async function fetchItems(collectionId, onProgress) {
    const items = [];
    let cursor = 0;
    let hasMore = true;
    while (hasMore) {
      await checkpoint();
      await throttle();
      const url =
        'https://www.tiktok.com/api/collection/item_list/?collectionId=' +
        encodeURIComponent(collectionId) +
        '&sourceType=113&aid=' +
        AID +
        '&count=30&cursor=' +
        cursor +
        '&device_platform=web_pc&channel=tiktok_web&os=windows&region=US&language=en';
      const data = await apiGet(url);
      if (!data || data.status_code !== 0) break;
      const list = data.itemList || [];
      for (const item of list) items.push(item);
      if (onProgress) onProgress(items.length);
      const next = data.cursor;
      hasMore = !!data.hasMore && next !== undefined && next !== null && next !== cursor;
      cursor = next;
    }
    return items;
  }

  async function fetchComments(videoId) {
    await checkpoint();
    await throttle();
    const url =
      'https://www.tiktok.com/api/comment/list/?aweme_id=' +
      encodeURIComponent(videoId) +
      '&count=' +
      COMMENT_LIMIT +
      '&cursor=0&aid=' +
      AID;
    const data = await apiGet(url);
    const list = (data && data.comments) || [];
    const comments = [];
    for (const comment of list.slice(0, COMMENT_LIMIT)) {
      comments.push({
        id: String(comment.cid !== undefined ? comment.cid : comment.id),
        text: comment.text || '',
        author: comment.user && comment.user.unique_id ? comment.user.unique_id : '',
        likes: toNumber(comment.digg_count),
        created_at: toIso(comment.create_time),
      });
    }
    return comments;
  }

  function toNumber(value) {
    if (value === null || value === undefined) return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function toIso(seconds) {
    const number = toNumber(seconds);
    if (number === null) return null;
    return new Date(number * 1000).toISOString();
  }

  function mapItem(item) {
    const videoId = String(item.id);
    const author = item.author && item.author.uniqueId ? item.author.uniqueId : '';
    const isSlideshow = !!item.imagePost;
    const type = isSlideshow ? 'slideshow' : 'video';
    const url = 'https://www.tiktok.com/@' + author + (isSlideshow ? '/photo/' : '/video/') + videoId;
    const images = [];
    if (isSlideshow && item.imagePost && Array.isArray(item.imagePost.images)) {
      for (const image of item.imagePost.images) {
        const list = image && image.imageURL && Array.isArray(image.imageURL.urlList) ? image.imageURL.urlList : [];
        if (list.length > 0) images.push(list[0]);
      }
    }
    let cover = null;
    if (!isSlideshow) {
      if (item.video && item.video.playAddr) cover = item.video.playAddr;
      else if (item.video && item.video.cover) cover = item.video.cover;
      else if (item.cover) cover = item.cover;
    }
    const stats = item.stats || {};
    return {
      video_id: videoId,
      type: type,
      url: url,
      author: author,
      desc: item.desc || '',
      created_at: toIso(item.createTime),
      stats: {
        plays: toNumber(stats.playCount),
        likes: toNumber(stats.diggCount),
        comments: toNumber(stats.commentCount),
        shares: toNumber(stats.shareCount),
      },
      images: images,
      cover: cover,
    };
  }

  const uiState = Object.assign({ expanded: false }, gmGet(STORE_UI, {}));

  const style = document.createElement('style');
  style.textContent = '@keyframes evPulse{0%,100%{opacity:1}50%{opacity:.2}}';
  document.head.appendChild(style);

  const root = document.createElement('div');
  root.style.cssText =
    'position:fixed;right:16px;bottom:16px;z-index:2147483647;' +
    'font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;';

  const pill = document.createElement('button');
  pill.type = 'button';
  pill.style.cssText =
    'display:flex;align-items:center;gap:8px;background:#fe2c55;color:#fff;border:0;' +
    'border-radius:999px;padding:10px 16px;font:inherit;font-weight:700;cursor:pointer;' +
    'box-shadow:0 4px 16px rgba(0,0,0,.35);';
  const pillDot = document.createElement('span');
  pillDot.style.cssText =
    'width:8px;height:8px;border-radius:50%;background:#25f4ee;display:none;animation:evPulse 1s infinite;';
  const pillLabel = document.createElement('span');
  pillLabel.textContent = 'TikTok сбор';
  pill.appendChild(pillDot);
  pill.appendChild(pillLabel);

  const panel = document.createElement('div');
  panel.style.cssText =
    'background:#161823;color:#fff;padding:12px;border-radius:12px;' +
    'box-shadow:0 8px 28px rgba(0,0,0,.45);width:340px;max-width:calc(100vw - 32px);' +
    'max-height:80vh;overflow-y:auto;box-sizing:border-box;';

  function makeButton(text, background) {
    const element = document.createElement('button');
    element.type = 'button';
    element.textContent = text;
    element.style.cssText =
      'background:' +
      background +
      ';color:#fff;border:0;border-radius:8px;padding:8px 12px;font:inherit;font-weight:600;cursor:pointer;';
    return element;
  }

  function makeRow(children) {
    const element = document.createElement('div');
    element.style.cssText = 'display:flex;gap:6px;align-items:center;margin-top:8px;flex-wrap:wrap;';
    for (const child of children) element.appendChild(child);
    return element;
  }

  function makeField(labelText, controlElement) {
    const wrap = document.createElement('label');
    wrap.style.cssText = 'display:flex;align-items:center;gap:6px;';
    const span = document.createElement('span');
    span.textContent = labelText;
    span.style.cssText = 'color:#c9c9d4;font-size:12px;';
    wrap.appendChild(span);
    wrap.appendChild(controlElement);
    return wrap;
  }

  const header = document.createElement('div');
  header.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:8px;';
  const title = document.createElement('div');
  title.textContent = 'TikTok сбор';
  title.style.cssText = 'font-size:15px;font-weight:700;';
  const collapseButton = document.createElement('button');
  collapseButton.type = 'button';
  collapseButton.textContent = '×';
  collapseButton.title = 'Свернуть';
  collapseButton.style.cssText =
    'background:transparent;color:#fff;border:0;font-size:20px;line-height:1;cursor:pointer;padding:0 4px;';
  header.appendChild(title);
  header.appendChild(collapseButton);

  const formatSelect = document.createElement('select');
  formatSelect.style.cssText = 'background:#2a2b3d;color:#fff;border:0;border-radius:6px;padding:5px 8px;font:inherit;';
  for (const value of ['json', 'csv']) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value.toUpperCase();
    formatSelect.appendChild(option);
  }
  formatSelect.value = settings.format;

  const delayInput = document.createElement('input');
  delayInput.type = 'number';
  delayInput.min = '0';
  delayInput.step = '50';
  delayInput.value = String(settings.delay);
  delayInput.style.cssText =
    'width:70px;background:#2a2b3d;color:#fff;border:0;border-radius:6px;padding:5px 8px;font:inherit;';

  const fullButton = makeButton('Собрать', '#fe2c55');
  fullButton.style.flex = '1';
  fullButton.style.padding = '10px 12px';
  fullButton.style.fontSize = '14px';
  const newButton = makeButton('Только новое', '#25f4ee');
  newButton.style.color = '#161823';
  newButton.style.flex = '1';
  newButton.style.padding = '10px 12px';
  newButton.style.fontSize = '14px';
  const pauseButton = makeButton('Пауза', '#3a3b4d');
  const resumeButton = makeButton('Продолжить', '#3a3b4d');
  const stopButton = makeButton('Стоп', '#3a3b4d');
  const refreshButton = makeButton('Обновить список', '#3a3b4d');

  const modeHint = document.createElement('div');
  modeHint.textContent = 'Собрать — первый полный слепок. Только новое — лишь добавленное с прошлого раза.';
  modeHint.style.cssText = 'margin-top:6px;color:#8a8b9c;font-size:12px;line-height:1.35;';

  const commentsHeader = document.createElement('button');
  commentsHeader.type = 'button';
  commentsHeader.style.cssText =
    'display:flex;align-items:center;gap:6px;width:100%;background:transparent;color:#fff;' +
    'border:0;padding:8px 0;font:inherit;font-weight:600;cursor:pointer;text-align:left;';
  const commentsArrow = document.createElement('span');
  commentsArrow.textContent = '▸';
  const commentsTitle = document.createElement('span');
  commentsTitle.textContent = 'Комментарии (топ-20)';
  commentsHeader.appendChild(commentsArrow);
  commentsHeader.appendChild(commentsTitle);

  const commentsBody = document.createElement('div');
  commentsBody.style.cssText = 'display:none;';

  const commentsCheck = document.createElement('input');
  commentsCheck.type = 'checkbox';
  commentsCheck.checked = false;
  const commentsLabel = document.createElement('label');
  commentsLabel.style.cssText = 'display:flex;gap:6px;align-items:center;cursor:pointer;';
  commentsLabel.appendChild(commentsCheck);
  commentsLabel.appendChild(document.createTextNode('Собирать комментарии'));

  const commentsWarning = document.createElement('div');
  commentsWarning.textContent = 'Сбор комментариев медленный и тяжёлый — по одному запросу на видео.';
  commentsWarning.style.cssText = 'margin-top:6px;color:#e0a458;font-size:12px;line-height:1.35;display:none;';

  const collectionSelect = document.createElement('select');
  collectionSelect.multiple = true;
  collectionSelect.size = 4;
  collectionSelect.style.cssText =
    'width:100%;background:#2a2b3d;color:#fff;border:0;border-radius:6px;padding:5px 8px;' +
    'font:inherit;margin-top:8px;box-sizing:border-box;';

  const collectionHint = document.createElement('div');
  collectionHint.textContent = 'выбери коллекции, где нужны комментарии';
  collectionHint.style.cssText = 'margin-top:6px;color:#8a8b9c;font-size:12px;';

  const collectionEmptyHint = document.createElement('div');
  collectionEmptyHint.textContent = 'Список пуст — нажми «Обновить список».';
  collectionEmptyHint.style.cssText = 'margin-top:6px;color:#8a8b9c;font-size:12px;display:none;';

  commentsBody.appendChild(commentsLabel);
  commentsBody.appendChild(commentsWarning);
  commentsBody.appendChild(makeRow([refreshButton]));
  commentsBody.appendChild(collectionSelect);
  commentsBody.appendChild(collectionHint);
  commentsBody.appendChild(collectionEmptyHint);

  const status = document.createElement('div');
  status.textContent = 'Готово';
  status.style.cssText = 'margin-top:10px;white-space:pre-wrap;word-break:break-word;font-size:12px;color:#e6e6ee;';

  const summary = document.createElement('div');
  summary.style.cssText = 'margin-top:6px;white-space:pre-wrap;word-break:break-word;font-size:12px;color:#9ad0ff;';

  const history = document.createElement('div');
  history.style.cssText =
    'margin-top:8px;padding-top:8px;border-top:1px solid #2a2b3d;font-size:12px;color:#8a8b9c;';

  panel.appendChild(header);
  panel.appendChild(makeRow([makeField('Формат', formatSelect), makeField('Пауза, мс', delayInput)]));
  panel.appendChild(makeRow([fullButton, newButton]));
  panel.appendChild(modeHint);
  panel.appendChild(makeRow([pauseButton, resumeButton, stopButton]));
  panel.appendChild(commentsHeader);
  panel.appendChild(commentsBody);
  panel.appendChild(status);
  panel.appendChild(summary);
  panel.appendChild(history);

  root.appendChild(pill);
  root.appendChild(panel);
  document.body.appendChild(root);

  function setStatus(text) {
    status.textContent = text;
  }

  function setSummary(text) {
    summary.textContent = text;
  }

  function setButtonsDisabled(disabled) {
    fullButton.disabled = disabled;
    newButton.disabled = disabled;
    refreshButton.disabled = disabled;
  }

  function setBusy(busy) {
    pillDot.style.display = busy ? 'block' : 'none';
    pillLabel.textContent = busy ? 'TikTok сбор · идёт' : 'TikTok сбор';
  }

  function applyExpanded(expanded) {
    uiState.expanded = expanded;
    panel.style.display = expanded ? 'block' : 'none';
    pill.style.display = expanded ? 'none' : 'flex';
    gmSet(STORE_UI, uiState);
  }

  let commentsOpen = false;

  function applyCommentsOpen(open) {
    commentsOpen = open;
    commentsArrow.textContent = open ? '▾' : '▸';
    commentsBody.style.display = open ? 'block' : 'none';
  }

  function updateCollectionEmptyHint() {
    collectionEmptyHint.style.display = collectionSelect.options.length === 0 ? 'block' : 'none';
  }

  function formatStamp(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    const pad = (value) => String(value).padStart(2, '0');
    return (
      date.getFullYear() +
      '-' +
      pad(date.getMonth() + 1) +
      '-' +
      pad(date.getDate()) +
      ' ' +
      pad(date.getHours()) +
      ':' +
      pad(date.getMinutes())
    );
  }

  function renderHistory() {
    const last = gmGet(STORE_HISTORY, null);
    if (!last || !last.at) {
      history.textContent = 'Последний сбор: ещё не было';
      return;
    }
    history.textContent =
      'Последний сбор: ' +
      formatStamp(last.at) +
      ' · ' +
      last.collections +
      ' коллекций · ' +
      last.videos +
      ' видео';
  }

  function csvCell(value) {
    if (value === null || value === undefined) return '';
    const text = String(value);
    if (/[",\n\r]/.test(text)) return '"' + text.replace(/"/g, '""') + '"';
    return text;
  }

  function toCsv(payload) {
    const lines = [CSV_HEADER];
    for (const collection of payload.collections) {
      for (const item of collection.items) {
        const stats = item.stats || {};
        lines.push(
          [
            csvCell(collection.name),
            csvCell(collection.collection_id),
            csvCell(item.video_id),
            csvCell(item.type),
            csvCell(item.url),
            csvCell(item.author),
            csvCell(item.desc),
            csvCell(item.created_at),
            csvCell(stats.plays),
            csvCell(stats.likes),
            csvCell(stats.comments),
            csvCell(stats.shares),
            csvCell((item.images || []).length),
          ].join(','),
        );
      }
    }
    return lines.join('\r\n');
  }

  function download(payload, format) {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    let blob;
    let name;
    if (format === 'csv') {
      blob = new Blob([toCsv(payload)], { type: 'text/csv' });
      name = 'tiktok-collections-' + stamp + '.csv';
    } else {
      blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
      name = 'tiktok-collections-' + stamp + '.json';
    }
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 10000);
  }

  async function loadCollectionsForSelect() {
    const account = resolveAccount();
    if (!account.secUid) {
      setStatus('secUid не найден. Откройте свой профиль (tiktok.com/@ваш_ник) и нажмите снова.');
      return;
    }
    setStatus('Загрузка списка коллекций...');
    const collections = await fetchCollections(account.secUid);
    collectionSelect.innerHTML = '';
    for (const collection of collections) {
      const option = document.createElement('option');
      option.value = String(collection.collectionId);
      option.textContent = collection.name || String(collection.collectionId);
      collectionSelect.appendChild(option);
    }
    updateCollectionEmptyHint();
    setStatus('Коллекций: ' + collections.length + '. Выбери нужные для комментариев.');
  }

  async function run(mode) {
    if (running) return;
    running = true;
    control.paused = false;
    control.stopped = false;
    setButtonsDisabled(true);
    setBusy(true);
    setSummary('');
    const totals = { collections: 0, videos: 0, new: 0, errors: [] };
    try {
      const account = resolveAccount();
      if (!account.secUid) {
        setStatus('secUid не найден. Откройте свой профиль (tiktok.com/@ваш_ник) и нажмите снова.');
        return;
      }
      const baseline = gmGet(STORE_BASELINE, {});
      const progress = gmGet(STORE_PROGRESS, null);
      const resuming = !!(progress && !progress.finished && progress.mode === mode);
      const runId = resuming ? progress.run_id : 'run_' + Date.now();
      const startedAt = resuming && progress.started_at ? progress.started_at : new Date().toISOString();
      const completed = new Set(resuming ? progress.completed || [] : []);
      if (resuming) {
        setStatus('Продолжаю прерванный запуск ' + runId + ' (' + completed.size + ' коллекций уже готово).');
      }
      const saveProgress = () => {
        gmSet(STORE_PROGRESS, {
          run_id: runId,
          mode: mode,
          completed: Array.from(completed),
          started_at: startedAt,
          finished: false,
        });
      };
      saveProgress();

      setStatus('Загрузка коллекций...');
      const collections = await fetchCollections(account.secUid);

      const commentsOn = commentsCheck.checked;
      const commentTargets = new Set();
      if (commentsOn) {
        for (const option of collectionSelect.selectedOptions) commentTargets.add(option.value);
        if (commentTargets.size === 0) {
          setStatus('Комментарии включены, но коллекции не выбраны — комментарии пропускаю.');
        }
      }

      const payload = {
        schema_version: 'collection-capture/v1',
        captured_at: new Date().toISOString(),
        platform: 'tiktok',
        account: {
          unique_id: account.uniqueId,
          sec_uid: account.secUid,
          uid: account.uid,
        },
        collections: [],
      };

      for (let index = 0; index < collections.length; index += 1) {
        const collection = collections[index];
        const collectionId = String(collection.collectionId);
        if (completed.has(collectionId)) continue;
        const name = collection.name || '';
        const expected = toNumber(collection.total);
        const label = 'Коллекция ' + (index + 1) + '/' + collections.length + ' — ' + name + ' — ';
        try {
          await checkpoint();
          setStatus(label + '0/' + (expected === null ? '?' : expected));
          const raw = await fetchItems(collectionId, (count) => {
            setStatus(label + count + '/' + (expected === null ? '?' : expected));
          });
          const seen = new Set();
          const items = [];
          for (const item of raw) {
            const mapped = mapItem(item);
            if (seen.has(mapped.video_id)) continue;
            seen.add(mapped.video_id);
            items.push(mapped);
          }
          const previous = Array.isArray(baseline[collectionId]) ? baseline[collectionId] : [];
          const previousSet = new Set(previous);
          const currentIds = items.map((item) => item.video_id);
          const newItems = items.filter((item) => !previousSet.has(item.video_id));
          const removed = previous.filter((id) => !seen.has(id)).length;
          const outputItems = mode === 'new' ? newItems : items;
          if (commentsOn && commentTargets.has(collectionId)) {
            for (let position = 0; position < outputItems.length; position += 1) {
              await checkpoint();
              setStatus(label + 'комментарии ' + (position + 1) + '/' + outputItems.length);
              try {
                outputItems[position].comments = await fetchComments(outputItems[position].video_id);
              } catch (commentError) {
                if (control.stopped) throw commentError;
                outputItems[position].comments = [];
              }
            }
          }
          payload.collections.push({
            collection_id: collectionId,
            name: name,
            expected: expected === null ? 0 : expected,
            actual: outputItems.length,
            items: outputItems,
          });
          baseline[collectionId] = currentIds;
          gmSet(STORE_BASELINE, baseline);
          totals.collections += 1;
          totals.videos += outputItems.length;
          totals.new += newItems.length;
          completed.add(collectionId);
          saveProgress();
          setStatus(
            label +
              outputItems.length +
              '/' +
              (expected === null ? '?' : expected) +
              ' (+' +
              newItems.length +
              ' новых, -' +
              removed +
              ' удалено)',
          );
        } catch (error) {
          if (control.stopped) throw error;
          const message = error && error.message ? error.message : String(error);
          totals.errors.push(collectionId + ' ' + name + ': ' + message);
          setStatus(label + 'ошибка: ' + message);
        }
      }

      payload.run = {
        started_at: startedAt,
        finished_at: new Date().toISOString(),
        mode: mode,
        totals: {
          collections: totals.collections,
          videos: totals.videos,
          new: totals.new,
          errors: totals.errors,
        },
      };
      download(payload, settings.format);
      gmDel(STORE_PROGRESS);
      gmSet(STORE_HISTORY, {
        at: new Date().toISOString(),
        collections: totals.collections,
        videos: totals.videos,
      });
      renderHistory();
      const summaryText =
        'Готово (' +
        (mode === 'new' ? 'новое' : 'всё') +
        '): ' +
        totals.collections +
        ' коллекций, ' +
        totals.videos +
        ' видео, +' +
        totals.new +
        ' новых, ' +
        totals.errors.length +
        ' ошибок.';
      setStatus(summaryText);
      setSummary(summaryText + (totals.errors.length ? '\n' + totals.errors.join('\n') : ''));
    } catch (error) {
      if (control.stopped) {
        setStatus('Остановлено. Прогресс сохранён — следующий запуск продолжит с места остановки.');
      } else {
        setStatus('Ошибка: ' + (error && error.message ? error.message : String(error)));
      }
    } finally {
      running = false;
      control.paused = false;
      control.stopped = false;
      setButtonsDisabled(false);
      setBusy(false);
    }
  }

  pill.addEventListener('click', () => applyExpanded(true));
  collapseButton.addEventListener('click', () => applyExpanded(false));
  commentsHeader.addEventListener('click', () => applyCommentsOpen(!commentsOpen));

  fullButton.addEventListener('click', () => run('full'));
  newButton.addEventListener('click', () => run('new'));

  pauseButton.addEventListener('click', () => {
    if (running) {
      control.paused = true;
      setStatus('Пауза. Нажмите «Продолжить».');
    }
  });

  resumeButton.addEventListener('click', () => {
    if (running) {
      control.paused = false;
      setStatus('Продолжаю...');
    }
  });

  stopButton.addEventListener('click', () => {
    if (running) {
      control.stopped = true;
      control.paused = false;
      setStatus('Останавливаю...');
    }
  });

  formatSelect.addEventListener('change', () => {
    settings.format = formatSelect.value;
    saveSettings();
  });

  delayInput.addEventListener('change', () => {
    const value = Number(delayInput.value);
    settings.delay = Number.isFinite(value) && value >= 0 ? value : DEFAULT_DELAY;
    delayInput.value = String(settings.delay);
    saveSettings();
  });

  commentsCheck.addEventListener('change', async () => {
    commentsWarning.style.display = commentsCheck.checked ? 'block' : 'none';
    if (commentsCheck.checked && collectionSelect.options.length === 0) {
      try {
        await loadCollectionsForSelect();
      } catch (error) {
        setStatus('Ошибка загрузки коллекций: ' + (error && error.message ? error.message : String(error)));
      }
    }
  });

  refreshButton.addEventListener('click', async () => {
    try {
      await loadCollectionsForSelect();
    } catch (error) {
      setStatus('Ошибка загрузки коллекций: ' + (error && error.message ? error.message : String(error)));
    }
  });

  applyExpanded(!!uiState.expanded);
  applyCommentsOpen(false);
  renderHistory();
})();
