# 每日饮食 · 出片工具

从你上传的食材清单出发，自动选出当天该吃的一道菜，生成 **9:16 竖屏短视频**
（配图 + 真人配音 + 字幕）。

不需要任何 Agent，一条命令跑完。Agent 只是众多调用方之一。
也可以开一个本地后台页面调参数、点按钮出片（见第七节）。

两种选菜方式：

- **菜谱模式（默认）**：从 1093 道真实菜谱里挑一道，缺的食材用库存里的**同类平替**。
  有真实步骤、真实分量，菜是真的。
- **组合模式**：从库存的蛋白 / 蔬菜 / 主食里枚举组合，自己拼出一道菜。
  永远做得出来、价格最可控，但没有步骤。

**第一次上手看第二节「部署与上手」**：macOS / Windows / 直接交给 Agent 三种方式都在那儿。
只想快速判断环境行不行，先跑 `python tools/doctor.py`。

---

## 一、目录结构

```
daily-diet/
├── run.py                  ← 命令行入口
├── serve.py                ← 配置后台（零依赖 HTTP）
├── 打开配置后台.bat          ← 双击启动后台（自动释放端口）
├── config.json             ← 界面参数（后台可改，也可手改）
├── daily.sh                ← 单餐出片（自动选对 ffmpeg，最后打印 FINAL=路径）
├── daily3.sh               ← 早/午/晚三餐 + 拼成一条
├── daily3_autosend.sh      ← 三餐出片 → 发企业微信（带失败告警，定时任务用这个）
├── send_wecom.sh           ← 把当天三餐发到企业微信
├── send_slot.sh            ← 发单餐
├── lib/
│   ├── ffmpeg.sh           ← 定位带 libass 的 ffmpeg（所有脚本共用）
│   └── notify.sh           ← 失败告警
├── .env                    ← 密钥（★ 不要外传，见第六节）
├── .env.example            ← 密钥模板
├── data/
│   ├── 01_食材清单_U001.csv  ← 库存清单（你填这个）
│   ├── ingredients.csv     ← 食材库：103 条，营养+可食部+价格档
│   ├── price_ref.csv       ← 价格表：按本地菜场改这里
│   ├── recipe_history.json ← 出过哪些菜（保证不重样，运行时自动生成）
│   └── recipes.json        ← 菜谱库：1093 道（包裹结构，见下）
├── core/                   ← 业务逻辑，不认识任何供应商
├── clients/                ← 只有这里知道"火山"存在
├── tools/
│   ├── doctor.py           ← ★ 环境体检（换设备先跑这个）
│   ├── fetch_ffmpeg.py     ← ★ 装带 libass 的 ffmpeg 到 vendor/（部署第一步）
│   ├── candidates.py       ← 看菜谱池子多大、多久会重样
│   ├── keycheck.py         ← 凭据体检
│   ├── build_ingredients.py← 从《中国食物成分表》重建食材库
│   ├── import_recipes.py   ← meals.json → data/recipes.json
│   ├── make_inventory.py   ← 生成示例库存
│   └── apply_inventory.py  ← 把 *.new 覆盖到正式清单（文件被占用时用）
├── vendor/ffmpeg/          ← fetch_ffmpeg.py 下载的 ffmpeg（不进仓库）
├── cache/tts/              ← 语音缓存（删了只是重新生成，不丢数据）
├── runs/                   ← 每次出片的产物
│   └── _cache/             ← 图片缓存（★ 注意：图片缓存在这里，不在 cache/）
└── web/                    ← 后台前端（静态三件套）
```

> **缓存有两处**，别找错：语音在 `cache/tts/`，图片在 `runs/_cache/`。
> 两者都是内容寻址，删掉只是下次重新生成、多花钱多花时间。

### 食材库从哪来

`data/ingredients.csv` 不是手写的，是从《中国食物成分表（标准版·第6版）》
1677 条数据里筛出来的：先按「健康低热量」规则过滤（排除脱水菜、加工肉、
腌制品、纯油脂、高钠、高脂部位），剩 1042 条营养数据；再取其中商超稳定
有货、家常做法成熟的 103 条作为推荐池。

三条筛选口径值得记住，因为它们是「低热量」这个说法能成立的前提：

| 口径 | 说明 |
|---|---|
| 干湿分开量 | 脱水白菜 305kcal/100g、黄豆 390kcal/100g，但一个是一顿吃 20g 的干货。拿 200g 蔬菜的尺子量干豆必然误杀，所以干豆单列一档上限 |
| 深色蔬菜靠数据判 | 用胡萝卜素 ≥300μg/100g 判定，不是手写名单。菠菜 2920、胡萝卜 4130、大白菜 120，这个阈值能自动把深绿/橙红的分出来 |
| 畜肉取瘦部位 | 猪肉代表值是肥瘦（181kcal/11.7g 脂肪），瘦部位是 153kcal/7.8g。既然按低热量筛，就明确取瘦的 |

