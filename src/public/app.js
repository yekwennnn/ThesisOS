/* ThesisOS · ThesisDiff —— 前端单页应用（零依赖） */
'use strict';

/* ---------------------------------------------------------------- 状态 */

const state = {
  settings: null,   // 脱敏后的设置
  providers: null,  // 可选大模型与数据源目录
  companies: [],    // 公司摘要列表
};

const app = document.getElementById('app');

/* ---------------------------------------------------------------- 基础工具 */

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {} };
  if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  let resp;
  try {
    resp = await fetch(path, init);
  } catch {
    throw new Error('连不上本地服务，请确认服务已启动。');
  }
  let data = null;
  try { data = await resp.json(); } catch { /* 忽略 */ }
  if (!resp.ok) throw new Error((data && data.error) || ('请求失败（HTTP ' + resp.status + '）'));
  return data;
}

function toast(msg, ms = 3600) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add('hidden'), ms);
}

function showOverlay(title, text) {
  document.getElementById('overlay-title').textContent = title;
  document.getElementById('overlay-text').textContent = text;
  document.getElementById('overlay').classList.remove('hidden');
}

function hideOverlay() {
  document.getElementById('overlay').classList.add('hidden');
}

function fmtDate(iso) {
  if (!iso) return '—';
  return String(iso).slice(0, 10);
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function marketName(m) {
  return { A: 'A 股', HK: '港股', US: '美股' }[m] || m || '—';
}

function impactClass(impact) {
  if (/增强/.test(impact)) return 'impact impact-up';
  if (/削弱|证伪/.test(impact)) return 'impact impact-down';
  if (/不足/.test(impact)) return 'impact impact-unknown';
  return 'impact impact-flat';
}

function setNav(key) {
  document.querySelectorAll('[data-nav]').forEach((a) => {
    a.classList.toggle('active', a.dataset.nav === key);
  });
}

function renderDemoBanner() {
  const el = document.getElementById('demo-banner');
  const s = state.settings;
  if (!s) { el.innerHTML = ''; return; }
  const demoLlm = s.llm.provider === 'demo';
  const demoFin = s.finance.source === 'demo';
  if (!demoLlm && !demoFin) { el.innerHTML = ''; return; }
  const parts = [];
  if (demoLlm) parts.push('模拟 AI');
  if (demoFin) parts.push('演示行情');
  el.innerHTML = `<div class="demo-banner">演示模式：当前使用${parts.join('与')}，输出均为示例，不代表真实分析。<a href="#/settings">前往设置接入你自己的大模型与数据源 →</a></div>`;
}

/* ---------------------------------------------------------------- 启动与路由 */

async function boot() {
  try {
    const data = await api('/api/bootstrap');
    state.settings = data.settings;
    state.providers = data.providers;
    state.companies = data.companies;
  } catch (e) {
    app.innerHTML = `<div class="view"><div class="empty"><div class="empty-title">无法连接本地服务</div>${esc(e.message)}</div></div>`;
    return;
  }
  renderDemoBanner();
  // 点击搜索框外部时收起候选下拉
  document.addEventListener('click', (e) => {
    document.querySelectorAll('.search-results').forEach((el) => {
      const box = el.closest('.search-box');
      if (box && !box.contains(e.target)) el.hidden = true;
    });
  });
  window.addEventListener('hashchange', route);
  route();
}

function route() {
  const hash = location.hash || '#/';
  const parts = hash.replace(/^#\//, '').split('/').filter(Boolean);
  setNav(parts[0] === 'settings' ? 'settings' : 'home');

  if (parts.length === 0) return viewDashboard();
  if (parts[0] === 'new') return viewNewCompany();
  if (parts[0] === 'settings') return viewSettings();
  if (parts[0] === 'company' && parts[1]) {
    if (parts[2] === 'materials') return viewCompany(parts[1], 'materials');
    if (parts[2] === 'diffs' && parts[3]) return viewDiff(parts[1], parts[3]);
    if (parts[2] === 'diffs') return viewCompany(parts[1], 'diffs');
    return viewCompany(parts[1], 'thesis');
  }
  viewDashboard();
}

/* ---------------------------------------------------------------- 工作台 */

async function viewDashboard() {
  try {
    state.companies = await api('/api/companies');
  } catch { /* 保留缓存 */ }
  const companies = state.companies;
  let listHtml;
  if (!companies.length) {
    listHtml = `
      <div class="hero">
        <div class="hero-title">把你为什么买它，<br>写下来。</div>
        <p class="hero-sub">ThesisOS 帮你把一家公司的投资逻辑变成一张可以持续检验的卡片：每次财报出来，用十五分钟判断持有理由有没有改变。</p>
        <div class="btn-row">
          <a class="btn" href="#/new">创建第一家公司</a>
          <a class="btn-ghost" href="#/settings">先配置大模型与数据源</a>
        </div>
        <div class="steps">
          <div class="step"><span class="step-num">01</span><div class="step-name">创建公司</div><div class="step-desc">输入名称、代码与当前状态</div></div>
          <div class="step"><span class="step-num">02</span><div class="step-name">确认逻辑卡</div><div class="step-desc">粘贴想法，AI 整理成投资逻辑，你确认</div></div>
          <div class="step"><span class="step-num">03</span><div class="step-name">加入新材料</div><div class="step-desc">粘贴财报、纪要或公告全文</div></div>
          <div class="step"><span class="step-num">04</span><div class="step-name">审阅变更</div><div class="step-desc">看逻辑哪里变了，决定接受或拒绝</div></div>
        </div>
      </div>`;
  } else {
    const rows = companies.map((c) => `
      <div class="company-row" onclick="location.hash='#/company/${c.id}'">
        <div class="company-name">${esc(c.name)}${c.version ? `<span class="ver">${esc(c.version)}</span>` : ''}</div>
        <div class="cell mono hide-sm">${esc(c.code || '—')} · ${marketName(c.market)}</div>
        <div class="cell hide-sm"><span class="chip">${esc(c.status)}</span></div>
        <div class="cell hide-sm">${c.thesisConfirmed ? esc(c.version) + ' · ' + fmtDate(c.asOfDate) : '<span style="color:var(--gray-400)">待建立逻辑卡</span>'}</div>
        <div class="cell mono hide-sm">${c.diffCount ? c.diffCount + ' 份报告' + (c.pendingDiffs ? '（' + c.pendingDiffs + ' 待审阅）' : '') : '—'}</div>
        <div class="cell"><span class="arrow">→</span></div>
      </div>`).join('');
    listHtml = `
      <div class="list-head">
        <h1 class="section-title">我的公司</h1>
        <a class="btn btn-sm" href="#/new">＋ 新建公司</a>
      </div>
      <div class="company-list">${rows}</div>`;
  }
  app.innerHTML = `<div class="view">${listHtml}</div>`;
}

/* ---------------------------------------------------------------- 新建公司 */

function viewNewCompany() {
  const today = new Date().toISOString().slice(0, 10);
  app.innerHTML = `
    <div class="view view-narrow">
      <div class="micro-label">新建公司</div>
      <h1 class="page-title">哪一家公司？</h1>
      <p class="page-desc">输入名称或代码，从候选中选一个即可，其余信息自动补全。</p>

      <div class="search-box" id="search-box">
        <input class="input input-lg" id="nc-q" placeholder="例如：阿里巴巴 / 09988 / 600519 / 腾讯" autocomplete="off" autofocus>
        <div class="search-results" id="search-results" hidden></div>
      </div>
      <div class="search-alt"><button class="link-btn" id="nc-manual-toggle" type="button">候选里没有？手动输入</button></div>

      <div id="nc-picked" hidden>
        <div class="picked-card">
          <div>
            <div class="picked-name" id="pk-name"></div>
            <div class="picked-meta" id="pk-meta"></div>
          </div>
          <button class="link-btn" id="pk-clear" type="button">重选</button>
        </div>
        <div class="form-grid" style="margin-top:28px">
          <div class="field"><label>当前状态</label>
            <select class="select" id="nc-status"><option>研究中</option><option>观察</option><option>持仓</option></select>
          </div>
          <div class="field"><label>分析截止日期</label>
            <input class="input" type="date" id="nc-asof" value="${today}">
            <div class="field-hint">你的判断基于哪一天之前的信息。</div>
          </div>
        </div>
        <div class="btn-row">
          <button class="btn" id="nc-submit">创建公司，建立投资逻辑 →</button>
        </div>
      </div>

      <div id="nc-manual" hidden>
        <div class="panel">
          <div class="form-grid">
            <div class="field"><label>公司名称</label><input class="input" id="nm-name" placeholder="例如：阿里巴巴"></div>
            <div class="field"><label>股票代码（选填）</label><input class="input" id="nm-code" placeholder="例如：09988"></div>
            <div class="field"><label>市场</label>
              <select class="select" id="nm-market"><option value="HK">港股</option><option value="A">A 股</option><option value="US">美股</option></select>
            </div>
            <div class="field"><label>当前状态</label>
              <select class="select" id="nm-status"><option>研究中</option><option>观察</option><option>持仓</option></select>
            </div>
            <div class="field full"><label>分析截止日期</label><input class="input" type="date" id="nm-asof" value="${today}"></div>
          </div>
          <div class="btn-row"><button class="btn" id="nm-submit">创建公司，继续 →</button></div>
        </div>
      </div>
    </div>`;

  const qInput = document.getElementById('nc-q');
  const resultsEl = document.getElementById('search-results');
  let items = [], activeIdx = -1, timer = null, picked = null;

  function closeResults() { resultsEl.hidden = true; items = []; activeIdx = -1; }

  function renderResults() {
    if (!items.length) {
      resultsEl.innerHTML = '<div class="search-empty">没有找到相关证券，换个名称或代码试试</div>';
      resultsEl.hidden = false;
      return;
    }
    resultsEl.innerHTML = items.map((it, i) => `
      <div class="search-item${i === activeIdx ? ' active' : ''}" data-i="${i}">
        <span class="si-name">${esc(it.name)}</span>
        <span class="si-meta">${esc(it.code)} · ${marketName(it.market)}</span>
      </div>`).join('');
    resultsEl.hidden = false;
    resultsEl.querySelectorAll('.search-item').forEach((el) => {
      el.onmousedown = (e) => { e.preventDefault(); pick(items[Number(el.dataset.i)]); };
    });
  }

  function pick(it) {
    picked = it;
    closeResults();
    document.getElementById('pk-name').textContent = it.name;
    document.getElementById('pk-meta').textContent = it.code + ' · ' + marketName(it.market);
    document.getElementById('nc-picked').hidden = false;
    document.getElementById('search-box').style.display = 'none';
    document.querySelector('.search-alt').style.display = 'none';
    document.getElementById('nc-asof').focus();
  }

  function unpick() {
    picked = null;
    document.getElementById('nc-picked').hidden = true;
    document.getElementById('search-box').style.display = '';
    document.querySelector('.search-alt').style.display = '';
    qInput.value = '';
    qInput.focus();
  }

  qInput.addEventListener('input', () => {
    const q = qInput.value.trim();
    clearTimeout(timer);
    if (!q) return closeResults();
    timer = setTimeout(async () => {
      try {
        const r = await api('/api/finance/search?q=' + encodeURIComponent(q));
        items = r.items || [];
        activeIdx = items.length ? 0 : -1;
        renderResults();
      } catch { closeResults(); }
    }, 250);
  });

  qInput.addEventListener('keydown', (e) => {
    if (resultsEl.hidden) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); activeIdx = Math.min(activeIdx + 1, items.length - 1); renderResults(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); activeIdx = Math.max(activeIdx - 1, 0); renderResults(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (items[activeIdx]) pick(items[activeIdx]); }
    else if (e.key === 'Escape') closeResults();
  });

  document.getElementById('pk-clear').onclick = unpick;

  document.getElementById('nc-manual-toggle').onclick = () => {
    const m = document.getElementById('nc-manual');
    m.hidden = !m.hidden;
    document.getElementById('search-box').style.display = m.hidden ? '' : 'none';
    document.getElementById('nc-manual-toggle').textContent = m.hidden ? '候选里没有？手动输入' : '返回搜索';
    if (!m.hidden) document.getElementById('nm-name').focus();
  };

  async function createCompany(body) {
    try {
      const created = await api('/api/companies', { method: 'POST', body });
      if (!state.companies.find((c) => c.id === created.id)) state.companies.push(created);
      location.hash = '#/company/' + created.id;
    } catch (e) { toast(e.message); }
  }

  document.getElementById('nc-submit').onclick = () => {
    if (!picked) return toast('请先从候选中选择一家公司');
    createCompany({
      name: picked.name,
      code: picked.code,
      market: picked.market,
      status: document.getElementById('nc-status').value,
      asOfDate: document.getElementById('nc-asof').value,
    });
  };

  document.getElementById('nm-submit').onclick = () => {
    const name = document.getElementById('nm-name').value.trim();
    if (!name) return toast('请填写公司名称');
    createCompany({
      name,
      code: document.getElementById('nm-code').value.trim(),
      market: document.getElementById('nm-market').value,
      status: document.getElementById('nm-status').value,
      asOfDate: document.getElementById('nm-asof').value,
    });
  };
}


/* ---------------------------------------------------------------- 公司页 */

async function viewCompany(id, tab) {
  app.innerHTML = '<div class="view"><div class="empty">加载中…</div></div>';
  let company;
  try {
    company = await api('/api/companies/' + id);
  } catch (e) {
    app.innerHTML = `<div class="view"><div class="empty"><div class="empty-title">公司不存在</div>${esc(e.message)}</div></div>`;
    return;
  }

  const tabs = [
    ['thesis', '投资逻辑', '#/company/' + id],
    ['materials', `材料（${company.materials.length}）`, '#/company/' + id + '/materials'],
    ['diffs', `变更报告（${company.diffs.length}）`, '#/company/' + id + '/diffs'],
  ];
  const tabsHtml = tabs.map(([k, label, href]) =>
    `<a href="${href}" class="${k === tab ? 'active' : ''}">${label}</a>`).join('');

  const steps = [
    ['建立逻辑卡', company.theses.length > 0, '#/company/' + id],
    ['加入材料', company.materials.length > 0, '#/company/' + id + '/materials'],
    ['生成变更报告', company.diffs.length > 0, '#/company/' + id + '/materials'],
  ];
  const stepsHtml = steps.map(([label, done, href], i) =>
    `<a class="co-step${done ? ' done' : ''}" href="${href}"><i></i>${label}</a>${i < steps.length - 1 ? '<span class="co-step-sep"></span>' : ''}`
  ).join('');

  app.innerHTML = `
    <div class="view">
      <div class="company-head">
        <div class="company-title-row">
          <div>
            <div class="micro-label">${marketName(company.market)} · ${esc(company.code || '无代码')}</div>
            <h1 class="page-title" style="margin-bottom:0">${esc(company.name)}</h1>
          </div>
          <div class="btn-row">
            <select class="select" id="co-status" style="width:120px;height:36px">
              ${['持仓', '观察', '研究中'].map((s) => `<option ${s === company.status ? 'selected' : ''}>${s}</option>`).join('')}
            </select>
            <button class="link-btn" id="co-delete">删除公司</button>
          </div>
        </div>
        <div class="quote-line" id="quote-line"><span class="quote-src">行情加载中…</span></div>
        <div class="co-steps">${stepsHtml}</div>
      </div>
      <div class="tabs">${tabsHtml}</div>
      <div id="tab-body"></div>
    </div>`;

  document.getElementById('co-status').onchange = async (e) => {
    try {
      await api('/api/companies/' + id, { method: 'PATCH', body: { status: e.target.value } });
      toast('状态已更新');
    } catch (err) { toast(err.message); }
  };
  document.getElementById('co-delete').onclick = async () => {
    if (!confirm(`确定删除「${company.name}」吗？其投资逻辑、材料与报告都会一并删除，且不可恢复。`)) return;
    try {
      await api('/api/companies/' + id, { method: 'DELETE' });
      state.companies = state.companies.filter((c) => c.id !== id);
      location.hash = '#/';
    } catch (err) { toast(err.message); }
  };

  loadQuote(company);

  const body = document.getElementById('tab-body');
  if (tab === 'thesis') renderThesisTab(company, body);
  else if (tab === 'materials') renderMaterialsTab(company, body);
  else renderDiffsTab(company, body);
}

async function loadQuote(company) {
  const el = document.getElementById('quote-line');
  if (!company.code) {
    el.innerHTML = '<span class="quote-src">未填写股票代码，暂无行情。</span>';
    return;
  }
  try {
    const q = await api(`/api/finance/quote?code=${encodeURIComponent(company.code)}&market=${encodeURIComponent(company.market)}&name=${encodeURIComponent(company.name)}`);
    if (q.error) {
      el.innerHTML = `<span class="quote-src">行情暂不可用：${esc(q.error)}</span>`;
      return;
    }
    const pct = q.changePct != null ? (q.changePct >= 0 ? '+' : '') + q.changePct.toFixed(2) + '%' : '—';
    const pe = q.pe != null ? 'PE ' + q.pe.toFixed(1) : '';
    el.innerHTML = `
      <span class="quote-price">${q.price != null ? q.price : '—'}</span>
      <span>${pct}</span>
      ${pe ? `<span>${pe}</span>` : ''}
      <span class="quote-src">${esc(q.source)} · ${fmtTime(q.asOf)}${q.demo ? ' · 虚构数据' : ''}</span>`;
  } catch {
    el.innerHTML = '<span class="quote-src">行情加载失败。</span>';
  }
}

/* ---------------------------------------------------------------- 投资逻辑卡渲染 */

function renderCard(card, meta) {
  const c = card;
  const assumptions = (c.assumptions || []).map((a) => `
    <div class="assumption">
      <div class="assumption-head">
        <span class="assumption-id">${esc(a.id)}</span>
        <span class="assumption-text">${esc(a.text)}</span>
      </div>
      ${a.indicators && a.indicators.length ? `
        <ul class="assumption-indicators dot-list">
          ${a.indicators.map((i) => `<li>${esc(i)}</li>`).join('')}
        </ul>` : ''}
    </div>`).join('');

  const listOr = (arr, empty) => arr && arr.length
    ? `<ul class="dot-list">${arr.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`
    : `<div style="color:var(--gray-400);font-size:13px">${empty}</div>`;

  return `
    <div class="thesis-doc">
      <div class="thesis-doc-head">
        <div class="thesis-doc-title">投资逻辑卡</div>
        <div class="thesis-doc-meta">${meta || ''}</div>
      </div>

      <div class="thesis-section">
        <div class="thesis-section-label">01 — 一句话投资逻辑</div>
        <div class="thesis-one-liner">${esc(c.oneLiner)}</div>
      </div>

      <div class="thesis-section">
        <div class="thesis-section-label">02 — 关键假设（${(c.assumptions || []).length} 条）</div>
        ${assumptions || '<div style="color:var(--gray-400)">暂无</div>'}
      </div>

      <div class="thesis-section">
        <div class="thesis-section-label">03 — 证伪条件</div>
        ${listOr(c.falsifiers, '暂无')}
      </div>

      <div class="thesis-section">
        <div class="thesis-section-label">04 — 当前最强反方观点</div>
        <div class="counter-view">${esc(c.counterView || '暂无')}</div>
      </div>

      <div class="thesis-section">
        <div class="thesis-section-label">05 — 估值锚</div>
        <dl class="kv-grid">
          <dt>估值口径</dt><dd>${esc(c.valuation && c.valuation.method || '—')}</dd>
          <dt>合理区间</dt><dd>${esc(c.valuation && c.valuation.range || '—')}</dd>
          <dt>价格隐含</dt><dd>${esc(c.valuation && c.valuation.implied || '—')}</dd>
          <dt>敏感变量</dt><dd>${esc(c.valuation && c.valuation.sensitive || '—')}</dd>
        </dl>
      </div>

      <div class="thesis-section" style="margin-bottom:0">
        <div class="thesis-section-label">06 — 未知问题</div>
        ${listOr(c.unknowns, '暂无')}
      </div>
    </div>`;
}

/* ---------------------------------------------------------------- 逻辑卡编辑器 */

function buildCardEditor(container, card, onSubmit, submitLabel) {
  const c = JSON.parse(JSON.stringify(card || {
    oneLiner: '', assumptions: [], falsifiers: [], counterView: '',
    valuation: { method: '', range: '', implied: '', sensitive: '' }, unknowns: [],
  }));
  if (!c.assumptions.length) c.assumptions.push({ id: 'A-01', text: '', indicators: [] });

  function assumptionRows() {
    return c.assumptions.map((a, i) => `
      <div class="assumption-edit" data-idx="${i}">
        <div class="row-top">
          <span class="aid">${esc(a.id)}</span>
          <input class="input a-text" placeholder="一条可以被现实检验的假设" value="${esc(a.text)}">
          <button class="link-btn a-remove" type="button">删除</button>
        </div>
        <textarea class="textarea textarea-sm a-ind" placeholder="这条假设对应哪些可观察指标？每行一条">${esc((a.indicators || []).join('\n'))}</textarea>
      </div>`).join('');
  }

  container.innerHTML = `
    <div class="panel">
      <div class="field"><label>一句话投资逻辑</label>
        <textarea class="textarea textarea-sm" id="ed-oneliner" placeholder="用一句话说明：为什么这家公司可能是一笔好投资？">${esc(c.oneLiner)}</textarea>
      </div>
      <div class="field"><label>关键假设（3–7 条，每条都要能被检验）</label>
        <div id="ed-assumptions">${assumptionRows()}</div>
        <button class="btn-ghost btn-sm" id="ed-add-a" type="button">＋ 添加一条假设</button>
      </div>
      <div class="field"><label>证伪条件（每行一条）</label>
        <textarea class="textarea textarea-sm" id="ed-falsifiers" placeholder="什么事情发生后，你应该承认原逻辑不再成立？">${esc((c.falsifiers || []).join('\n'))}</textarea>
      </div>
      <div class="field"><label>当前最强反方观点</label>
        <textarea class="textarea textarea-sm" id="ed-counter" placeholder="直接攻击关键假设的反方论证，而不是模板化风险">${esc(c.counterView)}</textarea>
      </div>
      <div class="form-grid">
        <div class="field"><label>估值口径</label><input class="input" id="ed-v-method" value="${esc(c.valuation.method)}" placeholder="例如：市盈率 / 自由现金流"></div>
        <div class="field"><label>合理区间</label><input class="input" id="ed-v-range" value="${esc(c.valuation.range)}" placeholder="你认为合理的估值区间"></div>
        <div class="field"><label>价格隐含假设</label><input class="input" id="ed-v-implied" value="${esc(c.valuation.implied)}" placeholder="当前市场价格隐含了什么"></div>
        <div class="field"><label>敏感变量</label><input class="input" id="ed-v-sensitive" value="${esc(c.valuation.sensitive)}" placeholder="对估值最敏感的变量"></div>
      </div>
      <div class="field"><label>未知问题（每行一条）</label>
        <textarea class="textarea textarea-sm" id="ed-unknowns" placeholder="当前仍然不知道什么？">${esc((c.unknowns || []).join('\n'))}</textarea>
      </div>
      <div class="btn-row">
        <button class="btn" id="ed-submit" type="button">${esc(submitLabel || '确认保存')}</button>
      </div>
    </div>`;

  function syncFromDom() {
    container.querySelectorAll('#ed-assumptions .assumption-edit').forEach((row, i) => {
      c.assumptions[i].text = row.querySelector('.a-text').value;
      c.assumptions[i].indicators = row.querySelector('.a-ind').value.split('\n').map((s) => s.trim()).filter(Boolean);
    });
  }

  function bindRows() {
    container.querySelectorAll('#ed-assumptions .a-remove').forEach((btn) => {
      btn.onclick = () => {
        if (c.assumptions.length <= 1) return toast('至少保留一条假设');
        syncFromDom();
        c.assumptions.splice(Number(btn.closest('.assumption-edit').dataset.idx), 1);
        c.assumptions.forEach((a, i) => (a.id = 'A-' + String(i + 1).padStart(2, '0')));
        container.querySelector('#ed-assumptions').innerHTML = assumptionRows();
        bindRows();
      };
    });
  }
  bindRows();

  container.querySelector('#ed-add-a').onclick = () => {
    syncFromDom();
    c.assumptions.push({ id: 'A-' + String(c.assumptions.length + 1).padStart(2, '0'), text: '', indicators: [] });
    container.querySelector('#ed-assumptions').innerHTML = assumptionRows();
    bindRows();
  };

  container.querySelector('#ed-submit').onclick = () => {
    syncFromDom();
    const card = {
      oneLiner: container.querySelector('#ed-oneliner').value.trim(),
      assumptions: c.assumptions.filter((a) => a.text.trim()),
      falsifiers: container.querySelector('#ed-falsifiers').value.split('\n').map((s) => s.trim()).filter(Boolean),
      counterView: container.querySelector('#ed-counter').value.trim(),
      valuation: {
        method: container.querySelector('#ed-v-method').value.trim(),
        range: container.querySelector('#ed-v-range').value.trim(),
        implied: container.querySelector('#ed-v-implied').value.trim(),
        sensitive: container.querySelector('#ed-v-sensitive').value.trim(),
      },
      unknowns: container.querySelector('#ed-unknowns').value.split('\n').map((s) => s.trim()).filter(Boolean),
    };
    if (!card.oneLiner) return toast('请先写下一句话投资逻辑');
    if (!card.assumptions.length) return toast('请至少保留一条关键假设');
    onSubmit(card);
  };
}

/* ---------------------------------------------------------------- 投资逻辑 Tab */

function renderThesisTab(company, body) {
  if (!company.theses.length) return renderBootstrap(company, body);

  const versions = company.theses;
  const latest = versions[versions.length - 1];

  body.innerHTML = `
    <div class="version-switch">
      <span class="micro-label" style="margin:0">版本</span>
      <select class="select" id="v-select" style="width:auto">
        ${versions.map((v) => `<option value="${esc(v.versionId)}" ${v.versionId === latest.versionId ? 'selected' : ''}>${esc(v.versionId)} · ${fmtDate(v.asOfDate)}${v.userConfirmed ? '' : '（未确认）'}</option>`).join('')}
      </select>
      <span style="flex:1"></span>
      <button class="btn-ghost btn-sm" id="v-revise">修正并保存为新版本</button>
    </div>
    <div id="v-body"></div>
    <div id="v-editor"></div>`;

  function show(versionId) {
    const v = versions.find((x) => x.versionId === versionId) || latest;
    const meta = `${esc(v.versionId)} · 基准日 ${fmtDate(v.asOfDate)} · 确认于 ${fmtTime(v.createdAt)}${v.supersedes ? ' · 取代 ' + esc(v.supersedes) : ''}${v.sourceDiffId ? ' · 来自变更报告' : ''}`;
    document.getElementById('v-body').innerHTML =
      renderCard(v.card, meta) +
      `<div class="thesis-doc-foot" style="margin-top:16px">
        <span>版本 ${esc(v.versionId)}</span><span>as_of ${fmtDate(v.asOfDate)}</span><span>created ${fmtTime(v.createdAt)}</span>${v.supersedes ? `<span>supersedes ${esc(v.supersedes)}</span>` : ''}
      </div>`;
    document.getElementById('v-editor').innerHTML = '';
  }

  document.getElementById('v-select').onchange = (e) => show(e.target.value);
  show(latest.versionId);

  document.getElementById('v-revise').onclick = () => {
    const v = versions.find((x) => x.versionId === document.getElementById('v-select').value) || latest;
    document.getElementById('v-body').innerHTML = `
      <div class="notice">你正在基于 ${esc(v.versionId)} 修正投资逻辑。保存后会生成新版本，原版本完整保留、不会被覆盖。</div>
      <div id="revise-editor"></div>`;
    document.getElementById('v-revise').disabled = true;
    buildCardEditor(document.getElementById('revise-editor'), v.card, async (card) => {
      try {
        const r = await api(`/api/companies/${company.id}/thesis`, { method: 'POST', body: { card, asOfDate: new Date().toISOString().slice(0, 10), note: '手动修正' } });
        toast('已保存为新版本 ' + r.version);
        renderThesisTab(await api('/api/companies/' + company.id), body);
      } catch (e) { toast(e.message); }
    }, '保存为新版本');
    document.getElementById('v-body').scrollIntoView({ behavior: 'smooth' });
  };
}

/* 首次建立投资逻辑 */
function renderBootstrap(company, body) {
  body.innerHTML = `
    <div class="notice">「${esc(company.name)}」还没有投资逻辑卡。用下面任意一种方式建立第一版（V1）：AI 整理你的想法，或手动填写。未经你确认的内容不会成为正式投资逻辑。</div>
    <div class="panel" style="max-width:720px">
      <div class="micro-label">方式一 · AI 帮你整理</div>
      <div class="field">
        <label>粘贴你的投资笔记、买入理由或研究材料</label>
        <textarea class="textarea" id="bs-notes" placeholder="可以粘贴：自己写过的笔记、买入理由、财报摘录、和 AI 的讨论记录……越具体，整理出来的逻辑卡越接近你的真实想法。"></textarea>
      </div>
      <div class="btn-row">
        <button class="btn" id="bs-generate">生成投资逻辑草稿</button>
        <button class="link-btn" id="bs-manual" type="button">不用 AI，我手动填写 →</button>
      </div>
    </div>
    <div id="bs-draft"></div>`;

  document.getElementById('bs-manual').onclick = () => {
    const host = document.getElementById('bs-draft');
    buildCardEditor(host, null, (card) => confirmV1(card), '确认，建立投资逻辑 V1');
    host.scrollIntoView({ behavior: 'smooth' });
  };

  document.getElementById('bs-generate').onclick = async () => {
    const notes = document.getElementById('bs-notes').value.trim();
    if (!notes) return toast('先粘贴一些内容，AI 才有东西可以整理');
    showOverlay('正在整理投资逻辑', 'AI 正在阅读你的材料，通常需要 20–60 秒。');
    try {
      const r = await api(`/api/companies/${company.id}/thesis/generate`, { method: 'POST', body: { notes } });
      hideOverlay();
      if (r.error) return toast(r.error);
      const host = document.getElementById('bs-draft');
      const extras = r.extras || {};
      host.innerHTML = `
        ${r.demo ? '<div class="notice">以下为演示模式生成的示例草稿，用于熟悉流程；接入真实大模型后将根据你的材料生成。</div>' : '<div class="notice">以下是 AI 整理的草稿。请逐条检查、修改，确认后才会成为你的投资逻辑 V1。</div>'}
        ${(extras.factsUsed && extras.factsUsed.length) || (extras.aiInferences && extras.aiInferences.length) ? `
          <div class="extras-box">
            ${extras.factsUsed && extras.factsUsed.length ? `<div class="micro-label">来自材料的事实</div><ul class="dot-list" style="margin-bottom:14px">${extras.factsUsed.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
            ${extras.aiInferences && extras.aiInferences.length ? `<div class="micro-label">属于 AI 推断（需你确认）</div><ul class="dot-list">${extras.aiInferences.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
          </div>` : ''}
        <div id="bs-editor"></div>`;
      buildCardEditor(document.getElementById('bs-editor'), r.draft, (card) => confirmV1(card), '确认无误，建立投资逻辑 V1');
      host.scrollIntoView({ behavior: 'smooth' });
    } catch (e) {
      hideOverlay();
      toast(e.message);
    }
  };

  async function confirmV1(card) {
    try {
      const r = await api(`/api/companies/${company.id}/thesis`, { method: 'POST', body: { card, asOfDate: company.asOfDate } });
      toast('投资逻辑 ' + r.version + ' 已确认。下一步：加入一份材料');
      location.hash = '#/company/' + company.id + '/materials';
    } catch (e) { toast(e.message); }
  }
}

/* ---------------------------------------------------------------- 材料 Tab */

const MATERIAL_TYPES = ['财报', '业绩会纪要', '公司公告', '研究材料', '其他'];

function renderMaterialsTab(company, body, justSaved) {
  const hasThesis = company.theses.length > 0;
  const rows = company.materials.slice().reverse().map((m) => {
    const usedInDiffs = company.diffs.filter((d) => d.materialId === m.id).length;
    return `
    <div class="material-row">
      <div>
        <div class="material-title">${esc(m.title)}</div>
        <div class="material-excerpt">${esc((m.content || '').replace(/\s+/g, ' ').slice(0, 90))}…</div>
      </div>
      <div class="cell"><span class="chip">${esc(m.type)}</span></div>
      <div class="cell mono">${fmtDate(m.publishDate) || '—'}${usedInDiffs ? `<br><span style="color:var(--gray-400)">已生成 ${usedInDiffs} 份报告</span>` : ''}</div>
      <div class="btn-row" style="justify-content:flex-end">
        <button class="btn-ghost btn-sm" data-diff="${m.id}" ${hasThesis ? '' : 'disabled'}>生成变更报告</button>
        <button class="link-btn" data-del="${m.id}">删除</button>
      </div>
    </div>`;
  }).join('');

  body.innerHTML = `
    ${!hasThesis ? '<div class="notice">先在「投资逻辑」页确认一版投资逻辑，材料才能用于生成变更报告。</div>' : ''}
    ${justSaved && hasThesis ? `
      <div class="next-bar">
        <span>已保存《${esc(justSaved.title)}》。下一步：让 AI 比对它与你投资逻辑的变化。</span>
        <button class="btn btn-sm" id="m-gen-now">立即生成变更报告 →</button>
      </div>` : ''}
    <div class="list-head">
      <h2 class="section-title">研究材料</h2>
      <button class="btn btn-sm" id="m-add-toggle">＋ 加入新材料</button>
    </div>
    <div id="m-form" style="display:none">
      <div class="panel">
        <div class="form-grid">
          <div class="field"><label>材料标题</label><input class="input" id="m-title" placeholder="例如：2026 财年 Q1 业绩公告"></div>
          <div class="field"><label>类型</label><select class="select" id="m-type">${MATERIAL_TYPES.map((t) => `<option>${t}</option>`).join('')}</select></div>
          <div class="field"><label>发布日期</label><input class="input" type="date" id="m-date"></div>
          <div class="field"><label>从文件导入（.txt / .md）</label><input class="input" type="file" id="m-file" accept=".txt,.md,.text" style="padding:8px 14px"></div>
          <div class="field full"><label>材料全文</label>
            <textarea class="textarea" id="m-content" placeholder="把材料全文粘贴到这里。PDF 财报请先用阅读器全选复制文本后粘贴。"></textarea>
            <div class="field-hint">关键事实应尽量来自一手资料：财报、公告、业绩会、监管文件。二手观点请标注作者。</div>
          </div>
        </div>
        <div class="btn-row"><button class="btn" id="m-save">保存材料</button></div>
      </div>
    </div>
    <div class="company-list" style="margin-top:16px">
      ${rows || `<div class="empty"><div class="empty-title">还没有材料</div>${hasThesis ? '逻辑卡已就位。每次财报发布后，把全文粘贴到这里。' : '每次财报发布后，把全文粘贴到这里，再去生成一份变更报告。'}</div>`}
    </div>`;

  const genNow = document.getElementById('m-gen-now');
  if (genNow && justSaved) genNow.onclick = () => generateDiff(company.id, justSaved.id);

  document.getElementById('m-add-toggle').onclick = () => {
    const f = document.getElementById('m-form');
    f.style.display = f.style.display === 'none' ? 'block' : 'none';
  };
  document.getElementById('m-file').onchange = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      document.getElementById('m-content').value = reader.result;
      if (!document.getElementById('m-title').value) {
        document.getElementById('m-title').value = file.name.replace(/\.(txt|md|text)$/i, '');
      }
      toast('已读入文件：' + file.name);
    };
    reader.readAsText(file);
  };
  document.getElementById('m-save').onclick = async () => {
    const payload = {
      title: document.getElementById('m-title').value.trim(),
      type: document.getElementById('m-type').value,
      publishDate: document.getElementById('m-date').value,
      content: document.getElementById('m-content').value,
    };
    if (!payload.title) return toast('请填写材料标题');
    if (!payload.content.trim()) return toast('请粘贴材料全文');
    try {
      const r = await api(`/api/companies/${company.id}/materials`, { method: 'POST', body: payload });
      renderMaterialsTab(await api('/api/companies/' + company.id), body, r.material || { id: null, title: payload.title });
    } catch (e) { toast(e.message); }
  };

  body.querySelectorAll('[data-del]').forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm('确定删除这份材料吗？已生成的变更报告会保留。')) return;
      try {
        await api(`/api/companies/${company.id}/materials/${btn.dataset.del}`, { method: 'DELETE' });
        renderMaterialsTab(await api('/api/companies/' + company.id), body);
      } catch (e) { toast(e.message); }
    };
  });

  body.querySelectorAll('[data-diff]').forEach((btn) => {
    btn.onclick = () => generateDiff(company.id, btn.dataset.diff);
  });
}

