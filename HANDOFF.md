# 交接文档 · 每日饮食出片工具

> 写给接手的 Agent / 开发者。本文档只陈述「事实与状态」，不含待办承诺。
> 项目根目录：`C:\Users\kim\WorkBuddy\2026-10-30-11-13-44\daily-diet`
> 最后更新：2026-10-30 14:42

---

## 0. 一句话现状

**代码与数据完整，recipe / combo 两条链路均 8/8 跑通，可出片、可移植。**
2026-10-30 下午修复了 7 个**只在真实使用中才暴露**的缺陷（见 §9），
其中「生图提示词不含菜名」「餐次不生效」「字体缺失静默回退」会直接让成片出错。
已实测：把最小可运行集拷到全新目录（无缓存）后，`doctor.py` 全绿、
真实生成 4 张图并 8/8 出片成功（241s）。

**接手第一件事**：`python tools/doctor.py`。

---

## 1. 这个项目是干什么的

从用户的**食材库存清单**出发，自动选出当天该吃的一道菜，生成 **9:16 竖屏短视频**
（配图 + 真人配音 + 字幕）。不需要 Agent，一条命令跑完；Agent 只是调用方之一。

| 模式 | 说明 | 特点 |
|---|---|---|
| `recipe`（默认） | 从 1093 道真实菜谱里挑一道，缺的食材用库存里的**同类平替** | 有真实步骤/分量，菜是真的 |
| `combo` | 从库存的蛋白/蔬菜/主食枚举组合，自己拼一道菜 | 永远做得出来、价格最可控，但无步骤 |

---

## 2. 目录结构（含每个文件的状态）

```
daily-diet/
├── run.py                 # CLI 入口
├── serve.py               # 零依赖 HTTP 配置后台（默认 http://127.0.0.1:8765）
├── 打开配置后台.bat        # 双击启动后台（自动释放端口，见 §6）
├── config.json            # 界面参数（后台可读写）
├── README.md              # 使用 + 移植说明（2026-10-30 重写，含移植专章）
├── HANDOFF.md             # 本文档
├── .env                   # 密钥（★ 不要外传）
├── .env.example           # 密钥模板
├── .env.bak / .bak2 / .bak3   # ← 遗留废备份，含旧密钥，确认无用请删
│
├── core/                  # 领域逻辑（纯函数为主，不读环境变量）
│   ├── foods.py           # 食材库加载/营养/价格/单位换算（读 data/ingredients.csv）
│   ├── lexicon.py         # 菜谱食材自由文本 → 结构化
│   ├── recipes.py         # 菜谱推荐器 + 平替引擎
│   ├── planner.py         # 组合模式（combo）的枚举打分器
│   ├── settings.py        # config.json 读写 + 默认值 + 夹紧校验 + run_args()
│   ├── narrator.py        # 四句旁白文案（template / from_recipe / ark_chat）
│   ├── prompts.py         # 生图提示词（按菜名+菜系+类别取措辞，见 §9.2）
│   ├── storyboard.py      # 分镜（转场时长由配置传入，见 §9.4）
│   ├── subs.py            # ASS/SRT 字幕（字号/字体/底框可配）
│   └── render.py          # ffmpeg 合成
│
├── clients/               # 外部 API 客户端（唯一知道"火山"的地方）
│   ├── ark_image.py       # 方舟生图（9:16 阶梯 + 参考链 + 内容寻址缓存）
│   ├── speech_doubao.py   # 豆包真人语音（双鉴权体系）
│   └── speech_mock.py     # 占位音轨
│
├── tools/                 # 维护脚本
│   ├── doctor.py          # ★ 环境体检（换设备先跑，见 §9.10）
│   ├── keycheck.py        # 凭据体检（真的发起调用）
│   ├── build_ingredients.py   # 从《中国食物成分表》重建食材库
│   ├── import_recipes.py      # meals.json → data/recipes.json
│   ├── make_inventory.py      # 生成示例库存
│   └── apply_inventory.py     # 覆盖库存清单（带重试+备份）
│
├── data/
│   ├── 01_食材清单_U001.csv   # 库存清单（30 条，已验证）
│   ├── 01_食材清单_U001.csv.bak / .bak.20261030_131741  # ← 备份，可删
│   ├── ingredients.csv        # 103 条食材库（含价格档）
│   ├── price_ref.csv          # 可编辑价格表
│   └── recipes.json           # 1093 道菜谱（包裹结构，见 §4）
│
├── web/                   # 配置后台前端（index.html / app.js / style.css）
│
├── cache/tts/             # 语音缓存（★ 注意：图片缓存不在这里）
└── runs/                  # 出片产物
    ├── _cache/            # ★ 图片缓存在这里 + costs.jsonl
    ├── 2026-10-30_红烧豆腐/  # 提示词修复后真实生成的成片（推荐看这个）
    ├── recipe_home/       # 旧提示词的缓存图
    └── 2026-10-30_dinner/ # 旧提示词的缓存图
```

