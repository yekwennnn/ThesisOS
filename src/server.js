/**
 * ThesisOS · ThesisDiff V0 原型服务器
 * 零依赖：仅使用 Node.js 内置模块（需要 Node 18+）。
 *
 * 功能：
 *  - 托管 public/ 下的前端页面
 *  - 本地 JSON 文件存储（data/ 目录，含公司与投资逻辑版本、设置）
 *  - 大模型 API 代理（OpenAI 兼容协议：Kimi / DeepSeek / OpenAI / 通义千问 / 智谱 / 豆包 / 自定义）
 *  - 金融数据代理（东方财富 / Tushare Pro / 内置演示数据）
 *  - 内置「演示模式」：没有任何 API Key 也能完整体验产品流程
 */
'use strict';

const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { exec, execFile } = require('child_process');

const ROOT = __dirname;
const PUB_DIR = path.join(ROOT, 'public');
const DATA_DIR = path.join(ROOT, 'data');
const DB_FILE = path.join(DATA_DIR, 'db.json');
const SETTINGS_FILE = path.join(DATA_DIR, 'settings.json');
const PROMPTS_DIR = path.join(ROOT, 'prompts');
const WIND_SKILL_DIR = path.join(os.homedir(), '.agents', 'skills', 'wind-mcp-skill');

const MAX_BODY = 25 * 1024 * 1024; // 25MB，容纳长篇财报文本
const MATERIAL_MAX_CHARS = 60000;    // 送入大模型的材料截断上限

/* ---------------------------------------------------------------- 目录与存储 */

function ensureDataDir() {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
}

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return fallback;
  }
}

function writeJson(file, obj) {
  const tmp = file + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2), 'utf8');
  fs.renameSync(tmp, file);
}

function loadDb() {
  return readJson(DB_FILE, { companies: [] });
}

function saveDb(db) {
  writeJson(DB_FILE, db);
}

function saveSettings(s) {
  writeJson(SETTINGS_FILE, s);
}

function defaultSettings() {
  return {
    llm: { provider: 'demo', apiKey: '', baseUrl: '', model: '' },
    finance: { source: 'demo', tokens: {} },
  };
}

/** 读取设置并做轻量迁移（旧版 finance.token → finance.tokens.tushare） */
function loadSettings() {
  const s = readJson(SETTINGS_FILE, defaultSettings());
  if (!s.llm) s.llm = defaultSettings().llm;
  if (!s.finance) s.finance = defaultSettings().finance;
  if (!s.finance.tokens || typeof s.finance.tokens !== 'object') s.finance.tokens = {};
  if (typeof s.finance.token === 'string' && s.finance.token) {
    s.finance.tokens.tushare = s.finance.token;
    delete s.finance.token;
  } else {
    delete s.finance.token;
  }
  return s;
}

function uid(prefix) {
  return prefix + '_' + crypto.randomBytes(6).toString('hex');
}

/* ---------------------------------------------------------------- 服务目录 */

const LLM_PROVIDERS = [
  {
    id: 'demo',
    name: '演示模式',
    hint: '无需密钥，内置模拟 AI，用来先熟悉产品',
    baseUrl: '',
    models: ['内置模拟模型'],
    needsKey: false,
    keyUrl: '',
  },
  {
    id: 'kimi',
    name: 'Kimi（月之暗面）',
    hint: '中文长文本表现好，适合读财报长文',
    baseUrl: 'https://api.moonshot.cn/v1',
    models: ['kimi-k2-0905-preview', 'kimi-k2-turbo-preview', 'moonshot-v1-32k', 'moonshot-v1-128k'],
    needsKey: true,
    keyUrl: 'https://platform.moonshot.cn/console/api-keys',
  },
  {
    id: 'kimi-coding',
    name: 'Kimi 订阅（会员）',
    hint: '用 Kimi 会员订阅额度，到 Kimi Code 控制台创建 API Key',
    baseUrl: 'https://api.kimi.com/coding/v1',
    models: ['kimi-for-coding', 'k3-256k', 'k3', 'kimi-for-coding-highspeed'],
    needsKey: true,
    keyUrl: 'https://www.kimi.com/code/console',
    hidden: true, // 不在服务商网格中单独占格，通过 Kimi 卡片下的「使用方式」切换
  },
  {
    id: 'glm',
    name: '智谱 GLM',
    hint: '智谱开放平台',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    models: ['glm-4-plus', 'glm-4-air', 'glm-4-flash'],
    needsKey: true,
    keyUrl: 'https://open.bigmodel.cn/usercenter/apikeys',
  },
  {
    id: 'deepseek',
    name: 'DeepSeek（深度求索）',
    hint: '推理能力强、价格低',
    baseUrl: 'https://api.deepseek.com',
    models: ['deepseek-chat', 'deepseek-reasoner'],
    needsKey: true,
    keyUrl: 'https://platform.deepseek.com/api_keys',
  },
  {
    id: 'openai',
    name: 'OpenAI',
    hint: 'GPT 系列模型',
    baseUrl: 'https://api.openai.com/v1',
    models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1', 'gpt-4.1-mini'],
    needsKey: true,
    keyUrl: 'https://platform.openai.com/api-keys',
  },
  {
    id: 'qwen',
    name: '通义千问（阿里）',
    hint: '阿里云百炼平台',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    models: ['qwen-plus', 'qwen-max', 'qwen-turbo'],
    needsKey: true,
    keyUrl: 'https://bailian.console.aliyun.com/?apiKey=1#/api-key',
  },
  {
    id: 'doubao',
    name: '豆包（火山引擎）',
    hint: '模型一栏填写推理接入点 ID（ep- 开头）或模型名',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    models: [],
    needsKey: true,
    keyUrl: 'https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey',
  },
  {
    id: 'hunyuan',
    name: '混元（腾讯）',
    hint: '腾讯混元大模型',
    baseUrl: 'https://api.hunyuan.cloud.tencent.com/v1',
    models: ['hunyuan-turbos-latest', 'hunyuan-t1-latest', 'hunyuan-lite'],
    needsKey: true,
    keyUrl: 'https://console.cloud.tencent.com/hunyuan/api-key',
  },
  {
    id: 'minimax',
    name: 'MiniMax',
    hint: 'MiniMax 开放平台',
    baseUrl: 'https://api.minimaxi.com/v1',
    models: ['MiniMax-M2', 'MiniMax-M1', 'abab6.5s-chat'],
    needsKey: true,
    keyUrl: 'https://platform.minimaxi.com/user-center/basic-information/interface-key',
  },
  {
    id: 'custom',
    name: '自定义（OpenAI 兼容）',
    hint: '任何兼容 /chat/completions 协议的接口',
    baseUrl: '',
    models: [],
    needsKey: true,
    keyUrl: '',
  },
];