> 数据集**没有价格字段**。`元每公斤` 是内置估值（2026 年一二线城市商超均价），
> 目的是判断相对贵贱的量级，不是报价。按本地菜场校准就改 `data/price_ref.csv`，
> 再跑一次 `python tools/build_ingredients.py --write` 生效。

### recipes.json 是包裹结构，不是裸数组

```json
{ "source": "...", "count": 1093, "recipes": [ ... ] }
```

`core/recipes.py` 的 `load()` 已按此结构读。**不要**当裸数组 `len(d)`（会得到 3）。

---

## 二、部署与上手

三种部署方式任选一种，最终状态完全一样：`doctor.py` 全绿 + `.env` 填好密钥 + 库存清单就位。

| 方式 | 适合 | 耗时 |
|---|---|---|
| A. 交给 Agent | 不想敲命令，或想让它顺手排错 | 3–5 分钟 |
| B. macOS 手动 | 本机部署，定时任务 | 5 分钟 |
| C. Windows 手动 | 本机部署，定时任务 | 5 分钟 |

部署只做三件事，没有别的：

1. **Python 3.8+**（只用标准库，不需要 `pip install` 任何运行依赖）
2. **带 libass 的 ffmpeg** ← 唯一的坑，见下
3. **中文字体名写进 `.env`**

### 那个唯一的坑：ffmpeg 的 libass 是编译期选项

有些包管理器装出来的 ffmpeg 被裁剪过，**没有 `ass` 滤镜**。缺它的时候字幕
**不会报错**：成片照常输出、退出码 0，只是画面里一个字都没有。所以不能拿
"命令跑通了"当成功标准，必须显式检查：

```bash
ffmpeg -hide_banner -filters | grep -w ass
```

有输出（形如 ` T.. ass  V->V  Render ASS subtitles`）就对了；没有就执行：

```bash
python3 tools/fetch_ffmpeg.py     # 下载带 libass 的静态构建到 vendor/ffmpeg/
```

它按「`--src` 指定 → 已有本地构建 → pip 装 imageio-ffmpeg 取其静态构建」的顺序找一个
真带 ass 的二进制放进 `vendor/ffmpeg/`，装完自己会验证一次。项目里所有脚本
（`daily.sh` / `daily3.sh` / `send_*.sh`）都通过 `lib/ffmpeg.sh` 按
`vendor/ffmpeg` → `~/.workbuddy/binaries/ffmpeg-libass` → 系统 PATH 的顺序找 ffmpeg，
所以装好就不用再管 PATH。

### 方式 A：交给 Agent 部署（最快）

把下面这段整块贴给任意能执行命令的 Agent（WorkBuddy / Claude Code / Cursor / Codex 都行），
它会自己读文档、装依赖、排错：

```text
你在一个「每日饮食出片」项目仓库里，请完成部署。按顺序做，每一步都要用命令验证，
不要因为"命令没报错"就认为成功。

1. 先读 README.md 的第二节（部署）和第六节（移植坑），再动手。
2. 跑 python3 tools/doctor.py，把它报的每个 [FAIL] 逐项修掉；[WARN] 记录下来最后一起报。
3. 如果 doctor 说 ffmpeg 缺 ass 滤镜：先跑 python3 tools/fetch_ffmpeg.py；
   若它仍拿不到可用二进制，就从 imageio-ffmpeg 的 wheel 里取出静态构建放到 vendor/ffmpeg/，
   然后重跑 doctor 确认 libass 那一项变成 [ OK ]。不要跳过这一项——
   没有 ass 滤镜时字幕会静默消失，退出码依然是 0。
4. 把 .env.example 复制成 .env，然后停下，等我填 ARK_API_KEY / VOLC_SPEECH_APP_ID /
   VOLC_SPEECH_ACCESS_TOKEN。绝对不要自己编造或猜测密钥。
5. 我填好后，跑 python3 tools/keycheck.py，确认凭据是真的能调通（不是格式对）。
6. 跑 ./daily.sh --slot dinner 出一条片子，然后抽一帧确认中文字幕不是方块：
   ffmpeg -ss 6 -i runs/<id>/final.mp4 -frames:v 1 -update 1 /tmp/chk.jpg，
   并把这一帧的内容描述给我（有没有方块/乱码）。
7. 最后汇报：doctor 结论、成片绝对路径、这一条片子生成了几张图、花了多少钱。

硬性要求：任何一步失败就停下来说明卡在哪、根因是什么，不要静默跳过，
不要编造成片路径或"部署成功"的结论。
```

> Windows 上没有 bash：把上面第 6 步的 `./daily.sh --slot dinner` 换成
> `python run.py --user U001 --slot dinner`，其余步骤完全一样。

这段之所以这么写，是因为这个项目出过两类"看起来成功"的故障：字幕静默丢失、
密钥格式对但调用失败。所以每步都要求**用命令验证**而不是看退出码。

### 方式 B：macOS 部署