> **缓存有两处**，别找错：语音 `cache/tts/`，图片 `runs/_cache/`。

---

## 3. 已完成的四块工作

### 3.1 食材库：1677 → 103 条（`data/ingredients.csv`）
- 来源：`E:\myProject\china-food-composition-data`（《中国食物成分表·第6版》）。
- 筛选：1677 → 1042 营养池 → 103 常用清单，全部命中。
- 角色：蔬菜 47 / 蛋白 32 / 主食 15 / 坚果 4 / 调味 3 / 乳品 2。
- 三条口径：干湿分开量（`DRY_ENERGY_CAP=520`）、深色蔬菜靠 `胡萝卜素≥300` 判、畜肉取瘦部位。
- 价格四档：经济 ≤10 / 常规 10–25 / 偏高 25–60 / 昂贵 >60 元/kg（39/52/6/6）。
- 价格是**外部注入**的，改 `price_ref.csv` 后跑 `python tools/build_ingredients.py --write`。

### 3.2 菜谱库 + 平替引擎（`core/recipes.py`）
- 来源：`E:\myProject\EveryDay_Food\public\data\meals.json`（1093 道，8 分类）。
- 平替：库存有→直接用；缺→同物种/同角色替换；替换菜名主角再打折；内脏限次。
- 熟重折算 `COOKED_STAPLE`：菜谱「米饭 200 克」是熟重，折算成生米再算营养。
- 价格三机制：超预算直接不排序、`cost` 扣分、昂贵/偏高配额。

### 3.3 配置后台（`serve.py` + `web/`）
- 零依赖（只用标准库 `http.server`），只监听 127.0.0.1。
- 路由：`/api/bootstrap` `/api/preview` `/api/runs` `/api/run` `/api/recipe`
  `/api/config`(POST) `/api/preview`(POST) `/api/run`(POST) `/api/run/stop`(POST)。
- 前端面板：参数、**密钥与凭据（脱敏预览）**、**当前全部参数**、库存、预览、出片、历史。

### 3.4 文案/提示词/字幕按类别取措辞
- 食材池 11→103、菜谱 1093，原硬编码措辞已全部改为按类别取。
- 字幕样式：`BorderStyle=3` 的半透明底框用 **`OutlineColour`** 的 alpha（写在
  `BackColour` 上无效，会变纯黑实心块）。

---

## 4. 关键数据契约（接手必读）

### 4.1 `recipes.json` 是包裹结构，不是裸数组
```json
{ "source": "...", "count": 1093, "recipes": [ ... ] }
```
`core/recipes.py` 的 `load()` 已按此结构读。**不要**当裸数组 `len(d)`（会得到 3）。

### 4.2 菜谱条目字段
`id / name / category / cuisine / emoji / mealType / methods / flavors / desc /
steps[] / kcal / nutrition{protein,carbs,fat} / per100g / serving / minutes /
mains[] / seasonings[] / role_mix / usable`

`mains[i]`：`raw / name / norm / grams / role / key`（key 对 `ingredients.csv` 的 foodCode）。