const FINANCE_SOURCES = [
  {
    id: 'demo',
    name: '演示数据',
    hint: '内置虚构行情，仅用于熟悉产品，不代表真实价格',
    needsKey: false,
    keyUrl: '',
  },
  {
    id: 'eastmoney',
    name: '东方财富',
    hint: '免费公开行情接口，无需密钥，支持 A 股 / 港股 / 美股',
    needsKey: false,
    keyUrl: '',
  },
  {
    id: 'tushare',
    name: 'Tushare Pro',
    hint: '需 Token；支持 A 股与港股日线',
    needsKey: true,
    keyUrl: 'https://tushare.pro/user/token',
    keyLabel: 'Tushare Token',
    keyHint: '',
  },
  {
    id: 'wind',
    name: 'Wind（万得）',
    hint: '专业金融数据，需 Wind API Key（本机已配置过可留空）',
    needsKey: true,
    keyUrl: 'https://aifinmarket.wind.com.cn/#/user/overview',
    keyLabel: 'Wind API Key',
    keyHint: '到万得开发者中心（登录后在 Overview 页）获取 API Key。若本机 wind-mcp-skill 已配置过密钥，可留空。',
  },
];

/* ---------------------------------------------------------------- 工具 */

function send(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

function sendError(res, code, message, extra) {
  send(res, code, Object.assign({ error: message }, extra || {}));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new Error('请求体过大'));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      if (!chunks.length) return resolve({});
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch {
        reject(new Error('请求不是合法 JSON'));
      }
    });
    req.on('error', reject);
  });
}

function fillTemplate(tpl, vars) {
  return tpl.replace(/\{\{([A-Z_0-9]+)\}\}/g, (m, k) => (vars[k] != null ? String(vars[k]) : ''));
}

function loadPrompt(name) {
  try {
    return fs.readFileSync(path.join(PROMPTS_DIR, name), 'utf8');
  } catch {
    return '';
  }
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

/** 从模型输出中提取 JSON 对象（容忍 ```json 包裹与前后杂讯） */
function extractJson(text) {
  if (!text) return null;
  let t = String(text).trim();
  const fence = t.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fence) t = fence[1].trim();
  const start = t.indexOf('{');
  const end = t.lastIndexOf('}');
  if (start === -1 || end === -1 || end <= start) return null;
  try {
    return JSON.parse(t.slice(start, end + 1));
  } catch {
    return null;
  }
}

/* ---------------------------------------------------------------- 大模型调用 */

async function llmComplete(cfg, messages, wantJson) {
  if (!cfg || cfg.provider === 'demo') {
    throw Object.assign(new Error('演示模式不应进入真实调用'), { code: 'DEMO' });
  }
  const provider = LLM_PROVIDERS.find((p) => p.id === cfg.provider);
  const baseUrl = (cfg.baseUrl || (provider && provider.baseUrl) || '').replace(/\/+$/, '');
  if (!baseUrl) throw new Error('未配置接口地址（Base URL）');
  if (!cfg.apiKey) throw new Error('未填写 API Key');
  if (!cfg.model) throw new Error('未填写模型名称');

  const url = baseUrl + '/chat/completions';
  const payload = {
    model: cfg.model,
    messages,
    temperature: 0.2,
    stream: false,
  };
  if (wantJson) payload.response_format = { type: 'json_object' };

  async function attempt(body) {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + cfg.apiKey,
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(120000),
    });
    const text = await resp.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error('接口返回了无法解析的内容（HTTP ' + resp.status + '）');
    }
    if (!resp.ok) {
      const msg =
        (data && data.error && (data.error.message || data.error.msg)) ||
        data.message ||
        'HTTP ' + resp.status;
      const err = new Error('模型接口报错：' + msg);
      err.status = resp.status;
      throw err;
    }
    return data;
  }

  let data;
  try {
    data = await attempt(payload);
  } catch (e) {
    // 部分兼容接口不支持 response_format，降级重试一次
    if (wantJson && e.status && e.status >= 400 && e.status < 500) {
      delete payload.response_format;
      data = await attempt(payload);
    } else {
      throw e;
    }
  }
  const content =
    data && data.choices && data.choices[0] && data.choices[0].message
      ? data.choices[0].message.content
      : '';
  return { content, usage: data.usage || null };
}

/* ---------------------------------------------------------------- 演示模式模拟生成 */

