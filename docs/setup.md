# 环境与部署

安装、平台差异、换设备要拷什么。装不上先跑 `python3 tools/doctor.py`。

返回 [README](../README.md)

---

## 目录结构

```
meal-reel/                  ← 仓库名；本地目录可以叫任意名字（本文档示例用 daily-diet/）
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
├── .env                    ← 密钥（ 不要外传；已被 .gitignore，不随仓库分发）
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
│   ├── doctor.py           ←  环境体检（换设备先跑这个）
│   ├── fetch_ffmpeg.py     ←  装带 libass 的 ffmpeg 到 vendor/（部署第一步）
│   ├── candidates.py       ← 看菜谱池子多大、多久会重样
│   ├── keycheck.py         ← 凭据体检
│   ├── build_ingredients.py← 从《中国食物成分表》重建食材库
│   ├── import_recipes.py   ← meals.json → data/recipes.json
│   ├── make_inventory.py   ← 生成示例库存
│   └── apply_inventory.py  ← 把 *.new 覆盖到正式清单（文件被占用时用）
├── vendor/ffmpeg/          ← fetch_ffmpeg.py 下载的 ffmpeg（不进仓库）
├── assets/
│   ├── logo.svg / logo.png ← 项目图标（SVG 是源文件，改色/改形状直接改它）
│   └── demo/               ← 演示素材：demo.mp4（20 秒示例成片）+ poster.jpg
├── cache/tts/              ← 语音缓存（删了只是重新生成，不丢数据）
├── runs/                   ← 每次出片的产物
│   └── _cache/             ← 图片缓存（ 注意：图片缓存在这里，不在 cache/）
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

## 部署与上手

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

### 第 0 步：取代码

```bash
git clone https://github.com/<你的用户名>/meal-reel.git
cd meal-reel
```

后面所有命令都在这个目录里执行。本地目录名可以自己改（示例里用 `daily-diet/`），
不影响运行——项目不依赖自己的目录名。

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
你在一个叫 meal-reel（每日一菜出片工具）的项目仓库里，请完成部署。按顺序做，每一步都要用命令验证，
不要因为"命令没报错"就认为成功。

1. 先读下面的「部署与上手」和「移植到其它设备」两节，再动手。
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

## 移植到其它设备

整个工具**除 ffmpeg 和中文字体外没有外部依赖**：纯 Python 3.8+ 标准库
（`urllib` 代替 requests），不需要 `pip install` 任何**运行**依赖，也不需要 Node、
数据库或 Agent。唯一会用 pip 的场合是 `tools/fetch_ffmpeg.py`——它临时装
`imageio-ffmpeg` 只为取出其中自带的静态 ffmpeg 二进制，装完即弃，不进运行路径。

### 先跑体检

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

### 拷哪些、不拷哪些

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

### 三个平台的具体差异

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

### 头号坑：中文字体缺失是**静默**的

这条单独拎出来说，因为它的表现极其迷惑：

- ASS 字幕里写的是字体名（默认 `Microsoft YaHei`）；
- 系统里没有这个字体时，**libass 不会报错**，而是回退到 Arial：

  ```
  fontselect: (NoSuchFontXYZ, 400, 0) -> ArialMT
  ```

- 结果是**成片照常输出、退出码 0、日志没有任何异常**，只有画面里的中文变成方块。

本机实测确认了这一行为（同一帧，缺字体与不缺字体的渲染哈希不同）。
所以移植后**不要用"命令跑通了"当作成功标准**，跑 `doctor.py` 或直接看一帧截图。

### 移植后的自检清单

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

### 路径问题

代码里**没有硬编码的绝对路径**（所有路径都基于 `run.py` 所在目录推导），
所以项目放在任何位置都能跑。唯一带绝对路径的是 `config.json` 的 `inventory` 字段，
默认是空字符串（表示用 `data/01_食材清单_<user>.csv`）——**别把它改成绝对路径**，
否则换机器就失效。

两个工具脚本的**默认数据源**指向原作者的开发机（`tools/build_ingredients.py`
的 `E:/myProject/china-food-composition-data`、`tools/import_recipes.py` 的
`E:/myProject/EveryDay_Food/...`）。换设备后这两个脚本只有在你**想重建**
食材库/菜谱库时才需要，届时用 `--src` 传自己的路径即可。
日常出片完全不碰它们。

### 后台服务

```bash
python serve.py            # http://127.0.0.1:8765
```

只监听 `127.0.0.1`，不对外暴露。启动前若端口被上次残留进程占用会直接退出
（表现像"起不来"），用 `打开配置后台.bat` 或先释放端口：

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen | % { Stop-Process -Id $_.OwningProcess -Force }
```

---