### 4.3 分类 id → 中文（`recipes.py` 的 `CATEGORY_LABEL`）
`fat-loss` 减脂 / `muscle-gain` 增肌 / `maintain` 维持 / `nutrition` 营养 /
`home` 家常 / `therapy` 食疗 / `bakery` 烘焙 / `snack` 小吃。

### 4.4 库存清单列
`食材名称 / 数量 / 单位 / 类别 / 购买日期 / 保质期至 / 存放位置 / 是否深色蔬菜 / 备注`。
名字用口语（「西蓝花」），由 `foods.py` 的 `COLLOQUIAL` 映射到正名。

### 4.5 配置分两处
- `.env`：只放**密钥** + 少量覆盖项（`DIET_TTS_SPEAKER` `DIET_BUDGET_CNY`
  `DIET_SUB_FONT` `DIET_SUB_*`）。只有人和 `run.py` 读它。
- `config.json`：**界面参数**，后台可读写，数值会被夹紧。

---

## 5. 常用命令

```bash
cd "C:\Users\kim\WorkBuddy\2026-10-30-11-13-44\daily-diet"

# ★ 换设备/改环境后第一件事
python tools/doctor.py

# 出片（真声 + 复用图 = 秒出、几乎不花钱）
python run.py --mode recipe --recipe-cats home --slot dinner --tts doubao --reuse-images

# 出片（真声 + 真实生图，4 张约 3-4 分钟，计费）
python run.py --mode recipe --recipe-cats home --slot dinner --tts doubao

# 列出可上传的食材名/单位/别名
python run.py --list-foods

# 启动配置后台
python serve.py                 # http://127.0.0.1:8765
python serve.py --port 9000 --open
# 或双击 打开配置后台.bat

# 凭据体检（真的发起调用）
python tools/keycheck.py

# 重建食材库（改了 price_ref.csv 后）
python tools/build_ingredients.py --write

# 重新导入菜谱库（改了 meals.json 后）
python tools/import_recipes.py
```

`run.py` 全部参数：`--user --date --slot --meal-types --inventory --chat
--tts(auto|mock|doubao) --reuse-images --run-id --budget --mode(recipe|combo)
--recipe-cats --list-foods`。

`--meal-types` 是 2026-10-30 新增：后台餐次多选时由 `settings.run_args()` 显式传下去，
否则会被单一 `--slot` 窄化（见 §9.1）。

---

## 6. 配置后台的启动方式（避坑）

**最省事**：双击根目录 `打开配置后台.bat` —— 自动释放端口、启动、开浏览器。

**手动**：
```powershell
cd "C:\Users\kim\WorkBuddy\2026-10-30-11-13-44\daily-diet"
python serve.py            # http://127.0.0.1:8765
```

- 后台任务里 `serve.py --port 8765` 多次报 **exit code 127**：端口被上次残留进程占用
  （服务本身没崩，是启动时端口已占用）。先停旧进程：
  ```powershell
  Get-NetTCPConnection -LocalPort 8765 -State Listen | % { Stop-Process -Id $_.OwningProcess -Force }
  ```
- 或用别的端口。服务只监听 127.0.0.1，不对外暴露。

### 6.1 密钥面板的安全取舍（重要）

后台只显示**脱敏预览**（`ark-****4548（46 位）`），不提供返回完整密钥的接口。

原因：服务虽只监听 127.0.0.1，但**没有 CSRF 防护**，本机任意网页都能向它发 POST。
一旦提供「返回完整密钥」的接口，等于给每个打开的网页开了一条读走 API Key 的路。
脱敏预览已能回答「配的是哪把钥匙、有没有粘错前缀」（历史上踩过 `ark-ark-` 重复前缀
导致 401）。要看完整值直接打开 `.env`。**改动这段前先想清楚这个边界。**

---

## 7. 遗留事项（非阻塞，按需处理）

1. **废备份文件**（含旧密钥，建议删）：`.env.bak` `.env.bak2` `.env.bak3`、
   `data/01_食材清单_U001.csv.bak`、`data/01_食材清单_U001.csv.bak.20261030_131741`。