function mockBootstrap(company) {
  const name = company.name || '该公司';
  return {
    oneLiner: `我认为${name}凭借核心业务建立的规模与履约优势，有望在行业竞争回归理性后持续提升自由现金流与股东回报。`,
    assumptions: [
      { id: 'A-01', text: '用户持续重视可靠履约与商品质量，而非单纯低价', indicators: ['活跃用户与购买频次', '用户留存与复购率'] },
      { id: 'A-02', text: '履约与规模优势可以长期转化为效率与成本优势', indicators: ['履约费用率', '单位经济模型'] },
      { id: 'A-03', text: '行业不会长期回到极端价格补贴状态', indicators: ['行业补贴强度', '市场份额变化'] },
      { id: 'A-04', text: '核心业务能够持续产生自由现金流', indicators: ['自由现金流', '资本开支'] },
      { id: 'A-05', text: '管理层保持理性的资本配置', indicators: ['回购与分红', '新业务投入回报', '股权稀释'] },
    ],
    falsifiers: [
      '用户增长长期依赖高额补贴，补贴退坡后留存恶化',
      '履约优势无法形成用户留存或复购',
      '核心业务自由现金流出现结构性恶化',
      '公司通过持续股权融资掩盖经营问题',
    ],
    counterView: '公司的服务与履约优势可能只能形成竞争门槛，却无法形成定价权，最终只能长期维持低利润率经营。',
    valuation: { method: '未提供（演示）', range: '未提供（演示）', implied: '未提供（演示）', sensitive: '未提供（演示）' },
    unknowns: [
      '利润率改善来自长期效率，还是阶段性费用收缩？',
      '新业务投入是否有明确的资本回报目标与退出条件？',
    ],
    factsUsed: ['演示模式：未读取真实材料，以上内容为模板示例'],
    aiInferences: ['全部内容均为 AI 演示推断，等待用户确认'],
  };
}

function mockDiff(company, material, card) {
  const title = (material && material.title) || '新材料';
  const date = (material && material.publishDate) || today();
  const base = card || mockBootstrap(company);
  const changes = (base.assumptions || []).slice(0, 5).map((a, i) => ({
    assumptionId: a.id || 'A-0' + (i + 1),
    assumption: a.text,
    originalJudgment: '作为关键假设持续跟踪',
    newEvidence: `《${title}》中与本假设相关的表述（演示模式未真实读取材料）`,
    impact: i === 1 ? '小幅增强' : i === 3 ? '小幅削弱' : '基本不变',
    confidence: i === 1 ? '中' : '低',
    alternativeExplanation: '演示模式：真实分析需要接入大模型后生成',
    source: `《${title}》${date}`,
  }));
  return {
    overall: '基本不变',
    summary: `《${title}》未显著改变对${company.name || '该公司'}的核心投资逻辑；个别假设有小幅边际变化。（演示内容）`,
    assumptionChanges: changes,
    managementWords: [
      {
        pastStatement: '管理层此前强调控制投入、关注股东回报',
        currentAction: '本期表述与行动的一致性需要在接入真实模型后核对',
        consistent: '无法确认',
        note: '演示模式未真实比对',
        source: `《${title}》${date}`,
      },
    ],
    counterArgument: '如果行业重新进入补贴竞争，目前呈现的效率改善可能无法维持，利润率将再度承压。（演示内容）',
    nextQuestions: [
      '效率改善中有多少来自长期结构性因素，多少来自阶段性收缩？',
      '新业务投入是否存在明确的回报目标与退出条件？',
      '用户增长是否同时伴随留存与购买频次改善？',
    ],
    suggestedChanges: {
      keep: ['核心投资逻辑与大部分关键假设'],
      modify: ['小幅上调对履约效率改善持续性的置信度（演示）'],
      add: [],
      remove: [],
      insufficient: ['新业务投入的回报约束仍缺乏证据（演示）'],
    },
    revisedCard: {
      oneLiner: base.oneLiner,
      assumptions: base.assumptions,
      falsifiers: base.falsifiers,
      counterView: base.counterView,
      valuation: base.valuation,
      unknowns: base.unknowns,
    },
  };
}

/* ---------------------------------------------------------------- 行情代理 */

const EM_FIELDS = 'f43,f57,f58,f60,f107,f170,f116,f117,f162,f167,f59';

function emSecidCandidates(code, market) {
  const c = String(code).trim().toUpperCase();
  if (market === 'HK' || /^\d{4,5}$/.test(c) && market === 'HK') return ['116.' + c.padStart(5, '0')];
  if (market === 'US') return ['105.' + c, '106.' + c, '107.' + c];
  // A 股：6/9 开头为沪，0/2/3 为深，4/8 为北交所
  if (/^[69]/.test(c)) return ['1.' + c];
  if (/^[023]/.test(c)) return ['0.' + c];
  if (/^[48]/.test(c)) return ['0.' + c, '1.' + c];
  return ['1.' + c, '0.' + c];
}

