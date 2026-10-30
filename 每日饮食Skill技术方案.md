# 每日饮食 · 「图片 + 解说音频 + 字幕视频」生成工具技术方案

> 版本 **v1.2**（直连火山 API 版）｜2026-10-30
> v1.0 → v1.2 主要变化：生图/音频/文案全部改为**脚本直连 API**，取消对 Agent 侧工具的依赖；引入 Ports & Adapters 分层；Skill 从"必需外壳"降级为"可选说明书"。

---

## 0. 决策摘要

| 决策点 | 结论 |
|---|---|
| 交付形态 | **CLI 优先**（`dietctl`），Skill 作为可选外壳。核心是"没有 Agent 也能跑" |
| 解耦层级 | 三重：不依赖 Agent ｜不依赖供应商（防腐层）｜不依赖交互方式（cron/手动/API） |
| 图像 | 火山方舟 **Seedream 5.0 pro**（`doubao-seedream-5-0-pro-260628`），脚本直连 |
| 文案 | 火山方舟 **chat/completions**（与图像同一个 ARK key），脚本直连 |
| 音频 | 豆包语音 TTS（`seed-tts-2.0` / 音频生成 `seed-audio-1.0`），经防腐层调用 |
| 凭证 | **两条鉴权线**：`ARK_API_KEY`（方舟：图 + 文案）+ 语音凭据（APP ID+Token 或 UUID Key），同一账号 |
| 依赖 | Python 3.13 + `requests` + `openpyxl` + ffmpeg 7.1（均已就绪） |
| 全链路原子单位 | **分镜 shot**（一句解说 + 一张图 + 一个时间区间） |
| 唯一真相源 | `runs/<date>_<slot>/plan_bundle.json` + `state` 状态机 |
| 核心原则 | **算数归代码，文案归模型，画面归生图，成片归 ffmpeg；供应商归 clients** |

---

## 1. 需求解构与一个必须纠正的假设

| 需求 | 判断 | 架构约束 |
|---|---|---|
| Agent 可调用工具 | 但"可被 Agent 调用"≠"只能被 Agent 调用" | 入口是 CLI，Agent 只是调用方之一 |
| 模板化上传 | 模板即 Schema | 校验必须代码化，不能靠 LLM 读表 |
| 动态匹配菜单 | 约束求解 + 打分 | 需结构化菜谱库 + 营养库，禁止模型"想"菜 |
| 图 + 音 + 视频 | 三类异构产出 | 用 shot 对齐 |
| 不依赖特定 Agent | 能力必须内聚到可执行程序 | 一切生成能力走 HTTP，不走 Agent 内置工具 |

### 1.1 必须纠正的假设：不是「一个 API」，是「一个账号、两条鉴权线」

火山引擎的图像生成与语音合成走**两套独立鉴权**，官方文档明确分列：

| 能力 | 控制台 | 凭证形态 | 请求头 | 端点 |
|---|---|---|---|---|
| 图像生成（Seedream） | 火山**方舟**控制台 | `ARK_API_KEY`（`ark-` 前缀） | `Authorization: Bearer $ARK_API_KEY` | `ark.cn-beijing.volces.com/api/v3/images/generations` |
| 文案生成（Chat） | 同上 | 同上 | 同上 | `ark.cn-beijing.volces.com/api/v3/chat/completions` |
| 语音合成（TTS） | 豆包**语音**控制台 | 见下方两套体系 | 见下方 | `openspeech.bytedance.com/api/v3/tts/unidirectional` |

**语音这一侧有个比"两把钥匙"更容易踩的坑：TTS 自身有两套并存的凭据体系。**

| 体系 | 控制台位置 | 请求头 |
|---|---|---|
| A. 旧版（早期开通的应用） | 语音技术 → 应用管理 → 应用详情 | `X-Api-App-Id: <app_id>` + `X-Api-Access-Key: <access_token>` |
| B. 新版（后建的 API Key） | 豆包语音 → API Key 管理 | `X-Api-Key: <uuid>` |

两者端点完全相同，用错体系与"未开通模型"**报同一个错误码 `45000010`**，因此只看错误码会把原因诊断偏。本项目的做法是让客户端按环境变量自动判定，A 优先（实测可用），并在启动日志里打印实际采用的体系：

```
[3/8] 配音：doubao 真人声 4 句，语音总长 17.0s，音色 zh_female_xiaohe_uranus_bigtts（鉴权：APP ID + Token）
```

好消息：两者同属一个火山账号，账单合并统计，配置可收敛到一个 `.env`：

```ini
ARK_API_KEY=ark-xxxxxxxx            # 方舟：图像 + 文案
VOLC_SPEECH_APP_ID=96xxxxxxxx        # 豆包语音：配音（旧版体系）
VOLC_SPEECH_ACCESS_TOKEN=xxxxxxxx    # 与 APP_ID 成对出现
# 若改用新版体系，则只填 VOLC_SPEECH_API_KEY=<uuid>，二选一
```

> 也可以只填一个用一半：只跑图文不出配音时，`ARK_API_KEY` 单独就能完成「菜单 + 文案 + 配图」，语音环节自动跳过并在结果里标注 `audio: skipped(no speech key)`。这让**冷启动门槛降到一把钥匙**。

### 1.2 为什么这个改动值得

| 维度 | v1.0（依赖 Agent 工具） | v1.2（直连 API） |
|---|---|---|
| 换 Agent | 需要目标 Agent 也提供等价生图工具 | 任意 Agent / 无 Agent |
| 一键跑完 | 不能，图片环节必须人/Agent 在环 | 能，`dietctl run` 一条命令 |
| 定时任务 | 不能 | `cron` / GitHub Actions / 计划任务 |
| 并发 | 受 Agent 轮次限制 | `asyncio` + Semaphore 自己控 |
| 成本核算 | 粗略（credits 估算） | 精确（按张/按 token 入账） |
| 可调试性 | 黑盒，只能在会话里看 | 全量 request/response 落盘 |
| 代码量 | 少 | **约 3 倍**（协议、重试、限流、错误码、台账） |

代码量增加 3 倍换自主性，对"每天都要跑"的项目是划算的——稳定性比开发速度重要，而且不必每次回到会话里手动点。

---

## 2. 形态：CLI 优先，Skill 可选

### 2.1 三层解耦

```
① 不依赖 Agent     → 一切能力在 CLI，Agent 只是一种调用方
② 不依赖供应商     → 领域层不认识"火山"，供应商只在 clients/ 出现
③ 不依赖交互方式   → 同一命令可被 cron / 快捷指令 / CI / HTTP 调用
```

**②是这次改动里最容易被忽略、但长期收益最大的一条。** 三年后 Seedream 停产、或你想换别家 TTS，只需重写 `clients/` 下一个文件，`nutrition.py`/`planner.py`/`render.py` 一行不改。

### 2.2 Skill 还要不要做

要，但角色变了：

| | v1.0 的 Skill | v1.2 的 Skill |
|---|---|---|
| 定位 | 编排层（必需） | 说明书 + 薄封装（可选） |
| 内容 | 阶段路由、状态机、提示词资产 | 能力描述、命令速查、少量胶水 |
| 没有它会怎样 | 跑不起来 | 仍可 `python dietctl.py run` 跑起来 |

这反而让"是否适合 Skills"这个原问题从"必须论证"变成"顺手加个壳"。**先做 CLI，跑通之后再包 Skill**——顺序不能反，否则会被 Skill 的会话模型牵着走。

### 2.3 分层与职责

| 层 | 位置 | 职责 | 是否认识供应商 |
|---|---|---|---|
| L0 交互 | — | Agent / cron / 手动 | — |
| L1 CLI | `dietctl.py` | 参数解析、JSON 输出、退出码 | ❌ |
| L2 领域 | `core/` | 校验、营养、打分、分镜、时间轴 | ❌ **绝不** |
| L3 适配 | `clients/` | HTTP、重试、限流、记账、ffmpeg 调用 | ✅ **只有这里** |
| L4 存储 | `store/` | SQLite、runs 目录 | ❌ |

**铁律：`core/` 里不允许出现 `import requests`、不允许出现 "ark"、"volces"、"seedream" 字样。**

---

## 3. 整体架构