2. **成片图的新旧**：`runs/2026-10-30_红烧豆腐/` 是提示词修复后**真实生成**的
   （4 张图实调，222.8s）。`recipe_home`、`2026-10-30_dinner` 的图是旧提示词缓存。
   核对图是否真实生成，看 `manifest.json` 的 `images.<role>.model` 与 `ms`，
   不要只看日志的 `[cache]`（见 §9.9）。
3. **文案模型未开通**：`--chat` 会 404 `ModelNotOpen`，自动回落 `template`。
   回落路径正常，但要真用模型改写文案需在方舟控制台开通任一 chat 模型。
4. **`config.json` 已重置默认**：`mode=recipe, categories=["home"], budget=25,
   tts=豆包小鹤`。
5. **两个工具脚本的默认数据源指向原开发机**（`tools/build_ingredients.py`
   `E:/myProject/china-food-composition-data`、`tools/import_recipes.py`
   `E:/myProject/EveryDay_Food/...`）。只在重建库时才需要，用 `--src` 传自己路径。
   日常出片不碰它们。

---

## 8. 验证结论（2026-10-30 14:42）

| 检查项 | 结果 |
|---|---|
| `compileall`（core/clients/tools/根） | 全部通过 |
| 13 个核心模块 import | 全部 OK |
| 食材库 `ingredients.csv` | 103 条，角色/价格档正确 |
| 菜谱库 `recipes.json` | 1093 道，8 分类，1079 道 usable |
| 库存 `01_食材清单_U001.csv` | 30 条，坏行 0 |
| `run.py` recipe 端到端 | 8/8，10.4s（复用图）/ 222.8s（真实生图） |
| `run.py` combo 端到端 | 8/8 |
| 三个餐次 breakfast/lunch/dinner | 各自出片，文案时间词与餐次一致 |
| 真实配音（APP ID + Token） | 4 句，语速参数生效 |
| `serve.py` 8 个 API | 全部实测可用 |
| `tools/doctor.py` | 14 项检查，本机全 OK |
| **模拟移植（全新目录、无缓存）** | **doctor 全绿，真实生成 4 图，8/8 出片** |

---

## 9. 2026-10-30 下午修复的 7 个缺陷

每条都写「怎么发现的 / 根因 / 怎么验证」，避免下一个人当成已修好却其实复发。

### 9.1 餐次形同虚设（影响所有非晚餐场景）

- **现象**：`--mode recipe --slot lunch` 出的还是晚餐菜。
- **根因**：`run.py` 原来 `if not args.mode: cfg["meal_types"] = [args.slot]`。
  只在**没写** `--mode` 时同步餐次，而 `--mode recipe` 恰恰是文档和后台最常用的写法，
  于是 `meal_types` 永远留在 `config.json` 的 `["dinner"]`。
  实测三个餐次全选中「红烧豆腐」，文案却写「早上七点」——同一份成片自相矛盾。
- **修复**：三档优先级 —— `--meal-types` 显式给了就照用；否则配置只挂一个餐次时
  由 `--slot` 决定；配置明确多选则尊重配置。
- **验证**：`--slot breakfast` 现在选中「蛋饼」（早上七点），三餐次各自不同；
  后台多选走 `--meal-types`，不再被窄化。

### 9.2 生图提示词里没有菜名（最贵的一条）

- **现象**：换一道菜，画面不变；`--reuse-images` 直接命中上一道菜的缓存图。
- **根因**：`core/prompts.py` 四个镜头位只有**食材级**描述，一个菜名都不含。
  而缓存键 = `prompt + size + refs 哈希`，于是红烧豆腐/麻婆豆腐/家常豆腐共用一张图，
  **换菜谱时直接复用上一道的图，等于白拿一张错的图**。
- **修复**：新增 `_dish_tag()`，把「这道菜是「红烧豆腐」，一道家常菜，做法是煎／红烧。」
  前置到 `process / final / hero`。`raw`（生食材俯拍）**故意不带** —— 生料本来就该长一样，
  带上只会让缓存永不命中、多花一份钱。另把固定的「撒黑胡椒与欧芹碎」换成按菜系取
  （`GARNISH` 表）—— 那是西餐收尾，套到红烧豆腐上画面明显不是这道菜。