```bash
# 1. 依赖
brew install ffmpeg
ffmpeg -hide_banner -filters | grep -w ass      # 没输出 → 见上面「唯一的坑」

# 2. 中文字体：macOS 自带 PingFang，但必须显式指定，否则静默回退成方块
cp .env.example .env
echo 'DIET_SUB_FONT=PingFang SC' >> .env

# 3. 体检
python3 tools/doctor.py                          # 全 [ OK ] 才算完
```

### 方式 C：Windows 部署

```powershell
# 1. 依赖（三选一）
choco install ffmpeg
# scoop install ffmpeg
# 或从 https://www.gyan.dev/ffmpeg/builds/ 下 static 包，把 bin 目录加进 PATH

ffmpeg -hide_banner -filters | Select-String " ass "   # 没输出 → 见上面「唯一的坑」

# 2. 配置（Windows 自带微软雅黑，不用设字体）
copy .env.example .env
notepad .env

# 3. 体检
python tools\doctor.py
```

Windows 上还有一个便利入口：双击项目根目录的 `打开配置后台.bat`，它会先释放 8765 端口
再启动配置页面（不管端口是否被上次残留进程占着）。

### 部署完成之后：填密钥 → 上传库存 → 出片

### 第 1 步：填密钥并验凭据（只做一次）

把 `.env.example` 复制成 `.env`，填两处：

```ini
# 方舟：图像 + 文案
ARK_API_KEY=                    # 控制台复制的完整 key，它本身带 ark- 前缀，不要再加

# 豆包语音：配音（两种体系任选其一，A 优先）
VOLC_SPEECH_APP_ID=             # 语音技术 → 应用管理
VOLC_SPEECH_ACCESS_TOKEN=       # 同一页面
# VOLC_SPEECH_API_KEY=          # 或者用新版控制台的 UUID Key
```

填完再体检一次，或单独验凭据：

```bash
python tools/keycheck.py        # 看到 ✅ 可用 就对了
```

> **这一步能省掉 90% 的排查时间。** 密钥区分「格式对」和「能用」两件事，
> `doctor.py` 只查有无与格式，`keycheck.py` 才是真正发起调用验证。

### 第 2 步：上传数据

把食材填进 `data/01_食材清单_U001.csv`（Excel 直接编辑另存为 CSV 也行）。

**填之前先看一眼可用清单**：

```bash
python run.py --list-foods
```

会列出 103 种食材、它们的价格档、每 100g 的热量与蛋白、以及「1 颗 ≈ 多少克」。
常见俗称已内置（鸡胸、西蓝花、土豆、西红柿、空心菜、包菜、三文鱼、里脊…），
写俗称也能识别。写错了会明确告诉你哪一行、为什么。

想从零来一份示例库存（30 条，覆盖四类，能撑一周）：

```bash
python tools/make_inventory.py --date 2026-10-30
```

### 第 3 步：出片

```bash
python run.py --user U001 --slot dinner
```

约 3–4 分钟（4 张图串行生成，实测 212s），产物在 `runs/2026-10-30_dinner/`：

| 文件 | 内容 |
|---|---|
| `final.mp4` | 成片，1080×1920 / 30fps |
| `narration.wav` | 独立音轨 |
| `subtitle.srt` | 外挂字幕（平台上传用） |
| `subtitle.ass` | 烧进画面的字幕（已合成，一般不用管） |
| `旁白稿.md` | 每句文案 + 营养溯源，审校用 |
| `cover.jpg` | 封面 |
| `img/` | 4 张分镜原图（原料/过程/成品/特写） |
| `manifest.json` | 本次运行的完整记录（用了什么、花了多少、有没有降级） |

---

## 三、配置项

分两处：

| 文件 | 放什么 | 谁能改 |
|---|---|---|
| `.env` | **密钥** + 少量覆盖项 | 只有你和 `run.py` 读；后台**不写**它 |
| `config.json` | **界面参数**（预算、餐次、音色、字幕…） | 后台页面随便改 |

这样分开是因为：`.env` 里混着密钥，一旦前端能写它就有读走或写坏的风险。

### 3.1 `.env`（密钥与覆盖项）

| 项 | 说明 |
|---|---|
| `ARK_API_KEY` | 方舟 key，管**图像 + 文案**。`ark-` 前缀本身就是 key 的一部分，粘贴时别再手打一遍（重复前缀会报 `401 API key format is incorrect`） |
| `VOLC_SPEECH_APP_ID`<br>`VOLC_SPEECH_ACCESS_TOKEN` | 语音**旧版**体系，成套出现。语音技术 → 应用管理 |
| `VOLC_SPEECH_API_KEY` | 语音**新版**体系，UUID 格式。与上面二选一，同时存在时旧版优先 |
| `DIET_TTS_SPEAKER` | 音色，覆盖 `config.json` 的 `tts.speaker` |
| `DIET_TTS_RESOURCE_ID` | 语音资源 ID，默认 `seed-tts-2.0` |
| `DIET_BUDGET_CNY` | 单餐预算，覆盖 `config.json` 的 `budget` |
| `DIET_SUB_FONT` | ★ 字幕字体名，默认 `Microsoft YaHei`。**移植必看第六节** |
| `DIET_SUB_SIZE`<br>`DIET_SUB_TITLE_SIZE`<br>`DIET_SUB_STAT_SIZE`<br>`DIET_SUB_MARGIN_V`<br>`DIET_SUB_ALPHA` | 字幕字号/边距/底框透明度（后台也能改，`config.json` 优先） |

