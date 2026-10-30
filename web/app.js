/* 每日饮食 · 配置后台前端
   没有框架，所以状态就一个 S 对象，改完标 dirty，点保存才写盘。
   刻意不做「改一下就自动保存」：这里的参数会直接影响真金白银的生图调用，
   误碰一下不该悄悄生效。 */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

let S = null;          // 后端状态（默认值、候选、库存、密钥状态）
let CFG = null;        // 当前编辑中的配置
let dirty = false;     // 有未保存改动
let pollTimer = null;
let logSince = 0;
let running = false;

const MODE_DESC = {
  recipe: '从 1093 道真实菜谱里选一道，缺的食材用库存里的同类替换。有真实步骤、真实分量，菜是真的。',
  combo: '从库存的蛋白 / 蔬菜 / 主食里枚举组合，自己拼出一道菜。永远做得出来，价格最可控，但没有步骤。',
};

/* ---------------------------------------------------------------- 工具 */
const get = (obj, path) => path.split('.').reduce((o, k) => (o == null ? o : o[k]), obj);
const put = (obj, path, val) => {
  const ks = path.split('.');
  let cur = obj;
  for (const k of ks.slice(0, -1)) cur = cur[k] = cur[k] || {};
  cur[ks[ks.length - 1]] = val;
};

function toast(msg, ms = 2000) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(t._tm);
  t._tm = setTimeout(() => t.classList.remove('on'), ms);
}

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opt);
  const txt = await r.text();
  try { return JSON.parse(txt); } catch { return { error: txt.slice(0, 300) }; }
}

function mark() { dirty = true; $('#btn-save').classList.add('primary'); }

const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

/* ---------------------------------------------------------------- 渲染 */
function renderStatus() {
  const e = S.env, inv = S.inventory;
  const chips = [];
  chips.push(inv.exists
    ? `<span class="tag ${inv.count ? 'ok' : 'bad'}">库存 ${inv.count} 条${inv.bad.length ? ` · ${inv.bad.length} 行有问题` : ''}</span>`
    : `<span class="tag bad">库存文件缺失</span>`);
  chips.push(`<span class="tag mute">菜谱 ${S.recipe_total} 道</span>`);
  chips.push(e.ark ? `<span class="tag ok">生图密钥就绪</span>`
    : `<span class="tag bad">缺 ARK_API_KEY</span>`);
  chips.push(e.tts_app ? `<span class="tag ok">语音密钥就绪</span>`
    : e.tts_key ? `<span class="tag warn">语音走 API Key 体系</span>`
      : `<span class="tag warn">语音未配置，将用占位音轨</span>`);
  if (dirty) chips.push('<span class="tag brand">有未保存改动</span>');
  $('#status').innerHTML = chips.join('');
}

function renderChips(host, items, selected, onToggle) {
  host.innerHTML = items.map(it => {
    const on = selected.includes(it.id);
    return `<button class="chip" data-id="${esc(it.id)}" aria-pressed="${on}">${esc(it.label)}`
      + (it.count != null ? `<span class="n">${it.count}</span>` : '') + `</button>`;
  }).join('');
  $$('.chip', host).forEach(btn => {
    btn.onclick = () => onToggle(btn.dataset.id, btn);
  });
}

function toggleIn(arr, id) {
  const i = arr.indexOf(id);
  if (i >= 0) arr.splice(i, 1); else arr.push(id);
  return arr;
}