async function generateDiff(companyId, materialId) {
  showOverlay('正在生成 Thesis Diff', 'AI 正在读取材料、提取证据并逐条比对你的投资逻辑，通常需要 30–90 秒。');
  try {
    const r = await api(`/api/companies/${companyId}/diffs`, { method: 'POST', body: { materialId } });
    hideOverlay();
    if (r.error) return toast(r.error, 5000);
    location.hash = `#/company/${companyId}/diffs/${r.diffId}`;
  } catch (e) {
    hideOverlay();
    toast(e.message, 5000);
  }
}

/* ---------------------------------------------------------------- 变更报告 Tab */

function renderDiffsTab(company, body) {
  const hasThesis = company.theses.length > 0;
  const rows = company.diffs.slice().reverse().map((d) => `
    <div class="company-row" style="grid-template-columns:1.6fr 1fr 1fr 0.8fr 24px" onclick="location.hash='#/company/${company.id}/diffs/${d.id}'">
      <div class="company-name">《${esc(d.materialTitle)}》${d.demo ? '<span class="tag-demo">演示</span>' : ''}</div>
      <div class="cell mono hide-sm">${fmtTime(d.createdAt)}</div>
      <div class="cell hide-sm"><span class="${impactClass(d.content.overall)}">${esc(d.content.overall)}</span></div>
      <div class="cell hide-sm">${d.decision ? '<span class="chip chip-dark">已审阅</span>' : '<span class="chip">待审阅</span>'}</div>
      <div class="cell"><span class="arrow">→</span></div>
    </div>`).join('');

  body.innerHTML = `
    <div class="list-head">
      <h2 class="section-title">投资逻辑变更报告</h2>
    </div>
    <div class="company-list">
      ${rows || '<div class="empty"><div class="empty-title">还没有变更报告</div>到「材料」页加入一份新财报或纪要，然后点击「生成变更报告」。</div>'}
    </div>`;
}