> **注意区分**：`DIET_IMAGE_MODEL`、`DIET_IMAGE_MODEL_FALLBACK`、`DIET_CHAT_MODEL`、
> `DIET_MAX_COST_CNY` 这几个只写在 `.env.example` 里供参考，
> **当前只有一个工具脚本读 `DIET_IMAGE_MODEL`，出片主链路并不读它们**。
> 模型名要改，目前得改 `clients/ark_image.py` 的 `DEFAULT_MODEL` / `FALLBACK_MODEL`。

### 3.2 `config.json`（界面参数）

后台页面能改的全部项，也可以直接编辑这个文件。数值都会被夹在合理区间内
（比如预算 3–500），填错不会让出片炸掉。

| 键 | 默认 | 说明 |
|---|---|---|
| `mode` | `recipe` | `recipe`=菜谱+平替 / `combo`=库存组合 |
| `categories` | `["home"]` | 菜谱类型：`fat-loss`/`muscle-gain`/`maintain`/`nutrition`/`home`/`therapy`/`bakery`/`snack` |
| `meal_types` | `["dinner"]` | 菜谱餐次筛选（可多选） |
| `user` / `slot` | `U001` / `dinner` | 出片用户与餐次 |
| `budget` | `25` | 单人单餐食材预算（元） |
| `max_minutes` | `40` | 最长用时，超过直接筛掉 |
| `cuisines` / `methods` / `flavors` | `[]` | 空 = 不限 |
| `exclude` | `[]` | 忌口/过敏原，命中即淘汰 |
| `tts.engine` | `auto` | `auto`/`doubao`/`mock` |
| `tts.speaker` | `zh_female_xiaohe_uranus_bigtts` | 音色 |
| `tts.speed` | `1.0` | 语速倍率（0.5–2.0） |
| `video.transition` | `0.3` | 转场秒数（0–1.0）。**超过 0.4 两张不同景别会叠影** |
| `video.reuse_images` | `false` | 只用缓存图 |
| `subs.main_size` / `title_size` / `stat_size` | `70` / `46` / `54` | 字幕/顶栏/营养卡字号 |
| `subs.margin_v` | `300` | 字幕距底部像素 |
| `subs.box_alpha` | `"66"` | 底框透明度，`00` 全黑 → `FF` 全透 |
| `subs.wrap` | `13` | 每行最大视觉宽度（汉字=1，半角=0.5） |
| `chat` | `false` | 用模型改写文案（需已开通文案模型） |

### 3.3 字幕字号（面向老年观众）

字幕和营养卡用的是**半透明黑底 + 白字加粗**，不是单纯描边——亮背景（白瓷盘）上
这样对比度才锁得住。想更清晰就把 `subs.box_alpha` 调小（如 `55`）。

调大字号会让每行字数变少。当前逻辑会**自动按标点断行**，每行上限 13 字（约 910px），
超过就换行。所以文案句长最好控制在 26 字内，否则会占三行、压到菜上。

> `DIET_SUB_TITLE_SIZE` 的默认值在 `.env.example` 里写的是 `40`，
> 但 `config.json` 里是 `46`，而 `config.json` 优先。以 `46` 为准。

### 3.4 价格与预算（决定"会不会总推贵的"）

只看营养的推荐器会稳定推出三文鱼、牛腱、对虾——营养上确实最优，但一周吃三次
不现实。所以价格是独立维度，分两层生效：

| 层 | 机制 | 效果 |
|---|---|---|
| 硬约束 | 单餐成本 > 预算的组合**不参与排序** | 贵的直接淘汰，不是扣点分了事 |
| 软扣分 | 预算内按剩余空间给 `cost` 分 | 同样营养下优先省 |
| 配额 | 「昂贵」最多 1 个/餐，「偏高及以上」合计最多 2 个 | 防止一道菜堆三个贵价食材 |

实测预算是真的在起作用（同一份库存，只改预算）：

```
预算 ¥8    候选 2644 组 → ¥5.4  香煎鸡胸配清炒洋葱＋胡萝卜
预算 ¥12   候选 2730 组 → ¥5.4  香煎鸡胸配清炒洋葱＋小白菜
预算 ¥25   候选 3076 组 → ¥7.0  香煎鸡胸配蒜蓉菠菜＋木耳
预算 ¥60   候选 3120 组 → ¥7.0  香煎鸡胸配蒜蓉菠菜＋木耳   ← 不会因为钱多就乱推贵的
```