async function eastmoneyQuote(code, market) {
  const candidates = emSecidCandidates(code, market);
  let lastErr = null;
  for (const secid of candidates) {
    try {
      const url = `https://push2.eastmoney.com/api/qt/stock/get?secid=${encodeURIComponent(secid)}&fields=${EM_FIELDS}&invt=2&fltt=1`;
      const resp = await fetch(url, { signal: AbortSignal.timeout(8000) });
      const data = await resp.json();
      if (!data || !data.data || data.data.f43 == null || data.data.f43 === '-') continue;
      const d = data.data;
      const scale = Math.pow(10, d.f59 != null ? d.f59 : 2);
      return {
        name: d.f58 || '',
        code: d.f57 || code,
        price: d.f43 / scale,
        prevClose: d.f60 != null && d.f60 !== '-' ? d.f60 / scale : null,
        changePct: d.f170 != null && d.f170 !== '-' ? d.f170 / 100 : null,
        pe: d.f162 != null && d.f162 !== '-' ? d.f162 / 100 : null,
        pb: d.f167 != null && d.f167 !== '-' ? d.f167 / 100 : null,
        marketCap: d.f116 != null && d.f116 !== '-' ? d.f116 : null,
        source: '东方财富',
        asOf: new Date().toISOString(),
      };
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error('未找到该代码的行情，请检查代码与市场是否正确');
}

function tsCode(code, market) {
  const c = String(code).trim().toUpperCase();
  if (market === 'HK') return c.padStart(5, '0') + '.HK';
  if (/^[69]/.test(c)) return c + '.SH';
  if (/^4|^8/.test(c)) return c + '.BJ';
  return c + '.SZ';
}

async function tushareQuote(code, market, token) {
  if (!token) throw new Error('未填写 Tushare Token');
  const apiName = market === 'HK' ? 'hk_daily' : 'daily';
  if (market === 'US') throw new Error('Tushare 行情暂不支持美股，可改用东方财富');
  const end = today().replace(/-/g, '');
  const startDate = new Date(Date.now() - 20 * 86400000).toISOString().slice(0, 10).replace(/-/g, '');
  const resp = await fetch('https://api.tushare.pro', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      api_name: apiName,
      token,
      params: { ts_code: tsCode(code, market), start_date: startDate, end_date: end },
      fields: apiName === 'daily'
        ? 'ts_code,trade_date,close,pre_close,pct_chg'
        : 'ts_code,trade_date,close,pre_close,pct_chg',
    }),
    signal: AbortSignal.timeout(10000),
  });
  const data = await resp.json();
  if (data.code !== 0) throw new Error('Tushare 报错：' + (data.msg || '未知错误'));
  const fields = data.data.fields;
  const items = data.data.items;
  if (!items || !items.length) throw new Error('Tushare 未返回该代码近期的行情');
  const row = items[0];
  const get = (k) => row[fields.indexOf(k)];
  return {
    name: '',
    code: get('ts_code'),
    price: get('close'),
    prevClose: get('pre_close'),
    changePct: get('pct_chg'),
    pe: null,
    pb: null,
    marketCap: null,
    source: 'Tushare Pro（最近交易日 ' + get('trade_date') + '）',
    asOf: new Date().toISOString(),
  };
}

/** 确定性的演示行情：同一代码每次看到的“价格”一致，明显标注为虚构 */
function demoQuote(code, market, name) {
  const c = String(code || '0000');
  let h = 0;
  for (const ch of c) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const price = Math.round(((h % 90000) / 100 + 10) * 100) / 100;
  const pct = ((h % 700) - 350) / 100;
  return {
    name: name || '演示公司',
    code: c,
    price,
    prevClose: Math.round((price / (1 + pct / 100)) * 100) / 100,
    changePct: pct,
    pe: Math.round(((h % 4000) / 100 + 8) * 10) / 10,
    pb: Math.round(((h % 800) / 100 + 0.8) * 10) / 10,
    marketCap: null,
    source: '演示数据（虚构，仅供体验）',
    asOf: new Date().toISOString(),
    demo: true,
  };
}

/** Wind 标准代码：600519.SH / 00700.HK / AAPL.O */
function windcodeFor(code, market) {
  const c = String(code).trim().toUpperCase();
  if (market === 'HK') return c.padStart(5, '0') + '.HK';
  if (market === 'US') return c + '.O';
  if (/^[69]/.test(c)) return c + '.SH';
  if (/^[48]/.test(c)) return c + '.BJ';
  return c + '.SZ';
}

/** 通过本机 wind-mcp-skill CLI 取行情截面；apiKey 通过环境变量传入（本机已有全局/技能配置时以本机配置为准） */
function windQuote(code, market, apiKey) {
  return new Promise((resolve, reject) => {
    const windcode = windcodeFor(code, market);
    if (!fs.existsSync(path.join(WIND_SKILL_DIR, 'scripts', 'cli.mjs'))) {
      return reject(new Error('未找到本机 Wind 接口（wind-mcp-skill 未安装）'));
    }
    const params = JSON.stringify({
      windcode,
      indexes: '最新交易日,交易时间,中文简称,最新成交价,前收盘价,涨跌幅,市盈率(TTM),市净率(LF),总市值2',
    });
    const env = Object.assign({}, process.env);
    if (apiKey) env.WIND_API_KEY = apiKey;
    execFile(
      'node',
      ['scripts/cli.mjs', 'call', 'stock_data', 'get_stock_price_indicators', params],
      { cwd: WIND_SKILL_DIR, timeout: 45000, maxBuffer: 8 * 1024 * 1024, env },
      (err, stdout, stderr) => {
        let data = null;
        try { data = JSON.parse(stdout); } catch { /* 非 JSON 输出 */ }
        if (data && data.ok === false) {
          if (data.code === 'AUTH_ERROR') {
            return reject(new Error('Wind 接口未授权：请在「设置」中粘贴 Wind API Key（万得开发者中心获取）'));
          }
          return reject(new Error('Wind 报错：' + (data.message || data.code || '未知错误')));
        }
        if (!data || !data.content || !data.content[0] || !data.content[0].text) {
          const reason = err && err.killed
            ? '响应超时'
            : (String(stderr || (err && err.message) || '').slice(0, 120) || '未知原因');
          return reject(new Error('本机 Wind 接口不可用：' + reason));
        }
        try {
          const payload = JSON.parse(data.content[0].text);
          const cols = payload.data.columns.map((c) => c.name);
          const row = payload.data.rows && payload.data.rows[0];
          if (!row) return reject(new Error('Wind 未返回该代码的行情，请检查代码是否正确'));
          const get = (n) => { const i = cols.indexOf(n); return i >= 0 ? row[i] : null; };
          const num = (v) => { const n = parseFloat(v); return Number.isNaN(n) ? null : n; };
          resolve({
            name: get('中文简称') || '',
            code: get('Wind代码') || windcode,
            price: num(get('最新成交价')),
            prevClose: num(get('前收盘价')),
            changePct: num(get('涨跌幅')),
            pe: num(get('市盈率(TTM)')),
            pb: num(get('市净率(LF)')),
            marketCap: num(get('总市值2')),
            source: 'Wind（万得）',
            asOf: new Date().toISOString(),
          });
        } catch {
          reject(new Error('Wind 返回内容解析失败'));
        }
      }
    );
  });
}