/* ---------------------------------------------------------------- Diff 详情 */

async function viewDiff(companyId, diffId) {
  app.innerHTML = '<div class="view"><div class="empty">加载中…</div></div>';
  let company;
  try {
    company = await api('/api/companies/' + companyId);
  } catch (e) {
    app.innerHTML = `<div class="view"><div class="empty">${esc(e.message)}</div></div>`;
    return;
  }
  const diff = company.diffs.find((d) => d.id === diffId);
  if (!diff) {
    app.innerHTML = '<div class="view"><div class="empty">报告不存在。</div></div>';
    return;
  }
  const c = diff.content;

  const assumptionRows = (c.assumptionChanges || []).map((a) => `
    <div class="diff-assumption">
      <div class="diff-assumption-top">
        <div class="diff-assumption-name"><span class="aid">${esc(a.assumptionId)}</span>${esc(a.assumption)}</div>
        <span class="${impactClass(a.impact)}">${esc(a.impact)}</span>
      </div>
      <dl class="diff-kv">
        <dt>原有判断</dt><dd>${esc(a.originalJudgment || '—')}</dd>
        <dt>新证据</dt><dd>${esc(a.newEvidence || '—')}</dd>
        <dt>置信度</dt><dd>${esc(a.confidence || '—')}</dd>
        <dt>另一种解释</dt><dd>${esc(a.alternativeExplanation || '—')}</dd>
      </dl>
      <div class="src-note">来源：${esc(a.source || '—')}</div>
    </div>`).join('');

  const mgmtRows = (c.managementWords || []).map((m) => `
    <div class="diff-assumption">
      <dl class="diff-kv">
        <dt>过去表态</dt><dd>${esc(m.pastStatement || '—')}</dd>
        <dt>本期行动</dt><dd>${esc(m.currentAction || '—')}</dd>
        <dt>是否一致</dt><dd>${esc(m.consistent || '—')}</dd>
        <dt>说明</dt><dd>${esc(m.note || '—')}</dd>
      </dl>
      <div class="src-note">来源：${esc(m.source || '—')}</div>
    </div>`).join('');

  const sc = c.suggestedChanges || {};
  const scBlock = (label, arr) => arr && arr.length
    ? `<div class="field" style="margin-bottom:16px"><label>${label}</label><ul class="dot-list">${arr.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>`
    : '';

  const decisionChoices = [
    ['accept', '接受更新 —— 按 AI 建议生成新版本'],
    ['accept_modified', '修改后接受 —— 我先改一改，再生成新版本'],
    ['reject', '拒绝更新 —— 保留现有版本'],
    ['defer', '证据不足，暂不更新'],
    ['research_task', '创建后续研究任务'],
  ];

  app.innerHTML = `
    <div class="view">
      <div class="btn-row" style="margin-bottom:24px;justify-content:space-between">
        <a class="link-btn" href="#/company/${companyId}/diffs">← 返回报告列表</a>
        <span class="micro-label" style="margin:0">${esc(company.name)} · 基于 ${esc(diff.baseVersion)}</span>
      </div>

      <div class="diff-head">
        <div class="diff-overall-label">总体判断${diff.demo ? '<span class="tag-demo">演示生成</span>' : ''}</div>
        <div class="diff-overall">${esc(c.overall)}</div>
        <div class="diff-summary">${esc(c.summary || '')}</div>
        <div class="diff-meta">
          <span>材料：《${esc(diff.materialTitle)}》${diff.materialDate ? ' · ' + esc(diff.materialDate) : ''}</span>
          <span>生成于 ${fmtTime(diff.createdAt)}</span>
          <span>原版本 ${esc(diff.baseVersion)}</span>
        </div>
      </div>

      <div class="thesis-section">
        <div class="section-title">逐条假设变化</div>
        ${assumptionRows || '<div class="empty">无</div>'}
      </div>

      <div class="thesis-section">
        <div class="section-title">管理层「说了什么」与「做了什么」</div>
        ${mgmtRows || '<div class="empty">本期材料未涉及可比对的管理层表态。</div>'}
      </div>

      <div class="thesis-section">
        <div class="section-title">最强反方解释</div>
        <div class="counter-view">${esc(c.counterArgument || '—')}</div>
      </div>

      <div class="thesis-section">
        <div class="section-title">最值得继续验证的问题</div>
        <ol class="question-list">${(c.nextQuestions || []).map((q) => `<li>${esc(q)}</li>`).join('')}</ol>
      </div>

      <div class="thesis-section">
        <div class="section-title">建议的投资逻辑修改</div>
        ${scBlock('建议保留', sc.keep)}
        ${scBlock('建议修改', sc.modify)}
        ${scBlock('建议新增', sc.add)}
        ${scBlock('建议删除', sc.remove)}
        ${scBlock('仍然证据不足', sc.insufficient)}
      </div>

      <div id="decision-host"></div>
    </div>`;

  const host = document.getElementById('decision-host');

  if (diff.decision) {
    const label = (decisionChoices.find((x) => x[0] === diff.decision.choice) || [null, diff.decision.choice])[1];
    host.innerHTML = `
      <div class="decision-done">
        <div class="micro-label">你的决定 · ${fmtTime(diff.decision.decidedAt)}</div>
        <div style="font-weight:600">${esc(label)}</div>
        ${diff.decision.note ? `<div style="color:var(--gray-600);margin-top:6px">${esc(diff.decision.note)}</div>` : ''}
        <div style="margin-top:12px"><a class="link-btn" href="#/company/${companyId}">查看当前投资逻辑 →</a></div>
      </div>`;
    return;
  }

  host.innerHTML = `
    <div class="decision-panel">
      <div class="micro-label">最后一步</div>
      <div class="section-title" style="margin-bottom:4px">你的决定</div>
      <p style="font-size:13px;color:var(--gray-600)">AI 输出的是一份等待你审阅的「逻辑补丁」，不会自动覆盖你的投资逻辑。</p>
      <div class="radio-row">
        ${decisionChoices.map(([v, label]) => `<label><input type="radio" name="decision" value="${v}"><span>${label}</span></label>`).join('')}
      </div>
      <div class="field"><label>说明（可选）</label><textarea class="textarea textarea-sm" id="dc-note" placeholder="为什么做这个决定？记下来，日后复盘用。"></textarea></div>
      <div id="dc-editor" style="display:none">
        <div class="notice">下面是 AI 建议的新版本投资逻辑。修改后点击「以修改版生成新版本」，原版本仍完整保留。</div>
        <div id="dc-editor-inner"></div>
      </div>
      <div class="btn-row">
        <button class="btn" id="dc-submit" disabled>提交决定</button>
      </div>
    </div>`;

  let editorCardGetter = null;

  host.querySelectorAll('input[name=decision]').forEach((radio) => {
    radio.onchange = () => {
      document.getElementById('dc-submit').disabled = false;
      const editor = document.getElementById('dc-editor');
      if (radio.value === 'accept_modified') {
        editor.style.display = 'block';
        if (!editorCardGetter) {
          buildCardEditor(document.getElementById('dc-editor-inner'), c.revisedCard, () => {}, '仅暂存修改内容');
          // 复用编辑器的 DOM 读值：单独包一层 getter
          editorCardGetter = () => readEditorCard(document.getElementById('dc-editor-inner'));
          document.getElementById('dc-editor-inner').querySelector('#ed-submit').style.display = 'none';
        }
        document.getElementById('dc-submit').textContent = '以修改版生成新版本';
      } else {
        editor.style.display = 'none';
        document.getElementById('dc-submit').textContent =
          radio.value === 'accept' ? '接受并生成新版本' : '提交决定';
      }
    };
  });

  document.getElementById('dc-submit').onclick = async () => {
    const choice = host.querySelector('input[name=decision]:checked');
    if (!choice) return;
    const payload = { choice: choice.value, note: document.getElementById('dc-note').value.trim() };
    if (choice.value === 'accept_modified' && editorCardGetter) {
      payload.card = editorCardGetter();
      if (!payload.card.oneLiner) return toast('修改版缺少一句话投资逻辑');
    }
    try {
      const r = await api(`/api/companies/${companyId}/diffs/${diffId}/decision`, { method: 'POST', body: payload });
      toast(r.newVersion ? '已生成新版本 ' + r.newVersion : '决定已记录');
      viewDiff(companyId, diffId);
    } catch (e) { toast(e.message); }
  };
}