价格档（写进清单「备注」列，方便你在 Excel 里一眼看出）：

| 符号 | 档位 | 元/公斤 | 典型 |
|---|---|---|---|
| `¥` | 经济 | ≤10 | 白菜 4、萝卜 4、土豆 6、鸡蛋 11、豆腐 8 |
| `¥¥` | 常规 | 10–25 | 菠菜 12、西兰花 16、鸡胸 22、草鱼 16、猪肝 20 |
| `¥¥¥` | 偏高 | 25–60 | 瘦猪肉 32、鲈鱼 45、带鱼 40、芦笋 30、藜麦 40 |
| `¥¥¥¥` | 昂贵 | >60 | 三文鱼 180、牛腱 90、对虾 85、羊后腿 80、核桃 60 |

---

## 四、数据上传模板

文件：`data/01_食材清单_<用户>.csv`

`<用户>` 与 `--user` 对应。默认 `U001`，换成 `U002` 就是另一份清单。

### 字段

| 列名 | 必填 | 填法 | 说明 |
|---|---|---|---|
| `食材名称` | ✅ | 鸡胸 / 豆腐 / 包菜 … | 先用 `--list-foods` 查。103 种食材 + 约 90 条俗称已内置 |
| `数量` | ✅ | `300` 或 `300g` 或 `1` | 写成 `300g` 也能识别，会自动拆出单位 |
| `单位` | | `g` `kg` `斤` `ml` `L`，或件数：`颗` `根` `个` `块` `条` `头` `瓶` | 留空按 g。数量列已带单位时这列可空 |
| `类别` | | 蛋白 / 蔬菜 / 主食 / 调味 | 不影响推荐（按食材本身判定），只作备注。留空也行 |
| `购买日期` | | `2026-10-29` | 必须是这个格式 |
| `保质期至` | ✅ | `2026-11-02` | **必填**，推荐排序会用它算临期 |
| `存放位置` | | 冷藏 / 冷冻 / 常温 | 写"冷冻"的食材**过期也不淘汰** |
| `是否深色蔬菜` | | 是 / 否 | 仅备注。实际判定用胡萝卜素 ≥300μg/100g，比手填准 |
| `备注` | | 任意文字 | 会显示在推荐理由里，如"今天到期"。`make_inventory.py` 也会把价格档写在这里 |

### 三条规则

1. **按表头名读取，不看列顺序**——你调整列的先后顺序不影响。
2. **单行出错不阻断整批**——坏行会被跳过并告诉你行号和原因，好行照常入库。
3. **表头不能改名**——`食材名称` 不能写成 `名称`，否则整列读不到。

### 一个填好的例子

```csv
食材名称,数量,单位,类别,购买日期,保质期至,存放位置,是否深色蔬菜,备注
鸡胸肉,300,g,蛋白,2026-10-29,2026-11-02,冷藏,否,
西兰花,1,颗,蔬菜,2026-10-29,2026-11-01,冷藏,是,
小番茄,250,g,蔬菜,2026-10-28,2026-11-01,冷藏,是,最近有点软，优先用
北豆腐,1,块,蛋白,2026-10-29,2026-10-31,冷藏,否,今天到期
大米,1,kg,主食,2026-10-20,2027-04-20,常温,否,
```

> 用 Excel 保存时选 **CSV UTF-8**。存成 GBK 中文会乱码（读取时能容错 BOM，
> 但容不了编码本身不对）。

---

## 五、常用命令

```bash
# ★ 换设备/改环境后第一件事
python tools/doctor.py

# 看能填哪些食材
python run.py --list-foods

# 正常出片（4 张图约 3-4 分钟）
python run.py --user U001 --slot dinner

# 图片全走缓存，只重做字幕/配音/剪辑（约 10-12 秒）
python run.py --user U001 --slot dinner --reuse-images

# 指定日期（影响"还剩几天过期"的计算）
python run.py --user U001 --date 2026-11-01 --slot lunch

# 换一份清单
python run.py --user U002

# 换预算（比如今天想吃好点）
python run.py --user U001 --budget 45

# 用自己路径的 CSV
python run.py --inventory D:\我的清单.csv

# 强制占位音轨（不调语音，用来快速验证其他环节）
python run.py --user U001 --tts mock

# 强制真语音，失败就报错而不是降级
python run.py --user U001 --tts doubao

# 凭据体检（真的发起调用）
python tools/keycheck.py

# 启动配置后台
python serve.py                 # http://127.0.0.1:8765
python serve.py --port 9000 --open

# 重建食材库（改了 price_ref.csv 或筛选规则后）
python tools/build_ingredients.py --write --report

# 生成示例库存
python tools/make_inventory.py --date 2026-10-30

# 把 *.new 覆盖到正式清单（清单被网盘/同步客户端占用时用）
python tools/apply_inventory.py
```

### 参数说明