```
┌────────────────────────────────────────────────────────────┐
│ L0  调用方：任意 Agent ｜ cron ｜ GitHub Actions ｜ 手动 CLI  │
└─────────────────────────┬──────────────────────────────────┘
                          │ subprocess / shell
┌─────────────────────────▼──────────────────────────────────┐
│ L1  dietctl.py   唯一入口，12 个 verb，统一 JSON envelope    │
└─────────────────────────┬──────────────────────────────────┘
                          │
┌─────────────────────────▼──────────────────────────────────┐
│ L2  core/   ingest · nutrition · planner · narrator ·       │
│             storyboard · subtitles · timeline               │
│             （纯函数，零网络，可单测）                        │
└──────────┬───────────────────────────────┬─────────────────┘
           │ Port 接口（抽象）              │
┌──────────▼───────────────┐   ┌───────────▼─────────────────┐
│ L3  clients/  适配器      │   │ L4  store/                  │
│  ark_image.py            │   │  SQLite(WAL) + runs/ 快照    │
│  ark_chat.py             │   │  assets.content_hash 去重    │
│  speech.py               │   │  costs 台账                  │
│  ffmpeg.py               │   └─────────────────────────────┘
└──────────┬───────────────┘
           │ HTTPS / 子进程
┌──────────▼─────────────────────────────────────────────────┐
│ 外部：方舟 images/generations · 方舟 chat/completions ·     │
│       豆包语音 openspeech · 本地 ffmpeg 7.1                 │
└────────────────────────────────────────────────────────────┘
```

---

## 4. 目录结构

```
daily-diet/
├── dietctl.py                  # 唯一入口
├── .env.example                # 凭据模板（ARK + 语音两套体系）
├── core/
│   ├── ingest.py               # 模板解析与校验（openpyxl）
│   ├── nutrition.py            # 营养累加与缺口（纯查表计算）
│   ├── planner.py              # 候选生成 + 加权打分
│   ├── narrator.py             # 解说脚本（只调 port，不碰 HTTP）
│   ├── storyboard.py           # 分镜 + 图提示词
│   └── timeline.py             # 时间轴与字幕断行
├── clients/
│   ├── base.py                 # 重试/退避/超时/记账/降级阶梯（共用）
│   ├── ark_image.py            # Seedream 文生图 / 图生图 / 多图融合
│   ├── ark_chat.py             # 文案生成（同 ARK key）
│   ├── speech.py               # 豆包 TTS（防腐层，实现可换）
│   └── ffmpeg.py               # 唯一的子进程调用点
├── store/
│   ├── schema.sql              # 12 张表 + 迁移
│   └── db.py
├── assets/
│   ├── nutrition/fct_subset.csv      # 成分表子集 ≈250 条
│   ├── recipes/dishes.jsonl          # 家常菜谱库（冷启动核心）
│   ├── fonts/SourceHanSansSC-Bold.otf # libass 必需
│   ├── bgm/*.mp3
│   └── templates/食材与饮食方案模板_v1.xlsx
├── runs/<date>_<slot>/         # 每集运行目录（检查点）
├── data/diet.db
└── skill/SKILL.md              # 可选外壳（Phase 3 再加）
```

---

## 5. 模块划分

| 模块 | 职责 | 输出 | 网络 |
|---|---|---|---|
| **M0** ingest | 解析 xlsx/csv、校验、单位归一、别名映射 | `ingest_report.json` | ❌ |
| **M1** nutrition | 营养累加、缺口分析、生熟比折算 | `nutrient_gap.json` | ❌ |
| **M2** planner | 硬过滤 + 角色槽枚举 + 加权打分 → Top3 | `plan_bundle.json` | ❌ |
| **M3** narrator | 解说脚本（经 `ark_chat` port） | `script.json` | ✅（文案） |
| **M4** storyboard | 分镜表 + 图提示词 | `shots.json` / `image_jobs.json` | ❌ |
| **M5** imaging | 并发调用 Seedream，落盘、质检、降级 | `assets_index.json` | ✅（图） |
| **M6** voice | 豆包 TTS + 句级时间戳 | `voice.mp3` + `subtitle_raw.json` | ✅（音） |
| **M7** subtitles | 时间戳对齐、断行、SRT/ASS | `final.srt` `.ass` | ❌ |
| **M8** render | ffmpeg 合成、ffprobe 自检 | `out/*.mp4` | ❌（本地进程） |

### 5.1 M5 的关键变化：从三段式塌缩成一次函数调用

**v1.0（依赖 Agent）**
```
imaging.dispatch → image_jobs.json → [Agent 逐个调 ImageGen] → imaging.register
```

**v1.2（直连）**
```python
# core/imaging.py 伪码
async def generate(jobs, concurrency=3):
    sem = asyncio.Semaphore(concurrency)
    async def one(job):
        async with sem:
            hit = store.find_by_hash(job.hash)      # 缓存命中，0 成本
            if hit: return hit
            return await ark_image.generate(job)     # 含重试与降级
    return await asyncio.gather(*[one(j) for j in jobs])
```

省掉的不是一层调用，是**一个人工环节**。这也是"能一键跑完"和"不能"的分界线。

### 5.2 clients 层规范（所有适配器必须遵守）

| 关注点 | 规定 |
|---|---|
| 超时 | 连接 10s，读 120s（图像生成 1K/2K 单张实测可达 20–60s，别用默认 30s） |
| 重试 | 429 / 500 / 502 / 503 / 超时 → 指数退避 + jitter，最多 3 次 |
| **不重试** | 400 参数错、401 鉴权错、内容审核拒绝（重试只是浪费钱） |
| 并发 | 全局 `Semaphore(3)`；IPM 500 用不满，瓶颈在单账号并发与自身带宽 |
| 幂等 | 以 `sha1(prompt + model + size + style_version + ref_hashes)` 为键，命中即复用 |
| 落盘 | API 返回 URL 时**立刻下载**（预签名 URL 通常 24h 失效）；优先 `b64_json` 直接拿字节 |
| 记账 | 每次调用写 `costs` 表：model / 张数 / tokens / 耗时 / 估算金额 / request_id |
| 留痕 | 原始 request / response 存 `runs/<id>/logs/`，仅保留最近 10 次 |
| 降级 | 见 §11.5 四级阶梯，写死在配置，不靠临场判断 |

### 5.3 降级阶梯（失去 Agent 兜底后的替代品）

原来"图生成失败让 Agent 换个说法重试"，现在必须**规则化**：

```
L1 原样重试          —— 仅对 429/5xx/超时，退避 2s→6s→18s
L2 简化提示词        —— 去掉手部动作、去掉器皿细节描述（最易触发审核/变形的部分）
L3 换模型/换角度     —— 降级 flash（doubao-seedream-5-0-flash-260915）或换机位
L4 占位 + 明确告知   —— 纯色渐变底 + "配图生成失败"角标，绝不静默出片
```

每一级都写进 `logs/`，最终报告里列出"哪张图降到了第几级"。

---