/** 证券搜索（名称或代码 → 候选列表），走腾讯公开联想接口（GBK 编码、\u 转义） */
async function searchSecurities(q) {
  const url = 'https://smartbox.gtimg.cn/s3/?v=2&q=' + encodeURIComponent(q) + '&t=all';
  const resp = await fetch(url, { signal: AbortSignal.timeout(8000) });
  const buf = Buffer.from(await resp.arrayBuffer());
  const text = new TextDecoder('gbk').decode(buf);
  const m = text.match(/v_hint="([\s\S]*?)"/);
  if (!m || !m[1]) return [];
  const unesc = (s) => s.replace(/\\u([0-9a-fA-F]{4})/g, (_, h) => String.fromCharCode(parseInt(h, 16)));
  const seen = new Set();
  const items = [];
  for (const rec of m[1].split('^')) {
    const f = rec.split('~');
    if (f.length < 5) continue;
    const mkt = f[0];
    const rawCode = f[1];
    const name = unesc(f[2]);
    const type = f[4];
    if (!/^GP/.test(type)) continue; // 只要股票，过滤基金/指数等
    let market, code;
    if (mkt === 'sh' || mkt === 'sz') { market = 'A'; code = rawCode; }
    else if (mkt === 'hk') { market = 'HK'; code = rawCode; }
    else if (mkt === 'us') { market = 'US'; code = rawCode.replace(/\..*$/, '').toUpperCase(); }
    else continue;
    if (!name || !code) continue;
    const key = market + ':' + code;
    if (seen.has(key)) continue;
    seen.add(key);
    items.push({ name, code, market, typeName: { A: 'A股', HK: '港股', US: '美股' }[market] });
    if (items.length >= 8) break;
  }
  return items;
}

async function getQuote(finance, code, market, companyName) {
  const tokens = finance.tokens || {};
  if (finance.source === 'eastmoney') return eastmoneyQuote(code, market);
  if (finance.source === 'tushare') return tushareQuote(code, market, tokens.tushare || '');
  if (finance.source === 'wind') return windQuote(code, market, tokens.wind || '');
  return demoQuote(code, market, companyName);
}

/* ---------------------------------------------------------------- 版本与diff */

function currentThesis(company) {
  if (!company.theses || !company.theses.length) return null;
  return company.theses[company.theses.length - 1];
}

function nextVersionId(company) {
  const n = (company.theses || []).length;
  return 'V' + (n + 1);
}

function normalizeCard(card) {
  const c = card || {};
  const assumptions = Array.isArray(c.assumptions) ? c.assumptions : [];
  return {
    oneLiner: String(c.oneLiner || ''),
    assumptions: assumptions.map((a, i) => ({
      id: a.id || 'A-' + String(i + 1).padStart(2, '0'),
      text: String(a.text || ''),
      indicators: Array.isArray(a.indicators) ? a.indicators.map(String) : [],
    })),
    falsifiers: Array.isArray(c.falsifiers) ? c.falsifiers.map(String) : [],
    counterView: String(c.counterView || ''),
    valuation: {
      method: String((c.valuation && c.valuation.method) || ''),
      range: String((c.valuation && c.valuation.range) || ''),
      implied: String((c.valuation && c.valuation.implied) || ''),
      sensitive: String((c.valuation && c.valuation.sensitive) || ''),
    },
    unknowns: Array.isArray(c.unknowns) ? c.unknowns.map(String) : [],
  };
}

async function generateBootstrap(company, notes, settings) {
  const tpl = loadPrompt('thesis-bootstrap.md');
  const prompt = fillTemplate(tpl, {
    COMPANY_NAME: company.name,
    COMPANY_CODE: company.code,
    MARKET: company.market,
    NOTES: String(notes).slice(0, MATERIAL_MAX_CHARS),
  });
  if (settings.llm.provider === 'demo') {
    return { draft: mockBootstrap(company), demo: true };
  }
  const { content } = await llmComplete(settings.llm, [{ role: 'user', content: prompt }], true);
  const draft = extractJson(content);
  if (!draft) throw new Error('模型没有返回可解析的 JSON，请重试或更换模型');
  return { draft, demo: false };
}