| 参数 | 作用 |
|---|---|
| `--user` | 用哪份清单（对应 `data/01_食材清单_<用户>.csv`） |
| `--date` | 计算"还剩几天过期"的基准日，默认今天 |
| `--slot` | 出哪一餐（`breakfast`/`lunch`/`dinner`）。**它同时决定菜谱筛选的餐次**，不是只改个标题 |
| `--meal-types` | 菜谱餐次多选，逗号分隔。一般不用手动给：后台的餐次是多选，会用它把集合传下来 |
| `--mode` | `recipe`=真实菜谱 + 平替（默认）/ `combo`=从库存组合 |
| `--recipe-cats` | 菜谱类型，逗号分隔：`fat-loss,muscle-gain,maintain,nutrition,home,therapy,bakery,snack` |
| `--budget` | 临时覆盖单餐预算 |
| `--inventory` | 指定清单 CSV 路径 |
| `--reuse-images` | 只用缓存图片，不发起新的生图调用。改文案、调字幕、调运镜时用它，省时省钱 |
| `--tts` | `auto`（默认，失败降级）/ `doubao`（失败报错）/ `mock`（占位音轨） |
| `--chat` | 用方舟模型改写文案（需已开通文案模型）。不加则用确定性模板 |
| `--run-id` | 指定产物目录名，默认 `<日期>_<餐次>` |
| `--list-foods` | 列出可上传的食材名、单位与别名 |

---

## 六、移植到其它设备 ★

整个工具**除 ffmpeg 和中文字体外没有外部依赖**：纯 Python 3.8+ 标准库
（`urllib` 代替 requests），不需要 `pip install` 任何**运行**依赖，也不需要 Node、
数据库或 Agent。唯一会用 pip 的场合是 `tools/fetch_ffmpeg.py`——它临时装
`imageio-ffmpeg` 只为取出其中自带的静态 ffmpeg 二进制，装完即弃，不进运行路径。

### 6.1 先跑体检

```bash
python tools/doctor.py
```

它逐项检查「换一台机器最容易坏、坏了又不报错」的地方，并给出修法：

```
[ OK ] Python 版本        3.11.9
[ OK ] ffmpeg / ffprobe   C:\ProgramData\chocolatey\bin\ffmpeg.EXE
[ OK ] libass（烧字幕）    ffmpeg 已编译 ass 滤镜
[ OK ] 中文字体            请求 Microsoft YaHei → 实际 MicrosoftYaHei-Bold
[ OK ] 目录可写            runs/  cache/
[ OK ] .env 存在           已配置 ARK_API_KEY / 语音体系 A
[ OK ] 食材库/菜谱库/价格表/库存清单
```

有 `[FAIL]` 就按行尾给的 `→` 修，修完再出片。

### 6.2 拷哪些、不拷哪些

| 目录/文件 | 要不要拷 | 原因 |
|---|---|---|
| `run.py` `serve.py` `core/` `clients/` `tools/` `web/` | ✅ 全拷 | 代码 |
| `data/`（`ingredients.csv` `recipes.json` `price_ref.csv` 清单 CSV） | ✅ 全拷 | 数据，缺一不可 |
| `config.json` `.env.example` `打开配置后台.bat` | ✅ 拷 | 配置模板 |
| **`.env`** | ⚠️ **看你** | **里面有你的 API Key 和语音 Token**。自己用可以拷；给别人/传到网盘前务必先清空 |
| `runs/` | ❌ 不拷 | 历史产物 + 图片缓存，体积大（上百 MB），新设备没有也能跑 |
| `cache/` | ❌ 不拷 | 语音缓存，删了只是重新生成 |
| `__pycache__/` | ❌ 不拷 | Python 字节码，会自动重建 |
| `.env.bak*` `*.csv.bak*` | ❌ 不拷 | 历史废备份，含旧密钥，**别带出去** |

**最小可运行集**：`run.py` `serve.py` `core/` `clients/` `tools/` `web/`
`data/` `config.json` `.env`（填好密钥）+ `打开配置后台.bat`。

### 6.3 三个平台的具体差异

**Windows**：装 [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) 或
`choco install ffmpeg`，确保 `ffmpeg.exe` 在 PATH。中文字体系统自带（微软雅黑），
开箱即用。这就是当前开发环境。

**macOS**：

```bash
brew install ffmpeg
ffmpeg -hide_banner -filters | grep -w ass    # 没输出 = 这个构建被裁剪过
python3 tools/fetch_ffmpeg.py                 # 缺 ass 时用它装带 libass 的静态构建
python3 tools/doctor.py
```

> 不要假设 Homebrew 的 ffmpeg 一定带 libass。本项目的开发机就踩过：`brew` 装出来的
> 构建没有 `--enable-libass/--enable-libfreetype`，字幕静默烧不进去且退出码为 0，
> `brew reinstall` 也没用（公式本身就缺这些依赖）。所以一律先 `grep -w ass` 确认。

若体检报字体被回退，在 `.env` 加一行：

```ini
DIET_SUB_FONT=PingFang SC
```