function renderAll() {
  const c = CFG;

  // 模式
  $$('#mode button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === c.mode)));
  $('#mode-desc').textContent = MODE_DESC[c.mode] || '';

  // 分类
  renderChips($('#cats'), S.categories, c.categories, (id, btn) => {
    if (c.categories.length === 1 && c.categories[0] === id) {
      toast('至少要选一个类型'); return;
    }
    toggleIn(c.categories, id);
    btn.setAttribute('aria-pressed', String(c.categories.includes(id)));
    mark(); renderStatus();
  });

  // 营养标准：空 = 跟着每道菜自己的分类；选一项 = 全部按这套标准打分
  renderChips($('#target_category'),
    [{ id: '', label: '跟随菜谱分类' }].concat(S.categories.map(x => ({ id: x.id, label: x.label }))),
    [c.target_category || ''], (id) => {
      c.target_category = id;
      $$('.chip', $('#target_category')).forEach(b =>
        b.setAttribute('aria-pressed', String((b.dataset.id || '') === (c.target_category || ''))));
      mark(); renderStatus();
    });

  // 餐次
  renderChips($('#slots'), S.slots, c.meal_types, (id, btn) => {
    if (c.meal_types.length === 1 && c.meal_types[0] === id) { toast('至少要选一个餐次'); return; }
    toggleIn(c.meal_types, id);
    btn.setAttribute('aria-pressed', String(c.meal_types.includes(id)));
    mark(); renderStatus();
  });

  // 菜系 / 做法 / 口味
  const facet = (items) => items.slice(0, 24);
  renderChips($('#cuisines'), facet(S.cuisines), c.cuisines, (id, b) => {
    toggleIn(c.cuisines, id); b.setAttribute('aria-pressed', String(c.cuisines.includes(id))); mark();
  });
  renderChips($('#methods'), facet(S.methods), c.methods, (id, b) => {
    toggleIn(c.methods, id); b.setAttribute('aria-pressed', String(c.methods.includes(id))); mark();
  });
  renderChips($('#flavors'), facet(S.flavors), c.flavors, (id, b) => {
    toggleIn(c.flavors, id); b.setAttribute('aria-pressed', String(c.flavors.includes(id))); mark();
  });

  // 数字滑杆
  bindRange('budget', v => '¥' + v);
  bindRange('max_minutes', v => v + ' 分');
  bindRange('repeat_cooldown_days', v => (Number(v) > 0 ? v + ' 天' : '不限制'));

  // 选菜方式：决定「每天出的菜是不是同一道」
  renderChips($('#pick'),
    [{ id: 'random', label: '随机不重样' }, { id: 'best', label: '固定最高分' }],
    [c.pick], (id) => {
      c.pick = id;
      $$('.chip', $('#pick')).forEach(b =>
        b.setAttribute('aria-pressed', String(b.dataset.id === id)));
      mark(); renderStatus();
    });

  bindRange('subs.main_size', v => v + ' px');
  bindRange('subs.title_size', v => v + ' px');
  bindRange('video.transition', v => Number(v).toFixed(2) + ' s');
  bindRange('speed', v => Number(v).toFixed(2) + '×');

  // 透明度是十六进制字符串，滑杆用 0-255
  const alphaNum = parseInt(c.subs.box_alpha || '66', 16) || 0;
  $('#subs.box_alpha_num').value = alphaNum;
  $('#subs.box_alpha_num-v').textContent = c.subs.box_alpha + ' / ' + alphaNum;

  // 忌口
  $('#exclude').value = (c.exclude || []).join('\n');

  // 配音
  $$('#tts-engine button').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.v === c.tts.engine)));
  const sel = $('#speaker');
  sel.innerHTML = S.speakers.map(s =>
    `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join('')
    + `<option value="">（用下面自定义的 ID）</option>`;
  sel.value = S.speakers.some(s => s.id === c.tts.speaker) ? c.tts.speaker : '';
  $('#speaker-custom').value = S.speakers.some(s => s.id === c.tts.speaker) ? '' : (c.tts.speaker || '');

  // 开关
  $('#video.reuse_images').checked = !!c.video.reuse_images;
  $('#chat').checked = !!c.chat;

  renderInventory();
  renderKeys();
  renderCfgTable();
  renderStatus();
}

function bindRange(path, fmt) {
  const el = $('#' + path), out = $('#' + path + '-v');
  el.value = get(CFG, path);
  out.textContent = fmt(el.value);
  el.oninput = () => { out.textContent = fmt(el.value); put(CFG, path, Number(el.value)); mark(); renderStatus(); };
}

function renderKeys() {
  const e = S.env || {};
  const modeTxt = e.tts_mode === 'app' ? 'APP ID + Token（旧版体系，实测可用）'
    : e.tts_mode === 'key' ? 'API Key（新版体系，UUID）'
      : '未配置';
  $('#env-file').textContent = (e.env_file || '.env');
  const rows = (e.keys || []).map(k => {
    const cls = k.set ? 'ok' : 'mute';
    return `<div class="kv-row">
      <div class="kv-k">${esc(k.label)}<code>${esc(k.name)}</code></div>
      <div class="kv-v">
        ${k.set
          ? `<span class="tag ${cls}">已配置</span><code>${esc(k.value)}</code>`
          : `<span class="tag bad">未配置</span>`}
      </div>
    </div>`;
  }).join('');
  const head = `<div class="kv-row">
      <div class="kv-k">语音鉴权体系</div>
      <div class="kv-v"><span class="tag ${e.tts_mode ? 'ok' : 'bad'}">${esc(modeTxt)}</span></div>
    </div>`;
  $('#keys').innerHTML = head + rows;
}

const CFG_LABEL = {
  'mode': '推荐方式', 'categories': '菜谱类型', 'meal_types': '餐次筛选',
  'cuisines': '菜系', 'methods': '做法', 'flavors': '口味',
  'max_minutes': '最长用时(分)', 'budget': '单餐预算(元)', 'exclude': '忌口',
  'user': '用户', 'slot': '出片餐次', 'inventory': '库存文件',
  'tts.engine': '配音引擎', 'tts.speaker': '音色', 'tts.speed': '语速',
  'tts.resource_id': '语音资源', 'video.reuse_images': '只用缓存图',
  'video.transition': '转场时长(秒)', 'subs.main_size': '字幕字号',
  'subs.title_size': '标题字号', 'subs.stat_size': '营养卡字号',
  'subs.margin_v': '字幕下边距', 'subs.box_alpha': '底框透明度',
  'subs.wrap': '每行字数', 'chat': '模型改写文案', 'chat_model': '文案模型',
};

function renderCfgTable() {
  const flat = [];
  (function walk(o, pre) {
    for (const [k, v] of Object.entries(o)) {
      const path = pre ? pre + '.' + k : k;
      if (v && typeof v === 'object' && !Array.isArray(v)) walk(v, path);
      else flat.push([path, v]);
    }
  })(CFG, '');
  const rows = flat.map(([path, v]) => {
    let shown;
    if (Array.isArray(v)) shown = v.length ? v.join('、') : '<span class="muted">（不限）</span>';
    else if (typeof v === 'boolean') shown = v ? '是' : '否';
    else if (v === '') shown = '<span class="muted">（默认）</span>';
    else shown = esc(v);
    return `<tr><td>${esc(CFG_LABEL[path] || path)}</td>
      <td class="muted" style="font-size:11.5px"><code>${esc(path)}</code></td>
      <td class="num">${shown}</td></tr>`;
  }).join('');
  $('#cfg-table').innerHTML =
    `<thead><tr><th>参数</th><th>键名</th><th class="right">当前值</th></tr></thead>
     <tbody>${rows}</tbody>`;
}

function renderInventory() {
  const inv = S.inventory;
  $('#inv-hint').textContent = inv.exists
    ? inv.path.split(/[\\/]/).pop() : '未找到';
  if (!inv.exists) {
    $('#inv-table').innerHTML = `<tr><td class="muted">找不到 ${esc(inv.path)}</td></tr>`;
    return;
  }
  const rows = inv.items.map(it => {
    const d = it.days_left;
    const cls = d < 0 ? 'bad' : d <= 1 ? 'warn' : 'mute';
    const dl = d < 0 ? `已过期 ${-d}天` : d === 0 ? '今天到期' : `${d}天`;
    return `<tr>
      <td>${esc(it.disp)}</td>
      <td class="muted" style="font-size:11.5px">${esc(it.role)}</td>
      <td class="num">${it.edible_g}<span class="muted">g</span></td>
      <td><span class="tag ${cls}" style="padding:1px 7px">${dl}</span></td>
      <td class="num muted">${it.tier_sym}</td>
    </tr>`;
  }).join('');
  $('#inv-table').innerHTML =
    `<thead><tr><th>食材</th><th>角色</th><th class="right">可食部</th><th>保质</th><th class="right">价位</th></tr></thead>
     <tbody>${rows}</tbody>`;
}

/* ---------------------------------------------------------------- 预览 */
const LBL = {
  target: '营养目标', fidelity: '平替保真', cost: '价格',
  expiry: '临期', time: '用时', variety: '多样',
};

function dishCard(m, i) {
  const f = m.facts;
  const items = m.items.map(it => {
    if (it.swapped && it.from) {
      return `<span class="it swapped" title="${esc(it.from)} → ${esc(it.disp)}">
        <span class="orig">${esc(it.from)}</span>→<b>${esc(it.disp)}</b>
        <span class="g">${Math.round(it.edible_g)}g</span></span>`;
    }
    return `<span class="it">${esc(it.disp)}<span class="g">${Math.round(it.edible_g)}g</span></span>`;
  }).join('');

  const adj = m.items.filter(x => x.adjust)
    .map(x => `<div class="flag brand"><span class="ico">⇄</span><span>${esc(x.disp)} 按熟重折算：${x.adjust.from_grams}→${x.adjust.to_grams}g（${esc(x.adjust.why)}）</span></div>`)
    .join('');

  const flags = (m.flags || []).map((t, k) =>
    `<div class="flag ${k === 0 ? '' : ''}"><span class="ico">${k === 0 ? '!' : '·'}</span><span>${esc(t)}</span></div>`).join('');

  const parts = Object.entries(m.parts || {}).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<span class="tag mute" style="font-weight:500">${LBL[k] || k} ${(v * 100).toFixed(0)}</span>`)
    .join('');

  const meta = [
    m.category_label, m.cuisine && m.cuisine !== 'generic' ? m.cuisine : null,
    (m.methods || []).join('/'), (m.flavors || []).join('/'),
  ].filter(Boolean).map(esc).join(' · ');

  const steps = (m.steps || []).length
    ? `<div class="detail"><h4>菜谱步骤（原文）</h4><ol class="steps">${m.steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol></div>`
    : '';

  const kcalNote = m.recipe_kcal && Math.abs(m.recipe_kcal - f.kcal) > 40
    ? `<span class="muted" style="font-size:11px">菜谱标注 ${m.recipe_kcal}</span>` : '';

  return `<div class="dish ${i === 0 ? 'best' : ''}">
    <div class="head">
      <div class="rank">${i + 1}</div>
      <div class="title">
        <h3>${esc(m.emoji || '')} ${esc(m.name)}</h3>
        <div class="meta">
          ${meta ? `<span>${meta}</span>` : ''}
          ${m.fidelity != null ? `<span class="tag ${m.uses_stock ? 'ok' : (m.fidelity > .9 ? 'warn' : 'bad')}" style="padding:1px 7px">保真 ${(m.fidelity * 100).toFixed(0)}%</span>` : ''}
          ${m.uses_stock ? '<span class="tag ok" style="padding:1px 7px">全用库存</span>' : ''}
        </div>
      </div>
      <div class="score"><b>${(m.score * 100).toFixed(1)}</b><span>SCORE</span></div>
    </div>
    <div class="kv">
      <div><b>${f.kcal}</b><span>千卡 ${kcalNote}</span></div>
      <div><b>${f.protein}<small>g</small></b><span>蛋白质</span></div>
      <div><b>${m.minutes}<small>分</small></b><span>用时</span></div>
      <div><b>¥${m.cost}</b><span>成本</span></div>
    </div>
    <div class="detail"><h4>下锅的食材</h4><div class="ilist">${items}</div></div>
    ${m.swaps && m.swaps.length ? `<div class="detail"><h4>平替明细</h4><div class="ilist">${
      m.swaps.map(s => `<span class="it swapped"><span class="orig">${esc(s.from)}</span>→<b>${esc(s.to)}</b><span class="g">${esc(s.reason)}</span></span>`).join('')
    }</div></div>` : ''}
    ${adj ? `<div class="detail"><h4>分量折算</h4><div class="flags">${adj}</div></div>` : ''}
    ${steps}
    <div class="detail">
      <h4>打分构成</h4>
      <div class="chips" style="gap:6px">${parts}</div>
      ${flags ? `<div class="flags" style="margin-top:9px">${flags}</div>` : ''}
    </div>
  </div>`;
}