/** 从编辑器 DOM 读取当前卡片内容（与 buildCardEditor 的结构对应） */
function readEditorCard(root) {
  const assumptions = [];
  root.querySelectorAll('#ed-assumptions .assumption-edit').forEach((row, i) => {
    assumptions.push({
      id: 'A-' + String(i + 1).padStart(2, '0'),
      text: row.querySelector('.a-text').value.trim(),
      indicators: row.querySelector('.a-ind').value.split('\n').map((s) => s.trim()).filter(Boolean),
    });
  });
  return {
    oneLiner: root.querySelector('#ed-oneliner').value.trim(),
    assumptions: assumptions.filter((a) => a.text),
    falsifiers: root.querySelector('#ed-falsifiers').value.split('\n').map((s) => s.trim()).filter(Boolean),
    counterView: root.querySelector('#ed-counter').value.trim(),
    valuation: {
      method: root.querySelector('#ed-v-method').value.trim(),
      range: root.querySelector('#ed-v-range').value.trim(),
      implied: root.querySelector('#ed-v-implied').value.trim(),
      sensitive: root.querySelector('#ed-v-sensitive').value.trim(),
    },
    unknowns: root.querySelector('#ed-unknowns').value.split('\n').map((s) => s.trim()).filter(Boolean),
  };
}