**Linux**（要装中文字体，否则字幕是方块）：

```bash
sudo apt install ffmpeg fonts-noto-cjk      # Debian/Ubuntu
# 或 sudo dnf install ffmpeg google-noto-sans-cjk-fonts
python3 tools/doctor.py
```

必要时 `.env` 里设 `DIET_SUB_FONT=Noto Sans CJK SC`（字体名要跟
`fc-list :lang=zh | head` 显示的一致）。

### 6.4 头号坑：中文字体缺失是**静默**的

这条单独拎出来说，因为它的表现极其迷惑：

- ASS 字幕里写的是字体名（默认 `Microsoft YaHei`）；
- 系统里没有这个字体时，**libass 不会报错**，而是回退到 Arial：

  ```
  fontselect: (NoSuchFontXYZ, 400, 0) -> ArialMT
  ```

- 结果是**成片照常输出、退出码 0、日志没有任何异常**，只有画面里的中文变成方块。

本机实测确认了这一行为（同一帧，缺字体与不缺字体的渲染哈希不同）。
所以移植后**不要用"命令跑通了"当作成功标准**，跑 `doctor.py` 或直接看一帧截图。

### 6.5 移植后的自检清单

1. `python tools/doctor.py` → 全 `[ OK ]`（`[WARN]` 不影响出片但影响效果）
2. `python tools/keycheck.py` → 凭据真的能调通
3. `python run.py --list-foods` → 能列出 103 种食材
4. `python run.py --user U001 --slot dinner --tts mock` → **先不花钱**跑通一次，
   确认 8 步全过（此时图仍会真调，想零成本见下条）
5. 看一帧截图确认中文正常：`ffmpeg -ss 2 -i runs/<id>/final.mp4 -frames:v 1 -update 1 chk.jpg`
6. 最后再跑一次不带 `--tts mock` 的完整出片

> **零成本验证流水线**：去掉 `.env` 里的 `ARK_API_KEY` 会让生图失败退出，
> 不能用于验证。想完全不花钱地验证「除了生图之外」的所有环节，
> 用 `--reuse-images`（前提是新设备上带着 `runs/_cache/`），
> 否则只能接受至少生成 1 张图的费用。

### 6.6 路径问题

代码里**没有硬编码的绝对路径**（所有路径都基于 `run.py` 所在目录推导），
所以项目放在任何位置都能跑。唯一带绝对路径的是 `config.json` 的 `inventory` 字段，
默认是空字符串（表示用 `data/01_食材清单_<user>.csv`）——**别把它改成绝对路径**，
否则换机器就失效。

两个工具脚本的**默认数据源**指向原作者的开发机（`tools/build_ingredients.py`
的 `E:/myProject/china-food-composition-data`、`tools/import_recipes.py` 的
`E:/myProject/EveryDay_Food/...`）。换设备后这两个脚本只有在你**想重建**
食材库/菜谱库时才需要，届时用 `--src` 传自己的路径即可。
日常出片完全不碰它们。

### 6.7 后台服务

```bash
python serve.py            # http://127.0.0.1:8765
```