async function runPreview() {
  const btn = $('#btn-preview');
  btn.disabled = true; btn.textContent = '跑…';
  try {
    const r = await api('/api/preview', { config: CFG });
    if (r.error) {
      $('#funnel-wrap').hidden = false;
      $('#funnel').innerHTML = `<span class="tag bad">${esc(r.error)}</span>`
        + (r.hint ? `<span class="muted" style="font-size:12px">${esc(r.hint)}</span>` : '');
      $('#preview').innerHTML = `<div class="empty"><b>筛不出菜</b>${esc(r.error)}</div>`;
      return;
    }
    $('#funnel-wrap').hidden = false;
    $('#funnel').innerHTML = (r.funnel || []).map(([n, c], i) =>
      `<span class="st">${esc(n)} <span class="n">${c}</span></span>`).join('<span class="arrow">→</span>')
      + `<span class="arrow">｜</span><span class="st">可用 <span class="n">${r.n}</span></span>`
      + `<span class="st">超预算 <span class="n">${r.over_budget ?? 0}</span></span>`
      + `<span class="muted" style="font-size:11.5px;margin-left:4px">${r.ms}ms</span>`;
    $('#preview').innerHTML = `<div class="plan">${r.top3.map(dishCard).join('')}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = '跑一次';
  }
}

/* ---------------------------------------------------------------- 出片 */
function paintLog(lines) {
  const box = $('#log');
  let html = '';
  for (const l of lines) {
    let cls = '';
    if (/^\[\d\/8\]/.test(l)) cls = 'l-step';
    else if (/失败|错误|Error|Traceback|注意/.test(l)) cls = 'l-err';
    else if (/降级|警告|缺|跳过|超预算/.test(l)) cls = 'l-warn';
    else if (/成片|产出目录|总耗时/.test(l)) cls = 'l-ok';
    html += `<span class="${cls}">${esc(l)}</span>\n`;
  }
  if (html) {
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
    box.insertAdjacentHTML('beforeend', html);
    if (atBottom) box.scrollTop = box.scrollHeight;
  }
}

function setProgress(n) {
  $('#bar').style.width = Math.max(0, Math.min(100, (n / 8) * 100)) + '%';
}

async function poll() {
  const r = await api('/api/run?since=' + logSince);
  if (r.lines && r.lines.length) {
    paintLog(r.lines);
    logSince = r.total;
    const last = [...r.lines].reverse().find(l => /^\[(\d)\/8\]/.test(l));
    if (last) setProgress(Number(last.match(/^\[(\d)/)[1]));
  }
  running = !!r.running;
  $('#btn-run').disabled = running;
  $('#btn-stop').disabled = !running;

  if (!running && r.rc != null) {
    clearInterval(pollTimer); pollTimer = null;
    setProgress(8);
    if (r.rc === 0) {
      paintLog(['', '✔ 出片完成']);
      toast('出片完成');
      await loadResult(r.run_id);
      await loadRuns();
    } else {
      paintLog(['', `✘ 退出码 ${r.rc}，看上面的日志找原因`]);
    }
  }
}

async function startRun() {
  await saveConfig(true);
  const r = await api('/api/run', { config: CFG });
  if (!r.ok) { toast(r.error || '启动失败'); return; }
  $('#log').textContent = '';
  logSince = 0;
  paintLog(['▶ ' + r.argv.join(' ')]);
  setProgress(1);
  running = true;
  $('#btn-run').disabled = true;
  $('#btn-stop').disabled = false;
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(poll, 1200);
  poll();
}

async function stopRun() {
  await api('/api/run/stop', {});
  toast('已请求停止');
}

async function loadResult(runId) {
  const runs = await api('/api/runs');
  const r = (runs.runs || []).find(x => x.run_id === runId) || (runs.runs || [])[0];
  if (!r) return;
  renderResult(r);
}

function renderResult(r) {
  const base = '/runs/' + encodeURIComponent(r.run_id) + '/';
  const media = r.video
    ? `<video src="${base}final.mp4" controls poster="${r.cover ? base + 'cover.jpg' : ''}"></video>`
    : (r.cover ? `<img src="${base}cover.jpg" alt="封面">` : '<div class="empty">无视频</div>');
  $('#result').innerHTML = `<div class="result">
    <div class="shot">${media}</div>
    <div>
      <h3 style="margin:0 0 4px;font-size:16px">${esc(r.dish)}</h3>
      <div class="chips" style="margin-bottom:10px">
        <span class="tag mute">${esc(r.mode)}</span>
        <span class="tag mute">${esc(r.date)} · ${esc(r.slot)}</span>
        ${r.cost != null ? `<span class="tag brand">¥${r.cost}</span>` : ''}
        ${r.kcal != null ? `<span class="tag mute">${r.kcal} 千卡</span>` : ''}
        ${r.degraded ? '<span class="tag warn">配音降级</span>' : '<span class="tag ok">真人声</span>'}
      </div>
      <div class="actions">
        <a class="btn sm" href="${base}final.mp4" download>下载视频</a>
        <a class="btn sm" href="${base}cover.jpg" download>下载封面</a>
        <a class="btn sm" href="${base}manifest.json" target="_blank">看 manifest</a>
        <a class="btn sm" href="${base}旁白稿.md" target="_blank">看旁白稿</a>
      </div>
      <div class="muted" style="font-size:12px;margin-top:9px">
        耗时 ${r.elapsed ?? '-'} 秒 · 生成于 ${esc(r.generated_at || '')}
      </div>
    </div>
  </div>`;
}

async function loadRuns() {
  const r = await api('/api/runs');
  const list = r.runs || [];
  $('#runs-hint').textContent = list.length ? `最近 ${list.length} 次` : '';
  if (!list.length) {
    $('#runs').innerHTML = '<div class="empty"><b>还没有历史</b>出一次片就有了</div>';
    return;
  }
  $('#runs').innerHTML = list.map(x => `
    <div class="actions" style="justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--line-2)">
      <span style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        <b>${esc(x.dish || x.run_id)}</b>
        <span class="muted" style="font-size:11.5px"> · ${esc(x.date)} ${esc(x.slot)} · ${esc(x.mode)} · ¥${x.cost ?? '-'}</span>
      </span>
      <button class="btn sm" data-run="${esc(x.run_id)}">查看</button>
    </div>`).join('');
  $$('#runs [data-run]').forEach(b => {
    b.onclick = () => {
      const x = list.find(y => y.run_id === b.dataset.run);
      if (x) { renderResult(x); $('#result').scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    };
  });
}

/* ---------------------------------------------------------------- 保存 */
async function saveConfig(silent) {
  const r = await api('/api/config', { config: CFG });
  if (r.ok) {
    CFG = r.config;
    dirty = false;
    renderStatus();
    if (!silent) toast('已保存' + (r.notes && r.notes.length ? '（' + r.notes.length + ' 项被修正）' : ''));
    if (r.notes && r.notes.length) paintLog(r.notes.map(n => '配置修正：' + n));
    return true;
  }
  toast(r.error || '保存失败');
  return false;
}

/* ---------------------------------------------------------------- 事件 */
function bindEvents() {
  $$('#mode button').forEach(b => b.onclick = () => {
    CFG.mode = b.dataset.v; renderAll(); mark();
  });
  $$('#tts-engine button').forEach(b => b.onclick = () => {
    CFG.tts.engine = b.dataset.v; renderAll(); mark();
  });

  $('#exclude').oninput = (e) => {
    CFG.exclude = e.target.value.split(/[\n,，、]/).map(s => s.trim()).filter(Boolean);
    mark(); renderStatus();
  };

  $('#subs.box_alpha_num').oninput = (e) => {
    const n = Number(e.target.value);
    CFG.subs.box_alpha = n.toString(16).toUpperCase().padStart(2, '0');
    $('#subs.box_alpha_num-v').textContent = CFG.subs.box_alpha + ' / ' + n;
    mark(); renderStatus();
  };

  $('#speaker').onchange = (e) => {
    if (e.target.value) { CFG.tts.speaker = e.target.value; $('#speaker-custom').value = ''; }
    mark();
  };
  $('#speaker-custom').oninput = (e) => {
    const v = e.target.value.trim();
    if (v) { CFG.tts.speaker = v; $('#speaker').value = ''; }
    mark();
  };

  $('#video.reuse_images').onchange = (e) => { CFG.video.reuse_images = e.target.checked; mark(); };
  $('#chat').onchange = (e) => { CFG.chat = e.target.checked; mark(); };

  $('#btn-save').onclick = () => saveConfig(false);
  $('#btn-preview').onclick = () => runPreview();
  $('#btn-run').onclick = () => startRun();
  $('#btn-stop').onclick = () => stopRun();
  $('#btn-reload').onclick = () => load();

  window.addEventListener('beforeunload', (e) => {
    if (dirty) { e.preventDefault(); e.returnValue = ''; }
  });
}

/* ---------------------------------------------------------------- 启动 */
async function load() {
  const r = await api('/api/bootstrap');
  if (!r.config) { alert('后台没起来：' + (r.error || '拿不到 bootstrap')); return; }
  S = r;
  CFG = r.config;
  dirty = false;
  renderAll();
  bindEvents();
  await loadRuns();
  const runner = r.runner || {};
  if (runner.running) {
    // 页面刷新时任务还在跑，接上日志尾巴
    running = true;
    $('#btn-run').disabled = true;
    $('#btn-stop').disabled = false;
    pollTimer = setInterval(poll, 1200);
    poll();
  } else if (runner.run_id && runner.rc === 0) {
    loadResult(runner.run_id);
  }
  if (r.broken) paintLog(['配置文件读不出来，已用默认值：' + r.broken]);
}

load();