/* ---------------------------------------------------------------- 设置 */

function viewSettings() {
  const s = state.settings;
  const providers = state.providers;

  const llmOptions = providers.llm.map((p) => `
    <div class="provider-option">
      <input type="radio" name="llm-provider" id="lp-${p.id}" value="${p.id}" ${s.llm.provider === p.id ? 'checked' : ''}>
      <label for="lp-${p.id}">
        <div class="p-name">${esc(p.name)}</div>
        <div class="p-hint">${esc(p.hint)}</div>
      </label>
    </div>`).join('');

  const finOptions = providers.finance.map((p) => `
    <div class="provider-option">
      <input type="radio" name="fin-source" id="fp-${p.id}" value="${p.id}" ${s.finance.source === p.id ? 'checked' : ''}>
      <label for="fp-${p.id}">
        <div class="p-name">${esc(p.name)}</div>
        <div class="p-hint">${esc(p.hint)}</div>
      </label>
    </div>`).join('');

  app.innerHTML = `
    <div class="view">
      <div class="micro-label">设置</div>
      <h1 class="page-title">大模型与数据源</h1>
      <p class="page-desc">密钥只保存在你自己电脑的 <span style="font-family:var(--mono);font-size:12px">ThesisOS/src/data/settings.json</span> 文件中，不会上传到任何第三方。没有密钥也可以先用演示模式熟悉产品。</p>

      <div class="settings-grid">
        <div class="panel">
          <div class="section-title">大模型</div>
          <div class="micro-label">选择服务商</div>
          <div class="provider-grid provider-grid-llm">${llmOptions}</div>
          <div id="llm-fields">
            <div class="key-row">
              <div class="field">
                <label>API Key</label>
                <input class="input" type="password" id="llm-key" autocomplete="off">
                <div class="field-hint" id="llm-key-hint"></div>
              </div>
            </div>
            <div class="form-grid" style="margin-top:20px">
              <div class="field">
                <label>模型</label>
                <input class="input" id="llm-model" list="model-options" value="${esc(s.llm.model)}">
                <datalist id="model-options"></datalist>
                <div class="model-datalist-note">可直接输入其他模型名。</div>
              </div>
              <div class="field">
                <label>接口地址（Base URL）</label>
                <input class="input" id="llm-baseurl" value="${esc(s.llm.baseUrl)}">
              </div>
            </div>
            <div class="btn-row">
              <button class="btn-ghost btn-sm" id="llm-test">测试连接</button>
              <button class="link-btn" id="llm-clear-key" type="button">清除已保存的密钥</button>
            </div>
            <div class="test-result" id="llm-test-result"></div>
          </div>
        </div>

        <div class="panel">
          <div class="section-title">金融数据库</div>
          <div class="micro-label">选择行情数据源</div>
          <div class="provider-grid">${finOptions}</div>
          <div id="fin-fields">
            <div class="key-row" id="fin-token-row" style="display:none">
              <div class="field">
                <label id="fin-token-label">Token</label>
                <input class="input" type="password" id="fin-token" autocomplete="off">
                <div class="field-hint" id="fin-token-hint"></div>
              </div>
            </div>
            <div class="btn-row" style="margin-top:20px">
              <button class="btn-ghost btn-sm" id="fin-test">测试连接</button>
              <button class="link-btn" id="fin-clear-token" type="button">清除已保存的 Token</button>
            </div>
            <div class="test-result" id="fin-test-result"></div>
          </div>
        </div>
      </div>

      <div class="btn-row" style="margin-top:8px">
        <button class="btn" id="settings-save">保存设置</button>
        <a class="link-btn" href="#/">返回工作台</a>
      </div>
    </div>`;

  /* ---- 大模型表单逻辑 ---- */
  const keyInput = document.getElementById('llm-key');
  const baseUrlInput = document.getElementById('llm-baseurl');
  const modelInput = document.getElementById('llm-model');
  const modelOptions = document.getElementById('model-options');
  const keyHint = document.getElementById('llm-key-hint');

  function currentLlmProvider() {
    const r = document.querySelector('input[name=llm-provider]:checked');
    return providers.llm.find((p) => p.id === (r ? r.value : 'demo')) || providers.llm[0];
  }

  function refreshLlmForm(keepModel) {
    const p = currentLlmProvider();
    const isDemo = p.id === 'demo';
    document.getElementById('llm-fields').style.opacity = isDemo ? '0.45' : '1';
    document.getElementById('llm-fields').style.pointerEvents = isDemo ? 'none' : 'auto';
    if (p.id !== 'custom' && p.baseUrl) baseUrlInput.value = p.baseUrl;
    if (p.id === 'custom' && !baseUrlInput.value) baseUrlInput.placeholder = 'https://你的接口地址/v1';
    modelOptions.innerHTML = (p.models || []).map((m) => `<option value="${esc(m)}">`).join('');
    if (!keepModel && p.models && p.models.length) modelInput.value = p.models[0];
    keyInput.placeholder = state.settings.llm.apiKeySet
      ? `已保存（尾号 ${state.settings.llm.apiKeyTail}），输入以更换`
      : '粘贴你的 API Key';
    keyHint.innerHTML = p.keyUrl ? `还没有密钥？到官方平台申请：<a href="${esc(p.keyUrl)}" target="_blank" rel="noopener" style="text-decoration:underline">${esc(p.keyUrl.replace(/^https?:\/\//, ''))}</a>` : '';
  }

  document.querySelectorAll('input[name=llm-provider]').forEach((r) => {
    r.onchange = () => refreshLlmForm(false);
  });
  refreshLlmForm(true);
  if (!state.settings.llm.model && currentLlmProvider().models.length) {
    modelInput.value = currentLlmProvider().models[0];
  }

  document.getElementById('llm-test').onclick = async () => {
    const result = document.getElementById('llm-test-result');
    result.className = 'test-result';
    result.style.display = 'block';
    result.textContent = '正在测试…';
    const r = await api('/api/llm/test', {
      method: 'POST',
      body: { llm: { provider: currentLlmProvider().id, baseUrl: baseUrlInput.value.trim(), model: modelInput.value.trim(), apiKey: keyInput.value.trim() } },
    });
    result.className = 'test-result ' + (r.ok ? 'ok' : 'err');
    result.textContent = r.ok ? r.message : r.error;
  };

  document.getElementById('llm-clear-key').onclick = async () => {
    await api('/api/settings', { method: 'PUT', body: { llm: { clearKey: true } } });
    state.settings = (await api('/api/settings'));
    keyInput.placeholder = '粘贴你的 API Key';
    toast('已清除保存的密钥');
  };

  /* ---- 数据源表单逻辑 ---- */
  const tokenRow = document.getElementById('fin-token-row');
  const tokenInput = document.getElementById('fin-token');
  const tokenLabel = document.getElementById('fin-token-label');
  const tokenHint = document.getElementById('fin-token-hint');

  function currentFinSource() {
    const r = document.querySelector('input[name=fin-source]:checked');
    return providers.finance.find((p) => p.id === (r ? r.value : 'demo')) || providers.finance[0];
  }

  function finTokenState(id) {
    const t = (state.settings.finance.tokens || {})[id];
    return t || { set: false, tail: '' };
  }

  function refreshFinForm() {
    const p = currentFinSource();
    tokenRow.style.display = p.needsKey ? 'flex' : 'none';
    const label = p.keyLabel || 'Token';
    tokenLabel.textContent = label;
    const saved = finTokenState(p.id);
    tokenInput.placeholder = saved.set
      ? `已保存（尾号 ${saved.tail}），输入以更换`
      : `粘贴你的 ${label}`;
    const hints = [];
    if (p.keyHint) hints.push(esc(p.keyHint));
    if (p.keyUrl) hints.push(`获取地址：<a href="${esc(p.keyUrl)}" target="_blank" rel="noopener" style="text-decoration:underline">${esc(p.keyUrl.replace(/^https?:\/\//, ''))}</a>`);
    tokenHint.innerHTML = hints.join('<br>');
  }

  document.querySelectorAll('input[name=fin-source]').forEach((r) => {
    r.onchange = refreshFinForm;
  });
  refreshFinForm();

  document.getElementById('fin-test').onclick = async () => {
    const result = document.getElementById('fin-test-result');
    result.className = 'test-result';
    result.style.display = 'block';
    result.textContent = '正在测试…';
    const r = await api('/api/finance/test', {
      method: 'POST',
      body: { finance: { source: currentFinSource().id, token: tokenInput.value.trim() } },
    });
    result.className = 'test-result ' + (r.ok ? 'ok' : 'err');
    result.textContent = r.ok ? r.message : r.error;
  };

  document.getElementById('fin-clear-token').onclick = async () => {
    const p = currentFinSource();
    await api('/api/settings', { method: 'PUT', body: { finance: { clearToken: true, tokenFor: p.id } } });
    state.settings = (await api('/api/settings'));
    tokenInput.placeholder = `粘贴你的 ${p.keyLabel || 'Token'}`;
    toast('已清除保存的密钥');
  };

  /* ---- 保存 ---- */
  document.getElementById('settings-save').onclick = async () => {
    const finSource = currentFinSource();
    const body = {
      llm: {
        provider: currentLlmProvider().id,
        baseUrl: baseUrlInput.value.trim(),
        model: modelInput.value.trim(),
      },
      finance: { source: finSource.id, tokenFor: finSource.id },
    };
    if (keyInput.value.trim()) body.llm.apiKey = keyInput.value.trim();
    if (tokenInput.value.trim()) body.finance.token = tokenInput.value.trim();
    try {
      const r = await api('/api/settings', { method: 'PUT', body });
      state.settings = r.settings;
      keyInput.value = '';
      tokenInput.value = '';
      refreshLlmForm(true);
      refreshFinForm();
      renderDemoBanner();
      toast('设置已保存');
    } catch (e) { toast(e.message); }
  };
}

/* ---------------------------------------------------------------- 启动 */

boot();