## 6. 数据模型（SQLite）

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE users(
  user_id TEXT PRIMARY KEY, nickname TEXT,
  sex TEXT CHECK(sex IN ('M','F')), birth_year INTEGER,
  height_cm REAL, weight_kg REAL,
  activity_level TEXT CHECK(activity_level IN ('low','moderate','high')),
  goal TEXT CHECK(goal IN ('maintain','lose_fat','gain_muscle','blood_sugar')),
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE ingredients(
  ing_id TEXT PRIMARY KEY, name_cn TEXT NOT NULL,
  aliases TEXT,                -- JSON array
  category TEXT,               -- 谷薯/蔬菜/水果/畜禽/水产/蛋/奶/大豆坚果/油脂调味
  edible_ratio REAL DEFAULT 1.0,
  cooked_ratio REAL DEFAULT 1.0,   -- 生→熟重量系数（鸡胸 0.70）
  fct_code TEXT, unit_default TEXT,
  is_dark_veg INTEGER DEFAULT 0
);

CREATE TABLE inventory(
  batch_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(user_id),
  ing_id TEXT NOT NULL REFERENCES ingredients(ing_id),
  raw_name TEXT, qty REAL NOT NULL, unit TEXT NOT NULL,
  qty_g REAL NOT NULL,             -- 归一化克数（生重）
  purchase_date TEXT, expire_date TEXT,
  storage TEXT CHECK(storage IN ('fridge','freezer','pantry')),
  source_upload_id TEXT, status TEXT DEFAULT 'active'
    CHECK(status IN ('active','consumed','discarded'))
);

CREATE TABLE diet_plans(
  plan_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  plan_name TEXT, plan_type TEXT, slots TEXT,
  kcal_target INTEGER, protein_g REAL, carb_g REAL, fat_g REAL,
  restrictions TEXT,               -- JSON {allergens,dislikes,religion}
  diet_type TEXT, max_cook_min INTEGER DEFAULT 40, servings INTEGER DEFAULT 1,
  raw_json TEXT, is_active INTEGER DEFAULT 1
);

CREATE TABLE dishes(
  dish_id TEXT PRIMARY KEY, name_cn TEXT NOT NULL,
  cuisine TEXT, role TEXT,         -- protein/veg/staple/soup/side
  ingredients TEXT NOT NULL,       -- JSON [{ing_id,qty_g}]
  steps TEXT NOT NULL,             -- JSON [{seq,action,text,shot_hint}]
  cook_min INTEGER, tags TEXT, nutrition_per_serving TEXT
);

CREATE TABLE runs(
  run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  run_date TEXT NOT NULL, slot TEXT NOT NULL,
  bundle_path TEXT NOT NULL,
  state TEXT DEFAULT 'planned' CHECK(state IN
    ('planned','scripted','storyboarded','voiced','imaged','rendered','published','failed')),
  seed INTEGER, model_snapshot TEXT,   -- JSON: 本次用的各 model id
  cost_cny REAL DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE assets(
  asset_id TEXT PRIMARY KEY, run_id TEXT, shot_id TEXT,
  kind TEXT CHECK(kind IN ('image_raw','image_process','image_final',
                           'audio','subtitle_raw','srt','ass','video')),
  path TEXT NOT NULL, content_hash TEXT NOT NULL,
  meta TEXT,                       -- JSON {w,h,dur,model,degrade_level}
  created_at TEXT DEFAULT (datetime('now'))
);

-- 成本台账（直连后才能精确记账，这是相对 v1.0 新增的表）
CREATE TABLE costs(
  cost_id TEXT PRIMARY KEY,
  run_id TEXT, stage TEXT,          -- image/chat/tts
  provider TEXT, model TEXT,
  units REAL,                       -- 张数 or 千tokens or 字符数
  unit_type TEXT, amount_cny REAL,
  request_id TEXT, latency_ms INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE uploads(
  upload_id TEXT PRIMARY KEY, user_id TEXT, file_name TEXT,
  file_hash TEXT, template_version TEXT,
  rows_total INTEGER, rows_ok INTEGER, rows_err INTEGER, report_path TEXT
);

CREATE TABLE feedback(
  fb_id TEXT PRIMARY KEY, run_id TEXT, dish_id TEXT,
  action TEXT CHECK(action IN ('cooked','skipped','liked','disliked','modified')), note TEXT
);

CREATE INDEX idx_inv_user_status ON inventory(user_id,status);
CREATE UNIQUE INDEX uq_assets_hash ON assets(content_hash,kind);
CREATE INDEX idx_costs_run ON costs(run_id);
```

---

## 7. 上传模板设计

### 7.1 形态

| 形态 | 说明 | 推荐 |
|---|---|---|
| **xlsx 多 Sheet** | `01_食材清单`/`02_饮食方案`/`03_偏好与禁区`/`04_餐次计划`/`05_填写说明` | ★★★ |
| csv 多文件 | 3 个 csv 打 zip | ★★ |
| Markdown 表格 | 聊天里粘贴 | ★ |

解析只按**表头名**匹配，不按列位置；表头做别名归一（`食材名`/`名称`/`食材` 等价）。

### 7.2 Sheet 01 · 食材清单

| # | 字段 | 类型 | 必填 | 枚举/范围 | 示例 | 校验 |
|---|---|---|---|---|---|---|
| 1 | 食材名称 | string | ✅ | — | 鸡胸肉 | 非空；须映射到主数据（编辑距离 ≤2），否则 `E_ING_UNKNOWN` + 给 Top3 候选 |
| 2 | 数量 | number | ✅ | >0 | 200 | 非数字 `E_QTY_TYPE`；≤0 `E_QTY_RANGE` |
| 3 | 单位 | enum | ✅ | g/kg/ml/L/个/根/颗/把/片/勺/碗 | g | 枚举外 `E_UNIT_ENUM`；中文量词走映射（半斤→250g，一把青菜→80g 可配置） |
| 4 | 类别 | enum | ⭕ | 谷薯/蔬菜/水果/畜禽/水产/蛋/奶/大豆坚果/油脂调味/其他 | 畜禽 | 空则按主数据补 |
| 5 | 购买日期 | date | ⭕ | YYYY-MM-DD | 2026-10-28 | 未来日期 `E_DATE_FUTURE` |
| 6 | 保质期至 | date | ⭕ | YYYY-MM-DD | 2026-11-02 | 早于购买日 `E_DATE_ORDER`；已过期标 `expired` 并剔除 |
| 7 | 存放位置 | enum | ⭕ | 冷藏/冷冻/常温 | 冷藏 | 默认冷藏 |
| 8 | 是否深色蔬菜 | bool | ⭕ | 是/否 | 是 | 空则查主数据 |
| 9 | 备注 | string | ⭕ | ≤100 字 | 已切好 | 存档 |

### 7.3 Sheet 02 · 饮食方案

| # | 字段 | 类型 | 必填 | 枚举/范围 | 示例 | 校验 |
|---|---|---|---|---|---|---|
| 1 | 方案名称 | string | ✅ | — | 工作日减脂餐 | 非空 |
| 2 | 方案类型 | enum | ✅ | 日方案/周方案/目标方案 | 日方案 | — |
| 3 | 适用餐次 | string | ✅ | 早餐/午餐/晚餐/加餐，逗号分隔 | 午餐,晚餐 | ≥1 个 |
| 4 | 目标热量 | int | ⭕ | 1200–3500 | 1800 | 越界仅 `W_KCAL_RANGE` |
| 5 | 蛋白质目标g | number | ⭕ | 40–200 | 90 | 与"体重×1.2~2.0"偏离 >30% 警告 |
| 6 | 碳水目标g | number | ⭕ | 100–400 | 200 | 同上 |
| 7 | 脂肪目标g | number | ⭕ | 30–100 | 55 | 同上 |
| 8 | 饮食类型 | enum | ✅ | 普通/素食/蛋奶素/低GI/低钠/低碳水 | 普通 | 影响硬过滤 |
| 9 | 最大烹饪时长min | int | ⭕ | 10–120 | 40 | 默认 40 |
| 10 | 份数 | int | ⭕ | 1–6 | 1 | 影响克数折算 |
| 11 | 方案说明 | string | ⭕ | ≤200 字 | 晚上不吃主食 | 进 narrator 的语气上下文 |

### 7.4 Sheet 03 · 偏好与禁区

| # | 字段 | 类型 | 必填 | 示例 | 校验 |
|---|---|---|---|---|---|
| 1 | 过敏原 | string | ⭕ | 花生,海鲜 | **硬约束**，命中即淘汰，不可被任何打分覆盖 |
| 2 | 不吃 | string | ⭕ | 香菜,内脏 | 硬约束（软化为 -100 分） |
| 3 | 宗教/信仰禁忌 | string | ⭕ | 无 | 硬约束 |
| 4 | 口味偏好 | string | ⭕ | 清淡,微辣 | 影响菜系轮换 |
| 5 | 忌口调料 | string | ⭕ | 味精,蚝油 | 菜谱配料级过滤 |
| 6 | 每日蔬菜目标g | int | ⭕ | 400 | 默认 400（指南 300–500） |
| 7 | 每日油上限g | int | ⭕ | 28 | 默认 28（指南 25–30） |
| 8 | 每日盐上限g | number | ⭕ | 5 | 默认 5 |

### 7.5 Sheet 04 · 餐次计划（可选）

| # | 字段 | 类型 | 必填 | 示例 |
|---|---|---|---|---|
| 1 | 日期 | date | ✅ | 2026-10-30 |
| 2 | 餐次 | enum | ✅ | 晚餐 |
| 3 | 指定菜品 | string | ⭕ | 番茄炒蛋（空则由 M2 推荐） |
| 4 | 指定主食 | string | ⭕ | 杂粮饭 |
| 5 | 备注 | string | ⭕ | 想吃热乎的 |

> `指定菜品` 非空时，M2 只做"补位"（凑齐营养缺口），不推翻用户意愿。

### 7.6 错误码

| 码 | 级别 | 处置 |
|---|---|---|
| `E_FILE_FORMAT` / `E_SHEET_MISSING` / `E_HEADER_MISMATCH` | 阻断 | 指出具体缺什么 |
| `E_ING_UNKNOWN` | 阻断该行 | 给 Top3 候选；用户确认后写别名表（越用越准） |
| `E_QTY_TYPE` / `E_QTY_RANGE` / `E_UNIT_ENUM` / `E_UNIT_UNMAPPABLE` | 阻断该行 | 给可选项 |
| `E_DATE_ORDER` / `E_DATE_FUTURE` | 阻断/警告 | — |
| `E_DUPLICATE` | 警告 | 合并数量 |
| `W_KCAL_RANGE` | 警告 | 给 DRI 参考值 |
| `W_ALLERGEN_CONFLICT` | 警告 | **红色提示**，不阻断 |

**部分成功**：坏行不入库、好行入库，报告写"第 7 行：单位'把'无法换算，请改为克数"。否则改一个错要重传 40 行。

---

## 8. 数据流

```
食材.xlsx
   │ M0 ingest（校验/归一/入库）
   ▼
nutrient_gap.json ──"蛋白质差42g｜蔬菜差180g｜深色蔬菜0/200g"
   │ M1 nutrition
   ▼
plan_bundle.json {menu, why, rejected, score}   ← M2 planner（Top3 待选）
   │ M3 narrator（ark_chat 写 4 段解说，只准引用 facts）
   ▼
script.json
   │ M4 storyboard（段 → 分镜，套 3 类图模板）
   ▼
shots.json + image_jobs.json
   │
   ├── M5 imaging ── ark_image 并发3 ──→ images/S01_raw.png …
   ├── M6 voice   ── speech TTS ────────→ voice.mp3 + subtitle_raw.json
   └── M7 subtitles ───────────────────→ final.srt / final.ass
   ▼
M8 render（ffmpeg：zoompan → xfade → ass → amix）→ out/final.mp4
   ▼
ffprobe 自检 → 交付
```

### 8.1 一键运行

```bash
python dietctl.py run --user U001 --date 2026-10-30 --slot dinner --auto
```
`--auto` 自动选 Top1；不带 `--auto` 时先输出 Top3 并停在 `planned` 状态等确认。

### 8.2 run 目录

```
runs/2026-10-30_dinner/
├── plan_bundle.json      # 唯一真相源（含 state）
├── ingest_report.json  nutrient_gap.json  script.json
├── shots.json  image_jobs.json  assets_index.json
├── images/  audio/  subs/  out/
└── logs/                 # 原始 request/response，仅留最近 10 次
```

`dietctl resume --run <id>` 读 `state` 从断点续跑；`content_hash` 未变的资产直接复用。

---

## 9. 工具接口设计

### 9.1 统一约定

```jsonc
// stdout 单行 JSON；stderr 放日志
{"ok": true, "data": {...}, "warnings": [...], "cost_cny": 0.86, "next": ["..."],
 "skipped": [{"stage":"voice","reason":"no speech credential"}]}
```

| 约定 | 规则 |
|---|---|
| 退出码 | 0 成功｜2 校验失败｜3 缺配置/依赖｜4 上游 API 失败｜5 状态非法 |
| 幂等 | 每 verb 以 content_hash 为键 |
| 参数 | 全部支持 `--json` / `--dry-run` / `--yes` / `--max-cost` |
| 路径 | 一律绝对路径，相对路径按 `DIET_ROOT` 解析 |

### 9.2 接口清单

| verb | 用途 | 主要入参 | 出参 |
|---|---|---|---|
| `init` | 建库/迁移/灌主数据/生成模板 | `--reset` | `{tables, ingredients, dishes, template}` |
| `ingest` | 解析上传文档 | `--file --user` | `{rows_ok, rows_err, errors[]}` |
| `plan` | 动态推荐菜单 | `--user --date --slot --top 3` | `{run_id, candidates[], gap}` |
| `commit` | 确认候选，落 bundle | `--run --candidate 1` | `{bundle_path}` |
| `script` | 生成解说 + 分镜 | `--run --secs 60 --tone warm` | `{script, shots[]}` |
| `image` | 并发生图 | `--run --types raw,process,final` | `{ok, failed[], cost_cny}` |
| `voice` | 配音 + 时间戳 | `--run --mode auto` | `{audio, dur, sentences, cost_cny}` |
| `subs` | 生成字幕 | `--run` | `{srt, ass, lines}` |
| `render` | 合成视频 | `--run --res 1080x1920` | `{mp4, dur, size_mb}` |
| `run` | 全链路（组合上面） | `--user --date --slot --auto` | `{mp4, cost_cny, stages[]}` |
| `status` | 状态机 + 下一步 | `--run` | `{state, done[], todo[], next}` |
| `doctor` | 自检（凭据/ffmpeg/字体） | — | `{ark_key: ok, speech_auth: app, ffmpeg: 7.1.1, font: ok}` |

`doctor` 是这次新增的关键 verb：**它把"凭据体系填错"这个坑变成一条命令的检查项**，而不是等跑到一半才发现少配一个。

### 9.3 `plan_bundle.json`（中央契约）

```jsonc
{
  "run_id": "2026-10-30_dinner",
  "state": "rendered",
  "seed": 1024,
  "model_snapshot": {
    "image": "doubao-seedream-5-0-pro-260628",
    "chat":  "doubao-seed-2-1-pro-260915",
    "tts":   "seed-tts-2.0"
  },
  "input_snapshot": {
    "inventory_used": [{"batch_id":"B012","ing_id":"ING_CHICKEN_BREAST","qty_g":200}],
    "restrictions": {"allergens":["花生"],"dislikes":["香菜"]}
  },
  "nutrition": {
    "target":   {"kcal":600,"protein_g":30,"veg_g":200,"dark_veg_g":100},
    "provided": {"kcal":578,"protein_g":34,"veg_g":220,"dark_veg_g":200},
    "gap_before": {"protein_g":42,"veg_g":180,"dark_veg_g":200}
  },
  "menu": {
    "title": "嫩煎鸡胸 · 蒜蓉西兰花 · 杂粮饭",
    "dishes": [{"dish_id":"D_0142","name":"嫩煎鸡胸","role":"protein",
                "uses":[{"ing_id":"ING_CHICKEN_BREAST","qty_g":160}]}],
    "why": ["鸡胸 160g 补足 34g 蛋白质缺口",
            "西兰花 200g 全是深色蔬菜，补上今天缺的",
            "杂粮饭 GI 低，符合减脂目标"],
    "score": 86.4,
    "rejected": [{"dish":"宫保鸡丁","reason":"含花生（过敏原）"}]
  },
  "shots": [
    {"shot_id":"S01","type":"raw","dur":5.4,
     "narration":"...","image_prompt":"...",
     "asset":"images/S01_raw.png","degrade_level":0}
  ],
  "timeline": {"total":58.6,"audio":"audio/voice.mp3","subtitle":"subs/final.srt"},
  "cost": {"cny":0.86,"by_stage":{"image":0.66,"chat":0.02,"tts":0.18}}
}
```

---

## 10. 推荐逻辑

### 10.1 为什么不让模型选菜

| 维度 | LLM 直接选 | 结构化打分 |
|---|---|---|
| 营养数值 | 会编、会算错、不自洽 | 查表累加，可对账 |
| 过敏原 | 偶尔漏判（致命） | 硬过滤，0 容忍 |
| 可复现 | 每次不同 | seed + 快照 |
| 可解释 | "我觉得这样好" | 每条理由对应一个 fact |
| 成本 | 一次长推理 | 本地毫秒级 |

**LLM 只负责把结构化事实翻译成人话。**

### 10.2 五步流水

**Step 1 可用集**：`status='active' and qty_g>0 and not expired`，附 `expire_urgency = clamp(1 - days_left/7, 0, 1)`

**Step 2 硬过滤（不可覆盖）**
```
过敏原命中 → reject "含过敏原X"
缺食材     → reject "缺食材X"（附替代建议）
超烹饪时长 → reject
违反饮食类型/宗教禁忌 → reject
```

**Step 3 角色槽枚举**：`[protein, veg, staple, (soup|side)?]` → 8×10×4 ≈ 320 组合，无需 ILP；超 2000 用 beam search。

**Step 4 加权打分**
```
f1 缺口覆盖   0.30   Σ min(provided/gap,1) / |N|
f2 膳食指南   0.20   蔬菜∈[300,500] +0.4｜深色≥1/2 +0.3｜油≤上限 +0.2｜盐≤上限 +0.1
f3 食材新鲜度 0.15   mean(expire_urgency)
f4 多样性     0.15   1 - Jaccard(近7日菜单, 本菜单)
f5 时间成本   0.08   1 - cook_min/max_cook_min
f6 口味轮换   0.07   与昨日主味型不同的比例
f7 库存利用   0.05   使用食材数/库存种类数
```
权重在 `assets/nutrition/weights.json`，可 `--weights` 覆盖。**默认值来自"减脂+工作日"，其他场景须调参。**

**Step 5 出 Top3 + rejected 原因**（"为什么没选宫保鸡丁"同样有价值）。

### 10.3 生熟比与可食部（最易错）

- 200g 生鸡胸 → 熟约 140g（`cooked_ratio=0.70`）
- "1 根胡萝卜" → 可食部 0.94 × 估重 150g ≈ 141g
- 成分表是**生重每 100g 可食部**：先折可食部 → 再乘生重 → 最后才谈熟重
- bundle 里必须同时记录 `raw_weight / cooked_weight / edible_ratio`，否则用户会发现"你说的 30g 蛋白我算不对"

**交叉验证（新增，直连后才做得到）**：narrator 写出的任何数字，进 `script.json` 前必须能被 `nutrition.facts` 反查命中，否则**拦截重写**。这条用代码强制，不靠 prompt 约束。

---

## 11. 图片生成（Seedream 5.0 pro）

### 11.1 接口事实（已核实）

| 项 | 值 |
|---|---|
| 端点 | `POST https://ark.cn-beijing.volces.com/api/v3/images/generations` |
| 鉴权 | `Authorization: Bearer $ARK_API_KEY` |
| Model ID | `doubao-seedream-5-0-pro-260628`（质量档）<br>`doubao-seedream-5-0-flash-260915`（降级档，更快更便宜） |
| 分辨率 | 1K / 1.5K / 2K |
| 输出格式 | png / jpeg |
| 限流 | IPM 500 张/分钟 |
| 能力 | 文生图 ✓｜单/多图生图 ✓｜**多图融合** ✓｜**交互编辑（框选/点位/坐标）** ✓｜图层拆分 ✓｜原生多语种 ✓ |
| 不支持 | 文生组图、流式输出、联网搜索 |

请求体：
```jsonc
{
  "model": "doubao-seedream-5-0-pro-260628",
  "prompt": "……",
  "size": "1080x1920",
  "output_format": "png",
  "watermark": true,
  "image": ["<参考图1>", "<参考图2>"]   // 文生图时省略；多图融合时传数组
}
```

> **v1.3 定稿：原生 9:16，不再"先出 2:3 再裁"。**
> 已实测 `"1080x1920"` 被接受并精确返回 1080×1920（ratio 0.5625），
> 与成片同尺寸，**零缩放零裁切**，锐度和构图都不损失。
> 早期"生成 1024x1536 再裁到 9:16"的方案作废——那样盘沿左右各切 8%，
> 每次都损画质，且裁切位置不可控。
> 尺寸阶梯（全部精确 9:16）：`1080x1920` → `1152x2048` → `864x1536`，
> 首选被拒时自动降档，返回后再统一 conform 到 1080×1920。

### 11.2 四类图（叙事节拍，不是"风格"）

| 类型 | 画面 | 叙事作用 | 镜头 |
|---|---|---|---|
| `hero` 特写 | 成品局部近景，看不到完整盘沿，断面/颗粒细节 | 开场 3 秒钩子 | 贴到一臂内，浅景深强虚化，缓慢推近 |
| `raw` 原料图 | 未加工食材按份量摆放 | "我今天有什么" | 俯拍 90°，全景，缓慢下摇 |
| `process` 过程图 | 食材在锅中的状态，有热气油花 | "这一步在干什么" | 45° 侧俯，中景，缓慢推近 |
| `final` 成品图 | 装盘 + 主食 + 热气 + 侧逆光 | "做好了，想吃" | 45°，全景，缓慢上摇 |

三处实测得来的硬约束：

1. **`process` 不要画手。** 原方案写"手部动作"，实际是最容易触发内容审核的元素，
   一旦被拒整条流程停下。改成只拍锅与食材，一样能表达"正在做"。
2. **`hero` 用 `input_fidelity=medium`。** 高保真会把构图钉死在参考图上，
   导致"要特写却给全盘"。一致性让位给景别时，必须降 fidelity。
3. **画面里必须有主食。** 营养计算含 60g 生米（熟重约 138g），
   成品图若只有菜，观众对不上"561 千卡"的出处。

过程图必须是**动作**而非食材堆叠，否则视频变成 PPT。

### 11.3 风格基线（Style Bible，全局唯一）

```
自然光美食摄影，侧逆光，色温 4800K 暖白，浅木纹桌面 + 亚麻餐布，
白瓷哑光器皿（同一套），f/2.8 浅景深，35mm 等效视角，轻微胶片颗粒，
真实质感，不过曝，主体居中且四周留 12% 安全边距，
画面中不出现任何文字、水印、logo、餐具品牌标识
```

`style_version` 参与 hash：改基线 = 新版本 = 缓存失效。避免"改了风格还命中旧图"的诡异 bug。

### 11.4 提示词模板

**原料图**
```
{style_bible}。俯拍视角，浅木纹桌面铺亚麻餐布，白瓷盘上分组摆放未加工食材：
生鸡胸肉 160g、西兰花 200g 切小朵、蒜 2 瓣。食材保持原始形态，有水分光泽，分组留白清晰。无文字。
```
**过程图**
```
{style_bible}。45 度侧俯中景，同一块亚麻桌布与白色砧板，
一双手正在将西兰花小朵推入沸水锅中，锅口有蒸汽升起，
食材表面有水珠，动作进行中，轻微运动模糊。无文字。
```
**成品图**
```
{style_bible}。45 度近景，同一只白瓷哑光浅盘，盘中为嫩煎鸡胸配蒜蓉西兰花：
鸡胸切片呈扇形叠放，西兰花围边，淋少量酱汁。表面有热气，侧逆光勾出边缘轮廓，色泽自然。无文字。
```

### 11.5 一致性：参考链 + 交互编辑（比 v1.0 更强）

```
① raw     文生图，无参考
② process image = [raw]，              → 保住器皿/桌面/色调
③ final   image = [raw, process]，     → 多图融合，保住"同一道菜"
```

**新增能力：交互编辑兜底。** 若成品图器皿/摆盘跑偏，不必整张重生成——Seedream 5.0 支持框选/点位局部重绘：

```
"保持构图不变，将画面右侧的盘子替换为同一只白瓷哑光浅盘，其余不变"
+ image = [成品图] + 框选坐标
```
这比"重生成 3 张"省 2/3 成本，且结果更可控。**这是直连 API 拿到的实质红利，v1.0 用 Agent 生图做不到。**

### 11.6 成本与止损

- 按张计费，单价以方舟控制台账单为准（不在此估算）。
- `image` verb 返回 `cost_cny` 并累加进 `costs` 表；`--max-cost 2.0` 超预算即中止后续阶段。
- 缓存命中（同 hash）= 0 成本。同一道菜复做几乎免费。
- 一集默认只出 3 张（1 道主菜 × 3 拍）。多道菜用"拼图法"：一张原料图放全部食材。

### 11.7 合规：水印不可关（重要）

国内生成式 AI 内容标识有强制要求（《人工智能生成合成内容标识办法》，2025-09-01 施行）。因此：

- **不要为了画面干净把 `watermark` 关掉**，也不要后期裁掉。
- 建议在片上常驻角标 `AI 生成`（ASS 底部小字），并保留文件元数据标识。
- 这一点写进 `assets/compliance.md`，render 阶段自动加角标，避免"某天想发出去才发现要重做"。

---

## 12. 音频生成

### 12.1 落地方式：直连 openspeech（已实现并验证）

| 方式 | 说明 | 结论 |
|---|---|---|
| **A 直连 openspeech** | 自己实现 HTTP + NDJSON 流解析，零外部依赖 | ✅ **已采用**，`clients/speech_doubao.py` |
| B 防腐层包第三方脚本 | 内部调用豆包官方示例脚本 | 放弃：语音体系有两套凭据，包一层反而把诊断信息藏起来 |

实际代码量比预估小得多：请求体 + header 约 30 行，NDJSON 累加约 15 行。
"协议细节要啃 2–3 天"这个判断偏保守了——真正的成本不在这，而在 §C.2 那个鉴权误判。

对外只有三个函数，`core/` 不认识任何供应商：

```python
cred = speech_doubao.cred_from_env()          # 自动判定 A/B 两套体系
clips = speech_doubao.tts_lines(lines, out, cred, speaker)   # 逐句合成 -> [(mp3, 秒)]
speech_doubao.assemble(pack, clips, wav)      # 按 vstart 落轨，合成整条音轨
```

### 12.2 不用"音频生成模型"，用逐句 TTS —— 因为句长本身就是时间戳

原方案的主选是 `seed-audio-1.0`，唯一理由是它能返回句级/词级时间戳。
**实测后这条理由不成立**，或者说：它解决的问题可以用更便宜的方式消掉。

关键洞察：**逐句合成时，每句音频的时长就是它的时间戳。**
一段 4.03 秒的音频放在 0.00s，那么这句话就精确地占 0.00–4.03s，
根本不需要服务端告诉我时间戳——`ffprobe` 读一下文件头就有了。

| 方案 | 时间戳来源 | 精度 | 代价 |
|---|---|---|---|
| 整篇合成 + 服务端句级时间戳 | 接口返回 | 依赖接口 | 需要音频生成模型；句数可能漂移，要 difflib 对齐 |
| 整篇合成 + 字数估算 | 自己算 | ±100–300ms，观众能看出来 | — |
| **逐句合成 + 读每句时长** | `ffprobe` | **采样点级** | 多几次 HTTP 请求（4 句 ≈ 4 次，每次 <2s） |

实测验证（4 句、19.54s 成片）：

```
第1句 vstart= 0.00 音频=4.06s 区间=4.68s 余量=0.62s  OK
第2句 vstart= 4.76 音频=4.27s 区间=4.74s 余量=0.47s  OK
第3句 vstart= 9.58 音频=3.86s 区间=4.33s 余量=0.47s  OK
第4句 vstart=13.99 音频=4.85s 区间=5.43s 余量=0.58s  OK

音频实际发声起点偏差：+40ms / +80ms / +50ms（TTS 自带的起始静音，非排轴误差）
```

逐句还有一个附带好处：**改一句只重跑那一句**，配合音频缓存，调文案时不重复付费。

### 12.3 时长必须按音频反推，不能用「音频 + 固定值」

这是接上真实语音后暴露的第一个 bug。镜头时长原本写成 `dur = 音频 + 0.55`，
但独占区间是 `dur - 转场占用(0.38) = 音频 + 0.17`——**只多 0.17 秒**。
而同一句话两次合成的时长有 ±5% 抖动（4 秒的句子就是 ±0.2 秒），
一旦偏长，句尾会被下一镜的转场吃掉。表现为"最后一个字听不清"，画面字幕全正常，极难发现。

改为按约束反推，并加不变量检查：

```python
floor = audio + TAIL_AIR(0.10) + TRANSITION(0.30) + 0.08
dur   = max(MIN_DUR, audio + VOICE_PAD(0.85), floor)
...
# 逐镜检查，宁可报错也不静默截断
if room + 1e-6 < s["audio_sec"]:
    raise ValueError(f"第 {s['i']} 镜音频超出区间，句尾会被截断")
```

改完后余量从 0.17s 提到 0.47–0.62s，同时对"音频比预估长"有了真实容忍度。

### 12.4 参数

| 参数 | 值 | 理由 |
|---|---|---|
| speaker | `zh_female_xiaohe_uranus_bigtts`（ASMR 轻声）或 `zh_female_vv_uranus_bigtts`（情感丰富） | 别用客服音念美食 |
| `speech_rate` | `int((speed-1)*100)` | **注意是百分比整数**，直接传 1.0 会被当作 +1%，等于没调速 |
| 格式 | mp3 / 24000Hz / 单声道 / 64kbps | 与拼轨采样率一致，零重采样 |
| BGM | 不在本阶段生成 | 渲染层统一铺，保证连续 |

### 12.5 BGM 与混音（渲染阶段）

`sidechaincompress` 闪避 + `loudnorm=I=-16:TP=-1.5:LRA=11`。详见 §14.4。

### 12.6 失败与降级

| 现象 | 原因 | 处置 |
|---|---|---|
| `45000010 Invalid X-Api-Key` | **两种可能**：① 用错凭据体系（旧版 APP ID vs 新版 UUID）；② 应用/项目下未开通语音合成。二者同码 | `cred_from_env` 自动判定体系并在日志打印；`tools/keycheck.py` 分别试两套 |
| `45000030` | 缺 `X-Api-Resource-Id` | 补 `seed-tts-2.0` |
| `55000000 resource mismatched` | speaker 与 resource id 不匹配 | 2.0 音色统一配 `seed-tts-2.0` |
| 音频超出镜头区间 | 见 §12.3 | 不变量检查直接报错，不静默截断 |
| 无任何语音凭据 | 未配置 | **不失败**：降级占位音轨（静音 + 分镜提示音），manifest 标 `degraded: true` 与原因 |
| 想听"是否对齐"但没有人声 | — | 占位轨在每个 `vstart` 放一声提示音，叮声即开口点，是声画对齐的听觉校验 |

---

## 13. 字幕流程

### 13.1 归一化

```
subtitle_raw.json（句级/词级）
 → [{idx, text, start_ms, end_ms, words[]}]
 → 按 shot 归属切分（§12.3 对齐结果）
 → 断行 → CPS 校验 → SRT + ASS
```

### 13.2 断行规则（中文竖屏）

| 规则 | 值 |
|---|---|
| 单行最大字数 | 14 字（1080 宽 / 52px / 90% 安全区） |
| 单条最多行数 | 2 |
| 最短停留 | 0.8s ｜ 最长 4.0s |
| CPS | ≤ 9 字/秒，超出拆条或延长 |
| 断行优先级 | 标点 > 语义停顿（的/了/着 后）> 均分 |
| 禁止 | 行首标点、行尾单字、数字单位被拆（"160g"不可断） |

### 13.3 输出两份

- `final.srt` —— 外挂，可关、可上传平台
- `final.ass` —— 烧录用：
  ```
  PlayResX:1080 PlayResY:1920
  Style: Main,SourceHanSansSC-Bold,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,80,80,260,1
  ```
  白字 + 3px 半透明黑描边（浅色菜品上也读得清），底边距 260px（避开手机底部条）。

### 13.4 质检

- 字幕条数 == 句数（对齐后）
- `end > start`，相邻无重叠
- 最后一条 `end ≤ 音频时长 + 0.1s`
- 抽检 3 条：字幕文本 == 脚本原文（**字幕跟脚本走，不跟 TTS 走**，防读错字导致不一致）

---

## 14. 视频合成

### 14.1 时间轴

```
shot_k.dur = (end_ms - start_ms)/1000 + 0.3      # 尾部气口
total = Σ dur - (n-1) × 0.4                       # 0.4s 交叉转场
```

### 14.2 单分镜动效

```bash
ffmpeg -y -loop 1 -framerate 30 -t ${DUR} -i shot01.png \
  -vf "scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,\
zoompan=z='min(1+0.0009*on,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=30,\
format=yuv420p" \
  -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -r 30 shots/shot01.mp4
```

**三个必踩的坑**
1. `zoompan` 前不 `scale` 到 2× 会**抖**（整数像素取整）。
2. `d=1` 配 `-loop 1` 才是"每帧推一点"；`d=125` 会变成"一张图停 125 帧"。
3. `-pix_fmt yuv420p` 必须显式给，否则部分播放器黑屏。

节奏：`raw` 缓推（0.0006）、`process` 稍快（0.0012）、`final` 缓拉（`max(1.15-0.0009*on,1)`）。**动效方向交替**，避免都往前推的单调感。

### 14.3 拼接

```bash
ffmpeg -y -i s1.mp4 -i s2.mp4 -i s3.mp4 -filter_complex "\
[0:v][1:v]xfade=transition=fade:duration=0.4:offset=${OFF1}[a];\
[a][2:v]xfade=transition=fade:duration=0.4:offset=${OFF2}[v]" \
 -map "[v]" -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p video_xfade.mp4
```
`OFF1 = d1-0.4`，`OFF2 = OFF1+d2-0.4`。只用 `fade`/`dissolve`；`wipe`/`slide` 在美食内容里显得廉价。

### 14.4 音频（人声 + BGM 闪避 + 响度）

```bash
ffmpeg -y -i audio/voice.mp3 -stream_loop -1 -i assets/bgm/light_01.mp3 \
 -filter_complex "\
[0:a]aresample=48000,loudnorm=I=-16:TP=-1.5:LRA=11[vo];\
[1:a]aresample=48000,volume=0.12,atrim=0:${TOTAL}[bg];\
[bg][vo]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck];\
[vo][duck]amix=inputs=2:duration=first:normalize=0[a]" \
 -map "[a]" -c:a aac -b:a 192k audio_mix.m4a
```

### 14.5 合片 + 烧字幕 + AI 标识

```bash
ffmpeg -y -i video_xfade.mp4 -i audio_mix.m4a \
 -vf "ass=subs/final.ass:fontsdir=assets/fonts" \
 -map 0:v -map 1:a -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p \
 -c:a aac -b:a 192k -shortest -movflags +faststart \
 out/2026-10-30_dinner.mp4
```
- `-shortest` 防尾部黑屏；`+faststart` 让网页/微信预览秒开。
- ASS 里已含"AI 生成"角标（§11.7 合规要求）。

### 14.6 自动质检

| 检查 | 方法 | 阈值 |
|---|---|---|
| 时长 | `ffprobe format=duration` | 与 total 差 < 0.5s |
| 分辨率/帧率 | `ffprobe -select_streams v` | 1080×1920 / 30fps |
| 音轨存在 | `ffprobe -select_streams a` | ≥1（无配音时豁免） |
| 响度 | `ebur128` | I ≈ -16 ±1 LUFS |
| 首尾非黑帧 | 抽首末帧算亮度均值 | > 20（0–255） |
| 字幕渲染成功 | 抽 3 帧检测底部像素变化 | 有变化 |
| AI 标识存在 | ASS 内容含标识行 | 必须 |

任一项不过 → 返回 `ok:false` 并指明项，**不静默交付**。

---

## 15. 成本模型

### 15.1 记账

每次外部调用写 `costs` 表（stage / model / units / unit_type / amount_cny / latency / request_id），run 结束汇总进 `plan_bundle.cost`。**直连的最大副产品就是成本变得可精确归因**——能回答"这条视频哪一步最贵"。

### 15.2 单集预算（60s，3 分镜，1 道主菜）

| 环节 | 耗时 | 计费口径 |
|---|---|---|
| ingest + 营养 + 推荐 | < 2s | 0 |
| 文案（chat） | 5–15s | 按 token |
| **图片 ×3（Seedream pro）** | 60–120s（并发3） | **按张 × 单价** |
| TTS 全片 | 8–20s | 按字符/时长 |
| ffmpeg | 40–90s | 0 |
| **合计** | **约 2.5–4 分钟** | 以控制台账单为准 |

单价不在方案里编数字——以火山控制台账单为准。脚本负责**按张/按 token 精确累计**，并提供 `--max-cost` 硬止损。

### 15.3 省钱杠杆（按效果排序）

1. **缓存**（同 hash 复用）：复做同一道菜 ≈ 免费
2. **只出 3 张**：多道菜用拼图法，不做每菜 3 张
3. **降级档**：非封面图用 flash 模型
4. **交互编辑代替重生成**：器皿不对时局部重绘，省 2/3
5. **dry-run 预演**：`--dry-run` 打印将发生的调用与预估，不花钱

---

## 16. 失败模式与降级矩阵

| 失败点 | 检测 | 处置 |
|---|---|---|
| 食材名无法映射 | `E_ING_UNKNOWN` | Top3 候选 → 用户确认 → 写别名表（越用越准） |
| 库存凑不出菜 | 候选=0 | 输出"最小补货清单"（差哪 2 样、买了能做什么），不硬凑 |
| 图片 401 | `doctor` 预检 / 响应码 | 提示 ARK_API_KEY 未配或过期 |
| 图片 429 | 响应码 | 指数退避重试 3 次，仍失败则降并发 |
| 图片内容审核拒绝 | 响应码 | **不重试**（浪费钱），直接 L2 简化 prompt |
| 图片生成超时 | 120s | L2 → L3 换 flash → L4 占位 |
| 图糊/变形 | 拉普拉斯方差 < 阈值 | 降不降 fidelity / 换机位 |
| 配音 401 | `45000010` | 两种成因同码：凭据体系用错 ／ 模型未开通。用 `tools/keycheck.py` 鉴别 |
| 配音无 key | 配置缺失 | **跳过不失败**：无声视频 + 字幕，标注 skipped |
| 字幕溢出 | CPS/字数 | 二次切分 + 缩字号 1 档 |
| 音画不同步 | 时长差 > 0.5s | `-shortest` 兜底 + 重算时间轴 |
| ffmpeg/字体缺失 | `doctor` 预检 | 退出码 3，明确装什么 |
| 中途中断 | `runs.state` | `resume` 断点续跑，已生成资产复用 |

**总原则：任何一环失败都不能让流水线崩掉，必须给出用户能理解的下一步。**

---

## 17. 实施路线

**Phase 0（半天，必须先做）**
`doctor` 打通：验证凭据、ffmpeg、字体、Seedream 尺寸参数、字幕 JSON 结构。
**产出：一份"哪些假设成立/不成立"的实测报告。** 这一步能把后面 80% 的返工挡掉。

**Phase 1（1 周，打通主链路）**
`init → template → ingest → plan → script → image(1张) → voice → subs → render(15秒)`
验收：给一份模板 xlsx，一条命令产出 15 秒竖屏视频。

**Phase 2（1 周，质量）**
三图参考链 + 交互编辑兜底、xfade 节奏、ASS 样式、BGM 闪避、ffprobe 质检
验收：60 秒成片，三条不同菜单，风格一致。

**Phase 3（1 周，健壮）**
断点续跑、缓存命中、四级降级全覆盖、`--max-cost` 止损、反馈回写

**Phase 4（可选）**
`clients/speech.py` 换成直连、菜谱库扩到 300 道、多餐次合成"一天 vlog"、包一层 SKILL.md

---

## 18. 风险与未决项

| 风险 | 影响 | 缓解 |
|---|---|---|
| **菜谱库是冷启动硬门槛** | 有食材无菜谱 → 推荐不出 | 内置 150 道家常菜；无匹配时用"烹饪手法矩阵"（炒/蒸/煮/烤/凉拌 × 主料 × 配料）合成候选标 `needs_review` |
| 营养成分表授权 | 中国食物成分表有版权 | 自建 250 条常用子集（公开资料整理），标注"估算值，非医学建议" |
| 生图与真实菜品不符 | 信任受损 | 定位为"氛围示意图"，片尾标注 AI 生成 |
| 健康建议合规 | 涉健康建议 | 定位"搭配辅助"，不宣称疗效；特殊人群提示咨询专业人士 |
| **AI 内容标识合规** | 可能违规 | 保留水印 + 片上"AI 生成"角标 + 元数据标识（§11.7） |
| 模型版本漂移 | 输出风格突变 | `model_snapshot` 记录到 bundle；升级前跑回归对比 |
| 成本失控 | 能跑=能烧钱 | `--max-cost` 硬止损 + dry-run + 缓存 |

**Phase 0 必须先验证的四个假设**：
1. `ARK_API_KEY` 能否同时调 `images/generations` 与 `chat/completions`（应为能）
2. `size` 是否接受 `"1024x1536"`，还是只能 `1K/1.5K/2K`
3. `image` 传数组是否即多图融合，两张参考图效果如何
4. 豆包语音字幕 JSON 的实际字段名与粒度（句级？词级？）

---

## 附录 A · `.env.example`

```ini
# 方舟：图像生成 + 文案生成（console.volcengine.com/ark → API Key）
ARK_API_KEY=ark-xxxxxxxxxxxxxxxx

# 豆包语音：配音。两套体系任选其一，A 优先
# A) 旧版：语音技术 → 应用管理（实测可用）
VOLC_SPEECH_APP_ID=
VOLC_SPEECH_ACCESS_TOKEN=
# B) 新版：豆包语音 → API Key 管理（UUID）
VOLC_SPEECH_API_KEY=

# 可选：默认模型与预算
DIET_IMAGE_MODEL=doubao-seedream-5-0-pro-260628
DIET_IMAGE_MODEL_FALLBACK=doubao-seedream-5-0-flash-260915
DIET_CHAT_MODEL=doubao-seed-2-1-pro-260915
DIET_TTS_SPEAKER=zh_female_xiaohe_uranus_bigtts
DIET_MAX_COST_CNY=2.0
```

## 附录 B · 端到端命令

```bash
python dietctl.py doctor                                  # 先自检凭据与依赖
python dietctl.py init
python dietctl.py ingest --file ~/Downloads/我的食材.xlsx --user U001
python dietctl.py plan   --user U001 --date 2026-10-30 --slot dinner --top 3
python dietctl.py commit --run 2026-10-30_dinner --candidate 1
python dietctl.py script --run 2026-10-30_dinner --secs 60
python dietctl.py image  --run 2026-10-30_dinner --types raw,process,final --max-cost 1.0
python dietctl.py voice  --run 2026-10-30_dinner
python dietctl.py subs   --run 2026-10-30_dinner
python dietctl.py render --run 2026-10-30_dinner --res 1080x1920

# 或者一条命令到底（含 dry-run 预演）
python dietctl.py run --user U001 --date 2026-10-30 --slot dinner --auto --dry-run
python dietctl.py run --user U001 --date 2026-10-30 --slot dinner --auto
```

---

## 附录 C · Phase 0 实测结论（2026-10-30，本机验证）

`scripts/smoke.py` + `clients/ark_image.py` 实跑结果，替代原来的"待验证假设"清单。

### C.1 已证实

| 假设 | 结果 | 证据 |
|---|---|---|
| ARK Key 有效 | ✅ 通过 | 去掉重复前缀后鉴权成功；格式错时报 `401 AuthenticationError: The API key format is incorrect` |
| `size` 支持显式尺寸 | ✅ **支持** | `"1024x1536"` 精确返回 1024×1536（比例 0.6667）；错误信息明示 `must be 'WIDTHxHEIGHT' or a supported size preset` |
| **`1080x1920` 原生 9:16** | ✅ **支持** | 精确返回 1080×1920，ratio 0.5625；四张图（raw/process/final/hero）全部一次命中首选尺寸，未触发降档 |
| 风格基线可控 | ✅ 命中 | 白瓷哑光盘 + 亚麻餐布 + 浅木纹 + 侧逆光，与 §11.3 一致 |
| 水印存在 | ✅ 在 | 右下角「AI 生成」，符合 §11.7 合规要求 |
| 多图融合可用 | ✅ 可用 | 成品图带「原料图+过程图」两张参考（`input_fidelity=high`），同盘同布同光，一致性成立 |
| `input_fidelity` 副作用 | ⚠️ 已确认 | `high` 会把构图钉死在参考图上，要求"特写"却返回全盘 —— 景别优先级高于一致性时必须降到 `medium` |
| 返回体形态 | `data[0].url` | 预签名 URL，必须立刻落盘 |
| 性能 | **32s / 57s / 59s / 88s** | 依次为 raw / final / hero / process；带 2 张参考的过程图最慢。**参考链是串行的**（后一张依赖前一张），4 张约 3.5 分钟 |

**由此得到的硬约束**：单张 30–90s，且参考链天然串行，全流程 4 张约 3–4 分钟。
缓解手段不是并发（链式依赖做不到），而是**内容寻址缓存**：第二次跑同一套参数
全部命中，0 成本 0 等待（实测 `--reuse-images` 全流程 12s）。
改一句文案要重出片时，只有受影响的图会重新生成。

### C.2 语音鉴权：一次误判与纠正（重要）

**误判过程**：先按「新版控制台 API Key」实现，用 `X-Api-Key: <uuid>` 打
`/api/v3/tts/unidirectional`，始终返回 `45000010 Invalid X-Api-Key`。据此推断是
「该 key 所属项目下未开通语音合成模型」，方向错了。

**真相**：火山语音有**两套并存的凭据体系**，端点相同、header 不同：

| 体系 | 请求头 | 本账号 |
|---|---|---|
| A. 旧版：语音技术 → 应用管理 | `X-Api-App-Id` + `X-Api-Access-Key` | ✅ **HTTP 200，一次通过** |
| B. 新版：豆包语音 → API Key | `X-Api-Key: <uuid>` | ❌ `45000010` |

同一账号下，A 可用而 B 不可用，且**失败时二者报同一个错误码**——这就是误判的根源：
`45000010` 既可能是"体系不对"，也可能是"未开通模型"，仅凭错误码无法区分。
排查时必须两条线分别试，而不是假设其中一条成立。

实测通过的请求（A 体系）：

```python
headers = {
    "X-Api-App-Id":      APP_ID,        # 语音技术应用管理里的 APP ID
    "X-Api-Access-Key":  ACCESS_TOKEN,  # 同一页面的 Access Token
    "X-Api-Resource-Id": "seed-tts-2.0",
    "X-Api-Request-Id":  str(uuid.uuid4()),
}
payload = {"user": {"uid": "daily-diet"},
           "req_params": {"text": "...", "speaker": "zh_female_xiaohe_uranus_bigtts",
                          "audio_params": {"format": "mp3", "sample_rate": 24000}}}
```

响应是 **NDJSON 流**：每行一个 JSON，`code=0` 携带 base64 音频片段，
`code=20000000` 是结束标记。必须按行累加 `data` 再解码，把整体当 JSON 解析会失败。

实测参数：24kHz / 单声道 / mp3 / 64kbps，一句话约 4 秒 → 34KB。

> `sentence.words` 默认为空数组，即不带词级时间戳。本项目不依赖它——
> 逐句合成、用每句音频的真实时长排时间轴，精度已足够（见 §12.2）。

### C.3 仍未开通（非阻塞）

| 项 | 现象 | 处置 |
|---|---|---|
| **文案模型** | 账号 `2100384160` 未开通任何 chat 模型。探测 6 个在售模型（`doubao-seed-2-1-pro-260628/260915`、`2-1-turbo`、`2-1-lite`、`2-0-lite`、`deepseek-v4-1-flash`、`glm-5-3-flash`）**全部 `404 ModelNotOpen`** | 方舟控制台 → 开通管理 → 开通 1 个文案模型（建议 `doubao-seed-2-1-pro-260915`）。当前走 `template` 兜底，不阻塞出片 |

> 顺带发现：`GET /api/v3/models` 可用（200），但返回的是**模型目录的生命周期状态**（Active/Retiring/Shutdown），**不是账号开通状态**，不能用来判断"我开通了哪些"。判断只能靠实调。

### C.4 由实测产生的设计修正

| # | 修正 | 原因 |
|---|---|---|
| 1 | `.env.example` 去掉 `ark-` 字面前缀 | 这是重复拼接的根源，已改 |
| 2 | `narrator` 需要**双实现**：`ark_chat` ／ `template`（模板拼接，无 LLM） | 文案模型未开通时，用模板兜底仍能跑通全链路，先验证视频管线 |
| 3 | 生图并发写进 M5 规格 | 见 C.1 的 31.5s/张 |
| 4 | 存储按 png 估：3 张 ≈ 8MB/集 | 体积无压力；若要压体积，渲染后转 jpeg 即可（反正要重编码） |
| 5 | 水印在右下角 → 裁剪与转场不得切掉该区域 | 合规；另加 ASS 角标做双保险，不只依赖图片内水印 |
| 6 | **语音客户端支持双鉴权，按环境变量自动判定** | `45000010` 无法区分"体系错"与"未开通"；自动判定 + 日志打印实际体系，把诊断成本降到零 |
| 7 | **镜头时长按音频反推，而非「音频 + 固定值」** | 固定 0.55s 时，独占区间只比音频多 0.17s；同一句话两次合成有 ±5% 抖动，句尾会被下一镜转场吃掉。改为 `dur ≥ 音频 + 转场占用 + 句尾空气`，并加不变量检查 |

### C.5 复查命令

```bash
cd daily-diet
python tools/keycheck.py                # 语音凭据体检（两套体系都会试，给出结论）
python scripts/smoke.py doctor          # 零成本：凭据 + ffmpeg + 字体
python scripts/smoke.py image --yes     # 计费 1 张：验证生图
python run.py --user U001 --slot dinner --reuse-images --tts doubao   # 用缓存图出真人声片
python run.py --user U001 --slot dinner --reuse-images --tts mock     # 无语音时降级占位音轨
```