- **验证**：3 道同类豆腐菜提示词两两不同且各含自己的菜名；真实调 Seedream 生成 1 张
  确认可出图（1080x1920，38.6s），与旧图哈希不同。

### 9.3 文案第 3 句 100% 缺句号、34.8% 被截成半句

- **根因**：`from_recipe()` 里 `_short(key)` 返回值没补句号（其他三句都有）。
  更实质的是挑选逻辑：先按「含烹饪动词」挑一条，再 26 字硬截断，
  于是「平底锅小火加热，刷薄薄一层橄榄油，放入鸡胸肉」这种半句被直接播出来。
- **修复**：新增 `_pick_step()` 改成**整句优先**（有动词且装得下 > 装得下 >
  有动词但超长 > 其他），截断后补句号。
- **验证**：400 道抽样，第 3 句缺句号 0 条；硬截断 34.8% → **2.0%**。

### 9.4 `video.transition` 配置完全没生效

- **根因**：`storyboard.build()` 内部直接引用模块常量 `TRANSITION`，`run.py` 只把
  `pack["transition"]` 传给渲染层，没把配置传**进**时间轴。这点尤其危险：
  字幕独占区间按「转场中点」算，只改渲染不改时间轴会直接错位。
- **修复**：`build()` 增加 `transition` 入参，`run.py` 从 `cfg["video"]["transition"]`
  读取并夹到 [0,1] 后传入。`core/` 仍不读环境变量。
- **验证**：配置 0.55 → `manifest.json` 的 `storyboard.transition == 0.55`，
  总时长随转场变化。

### 9.5 `tts.speed` 有配置项、有夹紧、有 UI，但从未传下去

- **根因**：`speech_doubao.tts_lines()` 有 `speed` 参数，`run.py` 调用时没传。
- **修复**：读取 `cfg["tts"]["speed"]` 并传入，日志打印实际语速。
- **验证**：speed 1.0 → 句长 `[3.24, 4.27, 4.85, 5.06]` / 总 19.17s；
  1.35 → `[2.28, 3.02, 3.6, 3.89]` / 总 14.53s。

### 9.6 `evaluate()` 不传 `by_key` 会崩

`core/recipes.py` 的 `evaluate(recipe, inv, cfg, by_key=None)` 是公开函数，
`plan()` 会传 `by_key`，但单独调用（后台看单道菜、脚本试算）走 `None` 分支时
崩在 `i["key"] in None`。已加 `_inventory()` 兜底。

### 9.7 `manifest.json` 的 `narration.source` 被覆盖成图片路径

- **根因**：`run.py` 合成循环写了 `src = images[s["image"]]["path"]`，而 `src` 在
  `[2/8]` 存的是文案来源（`template`/`ark_chat`）。同名变量被复用，manifest 落盘时
  读到的是最后那次赋值。危害：这是判断「文案到底走没走模型」的唯一入口，
  而它恰恰是 §7.3 里「模型没开通所以静默回落」时唯一能确认的字段。
- **修复**：合成循环改用 `img_src`。

### 9.8 通用教训：配置项「存在」≠「生效」

派生的 `settings.run_args()` 只负责拼 CLI 参数，**不代表配置生效**。
§9.4 / §9.5 正是「参数在配置里、在 UI 上、甚至被夹紧校验过，
但从来没被传到最后真正用它的人手里」。

验收标准应该是：**改配置 → 出一次片 → 在产物里读到变化**，而不是
「`config.json` 里有这个键」。本次用的核对方式：`subs.margin_v=420` → ASS 的
`Style: Main` 第 22 列；`video.transition=0.55` → `storyboard.transition`；
`tts.speed=1.35` → `audio.audio_sec` 明显变短。

### 9.9 真实生图与 `--reuse-images` 的缓存陷阱（曾自伤一次）

出片默认走内容寻址缓存，`--reuse-images` 更是**只认缓存**。意味着：提示词或参考链
一变旧图就不再命中；反之，**一旦有人把图塞进缓存键的位置，`--reuse-images`
会毫不犹豫地把它当作正确答案烧进成片**。