async function generateDiff(company, material, settings) {
  const thesis = currentThesis(company);
  if (!thesis) throw new Error('请先确认一版投资逻辑，再生成 Thesis Diff');
  const tpl = loadPrompt('thesis-diff.md');
  let content = material.content || '';
  let truncated = false;
  if (content.length > MATERIAL_MAX_CHARS) {
    content = content.slice(0, MATERIAL_MAX_CHARS);
    truncated = true;
  }
  const prompt = fillTemplate(tpl, {
    COMPANY_NAME: company.name,
    COMPANY_CODE: company.code,
    MARKET: company.market,
    VERSION: thesis.versionId,
    AS_OF_DATE: thesis.asOfDate || '',
    CARD: JSON.stringify(thesis.card, null, 2),
    MATERIAL_TITLE: material.title,
    MATERIAL_TYPE: material.type,
    MATERIAL_DATE: material.publishDate || '',
    MATERIAL_CONTENT: content + (truncated ? '\n\n（注：材料过长，以上内容已截断）' : ''),
  });
  if (settings.llm.provider === 'demo') {
    return { diff: mockDiff(company, material, thesis.card), baseVersion: thesis.versionId, demo: true };
  }
  const { content: out } = await llmComplete(settings.llm, [{ role: 'user', content: prompt }], true);
  const diff = extractJson(out);
  if (!diff) throw new Error('模型没有返回可解析的 JSON，请重试或更换模型');
  return { diff, baseVersion: thesis.versionId, demo: false };
}

/* ---------------------------------------------------------------- 路由 */

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.json': 'application/json; charset=utf-8',
};

function serveStatic(req, res, pathname) {
  let rel = pathname === '/' ? '/index.html' : pathname;
  const file = path.normalize(path.join(PUB_DIR, rel));
  if (!file.startsWith(PUB_DIR)) {
    res.writeHead(403);
    return res.end('Forbidden');
  }
  fs.readFile(file, (err, buf) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      return res.end('Not Found');
    }
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(file)] || 'application/octet-stream',
      'Cache-Control': 'no-cache',
    });
    res.end(buf);
  });
}

function maskedSettings(s) {
  const tokens = {};
  for (const p of FINANCE_SOURCES) {
    if (!p.needsKey) continue;
    const v = (s.finance.tokens && s.finance.tokens[p.id]) || '';
    tokens[p.id] = { set: !!v, tail: v ? v.slice(-4) : '' };
  }
  return {
    llm: {
      provider: s.llm.provider,
      baseUrl: s.llm.baseUrl,
      model: s.llm.model,
      apiKeySet: !!s.llm.apiKey,
      apiKeyTail: s.llm.apiKey ? s.llm.apiKey.slice(-4) : '',
    },
    finance: {
      source: s.finance.source,
      tokens,
    },
  };
}

function companySummary(c) {
  const t = currentThesis(c);
  const pendingDiffs = (c.diffs || []).filter((d) => !d.decision).length;
  return {
    id: c.id,
    name: c.name,
    code: c.code,
    market: c.market,
    status: c.status,
    asOfDate: c.asOfDate,
    createdAt: c.createdAt,
    updatedAt: c.updatedAt,
    version: t ? t.versionId : null,
    thesisConfirmed: !!t,
    materialCount: (c.materials || []).length,
    diffCount: (c.diffs || []).length,
    pendingDiffs,
  };
}

function findCompany(db, id) {
  return db.companies.find((c) => c.id === id);
}