只监听 `127.0.0.1`，不对外暴露。启动前若端口被上次残留进程占用会直接退出
（表现像"起不来"），用 `打开配置后台.bat` 或先释放端口：

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen | % { Stop-Process -Id $_.OwningProcess -Force }
```

---

## 七、配置后台能看到什么

双击 `打开配置后台.bat`，或 `python serve.py`，浏览器开 http://127.0.0.1:8765

| 面板 | 内容 |
|---|---|
| 参数区 | 推荐方式、菜谱类型、餐次、预算、最长用时、忌口、菜系/做法/口味、配音、画面字幕 |
| 密钥与凭据 | 每个环境变量是否已配 + **脱敏预览**（`ark-****4548（46 位）`）、当前语音鉴权体系、`.env` 路径 |
| 当前全部参数 | `config.json` 的完整扁平清单：中文名 + 键名 + 当前值 |
| 库存 | 每条食材的角色/可食部/保质期/价位 |
| 预览 | 点「跑一次」看 Top3 与筛选漏斗，**不出图不花钱** |
| 出片 | 实时日志、进度条、停止按钮 |
| 历史 | 历次成片，可播放/下载/看 manifest |

**为什么密钥只给脱敏预览**：本服务只监听 127.0.0.1，但**没有 CSRF 防护**，
本机任意网页都能向它发 POST。一旦提供「返回完整密钥」的接口，等于给每个打开的
网页开了一条读走 API Key 的路。脱敏预览能回答"配的是哪把钥匙、有没有粘错前缀"，
够用了；要看完整值直接打开 `.env`。

---

## 八、怎么判断这次跑得对不对

跑完会打印 8 个阶段。三个关键点：

```
[3/8] 配音：doubao 真人声 4 句，语音总长 17.0s …（鉴权：APP ID + Token）
[4/8] 分镜 4 个，总时长 19.54s …（时间轴依据：真实音频）
[5/8] 配图 … [cache]     ← 出现 [cache] 说明没花钱
```

- 第 3 行写 **doubao** 才是真人声；写 **mock** 说明降级了，原因会在下面一行给出。
- 第 4 行写 **真实音频** 才是按配音排的时间轴；写"字数估算"说明没用上语音。
- 第 5 行每张图后面的耗时，`[cache]` 是复用，数字是实际生成耗时（30–90 秒/张）。

**`[cache]` 不等于"图是对的"**——它只说明命中了缓存。要确认图是这批菜真实生成的，
看 `manifest.json`：

```bash
python -c "import json;m=json.load(open('runs/<id>/manifest.json',encoding='utf-8'));print({k:v.get('model') for k,v in m['images'].items()})"
```

正常应是 `doubao-seedream-5-0-pro-260628`；若出现别的值或耗时 `0`，说明命中的是
非本批次的缓存。

`manifest.json` 里其余关键字段：`narration.source`（`template` 还是 `ark_chat`）、
`storyboard.transition`、`audio.audio_sec`（每句真实时长）、`cost.total`。

---

## 九、常见问题

**改了文案，为什么图片没变？**
图片按「提示词 + 尺寸 + 参考图」做内容寻址缓存。文案变了但提示词没变，图就复用。
想强制重生成，删掉 `runs/_cache/` 下对应文件，或不加 `--reuse-images` 且改提示词。

**换了菜谱，为什么画面还是上一道菜？**
2026-10-30 修掉的缺陷：提示词里原来不含菜名，同类食材的菜共用一张图，
换菜谱会直接命中上一道的缓存。现在 `process/final/hero` 三张都带「这道菜是「菜名」」，
换菜必然换提示词、换缓存键。若你在看修复**之前**生成的 `runs/` 旧成片，图仍是旧的，
去掉 `--reuse-images` 重跑一次即可。

**后台把转场/语速改了，成片没变化？**
同属已修缺陷（2026-10-30）：这两个参数曾经只存在于配置里，从未传给真正使用它们的
时间轴和 TTS。现在生效，可这样核对：`video.transition` 看 `manifest.json` 的
`storyboard.transition`；`tts.speed` 看 `audio.audio_sec` 是否变短。

**字幕变成方块 / 部分汉字不显示？**
字体缺失，且**不会报错**。跑 `python tools/doctor.py`，或在 `.env` 里
`DIET_SUB_FONT=<本机已有的中文字体名>`。详见第六节 6.4。

**配音是占位音轨怎么办？**
跑 `python tools/keycheck.py` 看凭据是否可用。占位音轨是"静音 + 每句开头一声叮"，
叮声就是该开口的位置，可以用来验证声画是否对齐。

**字幕超出画面被裁掉了？**
说明有一句太长、断行后仍超宽。当前断行上限是 13 字（70px 字号）。
两个办法：把 `subs.main_size` 调到 `64`，或把那句话改短。

**某一行食材被跳过了？**
运行时会打印行号和原因。最常见是名称不在词表里（用 `--list-foods` 查）或
`保质期至` 日期格式不对。

**为什么总不推荐三文鱼、牛腱？**
因为它们 ¥¥¥¥ 档，一份就 ¥37 左右，超出单餐预算。这是刻意的：只看营养会
稳定推它们，但一周吃三次不现实。想吃就把 `--budget` 调高，或看
`premium_alternatives()` 给出的"多花多少钱能升级到哪一样"。

**价格不准怎么办？**
`data/price_ref.csv` 两列：`食材名称` 和 `元每公斤`。改成你家附近的价格，
再跑 `python tools/build_ingredients.py --write`。脚本优先读这个文件，
内置估值只作兜底。

**想加新的食材？**
在 `tools/build_ingredients.py` 的 `COMMON` 里加一行 `(正名, 元每公斤)`，
跑 `--write` 重建。正名要跟成分表对得上（`--report` 会列出对不上的名字）。
如果只是想在清单里换个说法，改 `core/foods.py` 的 `COLLOQUIAL`。

**清单文件写不进去，报 Permission denied？**
`data/01_食材清单_U001.csv` 被别的程序独占打开了，最常见是百度网盘 /
OneDrive 的同步客户端在盯着这个目录。退掉同步客户端后跑：

```bash
python tools/apply_inventory.py
```

它会把 `01_食材清单_U001.csv.new` 覆盖到正式文件，覆盖前先备份成 `.bak.<时间戳>`，
不会动你原来那份 `.bak`。想直接换目标就加 `--src` / `--dst`。

**后台点了出片没反应 / 启动就退出？**
端口 8765 被上次残留进程占着。见第六节 6.7 的释放端口命令，或直接双击
`打开配置后台.bat`（它会自己处理）。

**`--chat` 报 404？**
文案模型没在当前方舟账号下开通。程序会自动回落模板文案（日志会写
`[2/8] 文案：回落到 template`），不影响出片。要用模型改写，去方舟控制台开通任一
chat 模型。