本次就发生过：为在改完提示词后跑通 `--reuse-images` 全链路，把旧图复制到新缓存键下
当占位（`model` 字段写 `placeholder`）。随后真出片时这些占位图被全部命中，
**产出了一条画面完全错误的成片**（日志里 4 张图都显示 `[cache]`）。

处置与教训：
- 所有占位缓存已按 `model` 字段含 `placeholder` 全部删除；
- **判断图是否真实生成，看 `manifest.json` 的 `images.<role>.model` 与 `ms`**，
  不要只看日志里的 `[cache]`；
- 不要用「复制旧图到新键」绕过缓存。需要跑通流水线又不花钱时，用 `--tts mock`
  并接受 `[5/8]` 因缺图退出，或明确接受成片不可用的后果。

### 9.10 中文字体缺失是**静默**的（移植头号坑）

- **现象**：换到没有中文字体的机器，成片照常输出、退出码 0、日志无异常，
  但画面里中文全是方块。
- **实测证据**（本机 ffmpeg 7.1 + libass 0.17.3，ASS 指定不存在的字体名）：
  ```
  fontselect: (NoSuchFontXYZ, 400, 0) -> ArialMT
  ```
  libass 静默回退 Arial。同一帧「缺字体」与「不缺字体」的渲染哈希确实不同
  （`B6611AF1…` vs `4CCC597F…`），证明确实是字体在起作用。
- **修复**：
  1. `core/subs.py` 的字体名从硬编码改为 `DEFAULT_FONT` + 可被 `DIET_SUB_FONT`
     覆盖（`run.py` 的 `sub_style_from_env()` 注入，`core/` 仍不读环境变量）；
  2. 新增 `tools/doctor.py`：用「渲染一帧小 ASS 并解析 fontselect 日志」的办法
     判断字体是否被回退 —— 跨平台通用，不依赖枚举系统字体。
- **附带踩到的坑**：`ass=` 滤镜的参数解析器把 `\` 和 `:` 当选项分隔符，
  Windows 绝对路径传进去会报 `Error applying option 'original_size' to filter 'ass'`。
  `doctor.py` 里必须 `cwd=` 切到目录并用相对文件名。
- **验证**：本机 `doctor.py` 报
  `请求 Microsoft YaHei → 实际 MicrosoftYaHei-Bold`（OK）；
  设 `DIET_SUB_FONT=NoSuchFontXYZ` 能正确报 FAIL 并给出修法。

---

## 10. 移植到其它设备

完整版见 `README.md` 第六节。这里只记要点：

- **依赖**：只依赖 Python 3.8+、ffmpeg（含 libass 滤镜）、一个中文字体。
  零第三方 Python 包，不需要 Node/数据库/Agent。
- **拷**：`run.py` `serve.py` `core/` `clients/` `tools/` `web/` `data/`
  `config.json` `.env` `打开配置后台.bat`（最小集 1.8 MB）。
- **不拷**：`runs/`（历史产物+图片缓存）、`cache/`、`__pycache__/`、
  `.env.bak*`、`*.csv.bak*`（后两者含旧密钥，别带出去）。
- **`.env` 含密钥**：自己用可以拷；给别人或上传网盘前务必清空。
- **流程**：`doctor.py` → `keycheck.py` → `--list-foods` → 小成本跑一次 →
  看一帧截图确认中文正常 → 再正式出片。
- **已实测**：把最小集拷到全新目录（无缓存），`doctor.py` 14 项全 OK，
  真实生成 4 张图并 8/8 出片成功（241s），中文渲染正常。
- **路径**：代码无硬编码绝对路径，项目放哪都能跑。唯一带绝对路径的是
  `config.json` 的 `inventory` 字段，**保持空字符串**，别改成绝对路径。

---

## 11. 文档之间的关系

| 文件 | 定位 |
|---|---|
| `README.md` | **使用者视角**：怎么装、怎么配、怎么用、怎么移植。最新、最全 |
| `HANDOFF.md` | **接手者视角**：现状、数据契约、踩过的坑、验证结论 |

若两者冲突，以 `README.md` + 代码实际行为为准。