async function handleApi(req, res, pathname, query) {
  const db = loadDb();
  const settings = loadSettings();

  /* ---- 初始化数据 ---- */
  if (req.method === 'GET' && pathname === '/api/bootstrap') {
    return send(res, 200, {
      settings: maskedSettings(settings),
      providers: { llm: LLM_PROVIDERS, finance: FINANCE_SOURCES },
      companies: db.companies.map(companySummary),
    });
  }

  /* ---- 设置 ---- */
  if (req.method === 'GET' && pathname === '/api/settings') {
    return send(res, 200, maskedSettings(settings));
  }
  if (req.method === 'PUT' && pathname === '/api/settings') {
    const body = await readBody(req);
    const next = loadSettings();
    if (body.llm) {
      next.llm.provider = body.llm.provider || next.llm.provider;
      next.llm.baseUrl = body.llm.baseUrl != null ? body.llm.baseUrl : next.llm.baseUrl;
      next.llm.model = body.llm.model != null ? body.llm.model : next.llm.model;
      if (body.llm.apiKey) next.llm.apiKey = body.llm.apiKey; // 留空表示沿用旧密钥
      if (body.llm.clearKey) next.llm.apiKey = '';
    }
    if (body.finance) {
      next.finance.source = body.finance.source || next.finance.source;
      const tokenFor = body.finance.tokenFor || body.finance.source || next.finance.source;
      if (body.finance.token) next.finance.tokens[tokenFor] = body.finance.token;
      if (body.finance.clearToken) next.finance.tokens[tokenFor] = '';
    }
    saveSettings(next);
    return send(res, 200, { ok: true, settings: maskedSettings(next) });
  }

  /* ---- 连接测试 ---- */
  if (req.method === 'POST' && pathname === '/api/llm/test') {
    const body = await readBody(req);
    const cfg = Object.assign({}, settings.llm, body.llm || {});
    if (body.llm && !body.llm.apiKey) cfg.apiKey = settings.llm.apiKey; // 未输入则沿用已保存
    if (cfg.provider === 'demo') {
      return send(res, 200, { ok: true, message: '演示模式随时可用，无需连接测试。' });
    }
    const t0 = Date.now();
    try {
      const { content } = await llmComplete(cfg, [
        { role: 'user', content: '这是一个连接测试。请只回复：连接成功' },
      ], false);
      return send(res, 200, {
        ok: true,
        message: `连接成功（${Date.now() - t0}ms），模型回复：${String(content).slice(0, 50)}`,
      });
    } catch (e) {
      return sendError(res, 200, friendlyLlmError(e), { ok: false });
    }
  }

  if (req.method === 'POST' && pathname === '/api/finance/test') {
    const body = await readBody(req);
    const source = (body.finance && body.finance.source) || settings.finance.source;
    const savedTokens = settings.finance.tokens || {};
    const tokens = Object.assign({}, savedTokens);
    if (body.finance && body.finance.token) tokens[source] = body.finance.token; // 未输入则沿用已保存
    try {
      const q = await getQuote({ source, tokens }, '600519', 'A', '贵州茅台');
      return send(res, 200, {
        ok: true,
        message: `连接成功：贵州茅台(600519) 最新价 ${q.price}（来源：${q.source}）`,
      });
    } catch (e) {
      return sendError(res, 200, '数据源连接失败：' + e.message, { ok: false });
    }
  }

  /* ---- 行情 ---- */
  if (req.method === 'GET' && pathname === '/api/finance/quote') {
    const code = query.get('code') || '';
    const market = query.get('market') || 'A';
    const name = query.get('name') || '';
    if (!code) return sendError(res, 400, '缺少代码');
    try {
      const q = await getQuote(settings.finance, code, market, name);
      return send(res, 200, q);
    } catch (e) {
      return sendError(res, 200, e.message, { ok: false });
    }
  }

  /* ---- 证券搜索（名称或代码 → 候选） ---- */
  if (req.method === 'GET' && pathname === '/api/finance/search') {
    const q = (query.get('q') || '').trim();
    if (q.length < 1) return send(res, 200, { items: [] });
    try {
      const items = await searchSecurities(q);
      return send(res, 200, { items });
    } catch (e) {
      return send(res, 200, { items: [], error: '搜索服务暂时不可用，可改用手动输入' });
    }
  }

  /* ---- 公司 ---- */
  if (req.method === 'GET' && pathname === '/api/companies') {
    return send(res, 200, db.companies.map(companySummary));
  }

  if (req.method === 'POST' && pathname === '/api/companies') {
    const body = await readBody(req);
    if (!body.name || !body.name.trim()) return sendError(res, 400, '请填写公司名称');
    const company = {
      id: uid('cmp'),
      name: body.name.trim(),
      code: String(body.code || '').trim(),
      market: body.market || 'A',
      status: body.status || '研究中',
      asOfDate: body.asOfDate || today(),
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      theses: [],
      materials: [],
      diffs: [],
    };
    db.companies.push(company);
    saveDb(db);
    return send(res, 200, companySummary(company));
  }

  const mCompany = pathname.match(/^\/api\/companies\/([\w-]+)(\/.*)?$/);
  if (mCompany) {
    const company = findCompany(db, mCompany[1]);
    if (!company) return sendError(res, 404, '公司不存在');
    const sub = mCompany[2] || '/';

    if (req.method === 'GET' && sub === '/') {
      return send(res, 200, company);
    }

    if (req.method === 'PATCH' && sub === '/') {
      const body = await readBody(req);
      for (const k of ['name', 'code', 'market', 'status', 'asOfDate']) {
        if (body[k] != null) company[k] = body[k];
      }
      company.updatedAt = new Date().toISOString();
      saveDb(db);
      return send(res, 200, companySummary(company));
    }

    if (req.method === 'DELETE' && sub === '/') {
      db.companies = db.companies.filter((c) => c.id !== company.id);
      saveDb(db);
      return send(res, 200, { ok: true });
    }

    /* ---- 生成投资逻辑草稿 ---- */
    if (req.method === 'POST' && sub === '/thesis/generate') {
      const body = await readBody(req);
      if (!body.notes || !String(body.notes).trim()) {
        return sendError(res, 400, '请先粘贴一些你的想法或材料');
      }
      try {
        const { draft, demo } = await generateBootstrap(company, body.notes, settings);
        return send(res, 200, { draft: normalizeCard(draft), extras: { factsUsed: draft.factsUsed || [], aiInferences: draft.aiInferences || [] }, demo });
      } catch (e) {
        return sendError(res, 200, friendlyLlmError(e), { ok: false });
      }
    }

    /* ---- 确认/保存投资逻辑（新版本） ---- */
    if (req.method === 'POST' && sub === '/thesis') {
      const body = await readBody(req);
      const card = normalizeCard(body.card);
      if (!card.oneLiner.trim()) return sendError(res, 400, '一句话投资逻辑不能为空');
      const prev = currentThesis(company);
      const version = {
        versionId: nextVersionId(company),
        asOfDate: body.asOfDate || company.asOfDate || today(),
        createdAt: new Date().toISOString(),
        supersedes: prev ? prev.versionId : null,
        userConfirmed: true,
        sourceDiffId: body.sourceDiffId || null,
        note: body.note || '',
        card,
      };
      company.theses.push(version);
      company.asOfDate = version.asOfDate;
      company.updatedAt = new Date().toISOString();
      saveDb(db);
      return send(res, 200, { ok: true, version: version.versionId });
    }

    /* ---- 材料 ---- */
    if (req.method === 'POST' && sub === '/materials') {
      const body = await readBody(req);
      if (!body.title || !String(body.title).trim()) return sendError(res, 400, '请填写材料标题');
      if (!body.content || !String(body.content).trim()) return sendError(res, 400, '材料内容不能为空');
      const material = {
        id: uid('mat'),
        title: String(body.title).trim(),
        type: body.type || '其他',
        publishDate: body.publishDate || '',
        content: String(body.content),
        addedAt: new Date().toISOString(),
      };
      company.materials.push(material);
      company.updatedAt = new Date().toISOString();
      saveDb(db);
      return send(res, 200, { ok: true, material: { ...material, content: undefined } });
    }

    const mMaterial = sub.match(/^\/materials\/([\w-]+)$/);
    if (mMaterial && req.method === 'DELETE') {
      company.materials = company.materials.filter((m) => m.id !== mMaterial[1]);
      saveDb(db);
      return send(res, 200, { ok: true });
    }

    /* ---- 生成 Thesis Diff ---- */
    if (req.method === 'POST' && sub === '/diffs') {
      const body = await readBody(req);
      const material = company.materials.find((m) => m.id === body.materialId);
      if (!material) return sendError(res, 400, '找不到该材料');
      if (!currentThesis(company)) {
        return sendError(res, 400, '请先在「投资逻辑」页确认一版投资逻辑，再生成变更报告');
      }
      try {
        const { diff, baseVersion, demo } = await generateDiff(company, material, settings);
        const record = {
          id: uid('diff'),
          materialId: material.id,
          materialTitle: material.title,
          materialDate: material.publishDate || '',
          baseVersion,
          createdAt: new Date().toISOString(),
          demo,
          content: diff,
          decision: null,
        };
        company.diffs.push(record);
        company.updatedAt = new Date().toISOString();
        saveDb(db);
        return send(res, 200, { ok: true, diffId: record.id, demo });
      } catch (e) {
        return sendError(res, 200, friendlyLlmError(e), { ok: false });
      }
    }

    /* ---- Diff 用户决定 ---- */
    const mDiff = sub.match(/^\/diffs\/([\w-]+)\/decision$/);
    if (mDiff && req.method === 'POST') {
      const diff = company.diffs.find((d) => d.id === mDiff[1]);
      if (!diff) return sendError(res, 404, '报告不存在');
      const body = await readBody(req);
      const choice = body.choice;
      const allowed = ['accept', 'accept_modified', 'reject', 'defer', 'research_task'];
      if (!allowed.includes(choice)) return sendError(res, 400, '无效的决定类型');

      diff.decision = { choice, note: body.note || '', decidedAt: new Date().toISOString() };

      let newVersion = null;
      if (choice === 'accept' || choice === 'accept_modified') {
        const card = normalizeCard(
          choice === 'accept_modified' && body.card ? body.card : diff.content.revisedCard
        );
        const prev = currentThesis(company);
        const version = {
          versionId: nextVersionId(company),
          asOfDate: today(),
          createdAt: new Date().toISOString(),
          supersedes: prev ? prev.versionId : null,
          userConfirmed: true,
          sourceDiffId: diff.id,
          note: body.note || '',
          card,
        };
        company.theses.push(version);
        company.asOfDate = version.asOfDate;
        newVersion = version.versionId;
      }
      company.updatedAt = new Date().toISOString();
      saveDb(db);
      return send(res, 200, { ok: true, newVersion });
    }
  }

  return sendError(res, 404, '接口不存在');
}

function friendlyLlmError(e) {
  const msg = e && e.message ? e.message : String(e);
  if (/401|Unauthorized|invalid.*key|Incorrect API key/i.test(msg)) {
    return 'API Key 似乎不正确，请到「设置」检查密钥。';
  }
  if (/402|insufficient|balance|余额/i.test(msg)) {
    return '模型账户余额不足，请充值后重试。';
  }
  if (/429|rate.?limit/i.test(msg)) {
    return '请求太频繁，稍等片刻再试。';
  }
  if (/timeout|ETIMEDOUT|ECONNREFUSED|ENOTFOUND|fetch failed/i.test(msg)) {
    return '连不上模型服务，请检查网络或接口地址（Base URL）。';
  }
  if (/模型接口报错/.test(msg)) return msg;
  return '生成失败：' + msg;
}

/* ---------------------------------------------------------------- 启动 */

function startServer(port, attemptsLeft, autoOpen) {
  ensureDataDir();
  const server = http.createServer(async (req, res) => {
    try {
      const u = new URL(req.url, 'http://localhost');
      if (u.pathname.startsWith('/api/')) {
        await handleApi(req, res, u.pathname, u.searchParams);
      } else {
        serveStatic(req, res, u.pathname);
      }
    } catch (e) {
      if (!res.headersSent) sendError(res, 500, '服务器内部错误：' + e.message);
      else res.end();
      console.error(e);
    }
  });
  server.on('error', (e) => {
    if (e.code === 'EADDRINUSE' && attemptsLeft > 0) {
      startServer(port + 1, attemptsLeft - 1, autoOpen);
    } else {
      console.error('启动失败：', e.message);
      process.exit(1);
    }
  });
  server.listen(port, () => {
    const url = `http://localhost:${port}`;
    console.log('');
    console.log('  ThesisOS · ThesisDiff');
    console.log('  ------------------------------------');
    console.log('  已在本地启动： ' + url);
    console.log('  数据保存在：   ' + DATA_DIR);
    console.log('  关闭本窗口即可停止服务。');
    console.log('');
    if (autoOpen) exec(`open "${url}"`);
  });
}

const autoOpen = process.argv.includes('--open');
const portArg = process.argv.find((a) => a.startsWith('--port='));
const basePort = portArg ? parseInt(portArg.split('=')[1], 10) : 8787;
startServer(basePort, 10, autoOpen);
