<p align="center">
  <img src=".github/images/icon.svg" alt="Minecraft UniBot" width="120" height="120">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Fork%20of-MineJPGcraft%2FUniBot-blue" alt="Fork">
  <img src="https://img.shields.io/github/v/release/MineJPGcraft/UniBot?label=upstream" alt="上游版本">
  <img src="https://img.shields.io/github/license/MineJPGcraft/UniBot" alt="许可证">
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/NoneBot2-2.2%2B-purple" alt="NoneBot2">
</p>

<h1 align="center">Minecraft UniBot · 个人增强版</h1>

<p align="center">
  <b>跨平台 · 多服互联 · 即插即用 — 让 Minecraft 与你的聊天世界无缝相连</b>
</p>

<p align="center">
  <a href="https://bot.mcjpg.dev/">📚 上游项目文档</a>
  ·
  <a href="https://github.com/MineJPGcraft/UniBot">⬆️ 上游仓库</a>
  ·
  <a href="https://github.com/MineJPGcraft/UniBot/issues">🐛 反馈问题</a>
</p>

<p align="center">
  <b><i>Cross-platform · Multi-server interconnection · Plug-and-play — Seamlessly bridging Minecraft with your chat world.</i></b>
</p>

---

> [!NOTE]
> **关于本仓库**
>
> 本仓库是 [MineJPGcraft/UniBot](https://github.com/MineJPGcraft/UniBot)（作者 **LonelySail**）的**个人增强 Fork**（维护者 **DICEALPHAHU**），基于上游 **v1.0.6** 二次开发。
> 核心框架、WebUI、扩展系统等均来自上游；本仓库的主要改动是新增了一组**群聊娱乐插件**与**B站 UP 动态监控**，并在 `pyproject.toml` 中注册了这些插件与所需依赖。
> 上游的 `Docs/` 文档站与 `tests/` 测试未包含在本仓库中，需要查阅请前往[上游仓库](https://github.com/MineJPGcraft/UniBot)。
>
> 所有 Token / 服务器地址 / 群号均已占位符化：请复制 `.env.example` 为 `.env` 后填写自己的配置，**不要提交填好真实值的 `.env`**。

## 简介

**Minecraft UniBot** 是一套基于 [NoneBot2](https://nonebot.dev/) 构建的跨平台 Minecraft 机器人，通过 [鹊桥](https://github.com/17TheWord/QueQiao) 协议与服务器互通，让 Minecraft 与你的聊天世界无缝相连。

- **真正的跨平台**：不止 QQ，还支持 Telegram、Discord、Kook、QQ 频道等，一套指令全平台通用
- **多服互联**：同时连接多台 Minecraft 服务器，消息互通，跨服聊天零延迟
- **全服务端兼容**：支持 Fabric、Forge、Spigot、Paper 等主流服务端，甚至是基岩版服务器，即插即用
- **扩展系统**：指令、服务、渲染引擎、模板与资源五类扩展，即插即用，失败自动隔离
- **WebUI 管理面板**：现代化管理界面，可视化配置、实时监控、日志查看，开箱即用
- **图片渲染模式**：将指令输出渲染为精美图片，支持自定义背景
- 🆕 **群聊娱乐插件集**：抽老婆 / 强娶、戳一戳冷知识、天气、今天吃什么、猜数字、抽签、摸摸表情包 —— 见[下节](#-本-fork-新增功能)
- 🆕 **B站 UP 动态监控与视频推送**：自动轮询 UP 主新投稿，生成快照 / 转发视频到群 —— 见[下节](#-本-fork-新增功能)

### *Introduction*

<i>

**Minecraft UniBot** is a cross-platform chat bot built on [NoneBot2](https://nonebot.dev/). It connects to Minecraft servers via the [QueQiao](https://github.com/17TheWord/QueQiao) protocol, seamlessly bridging Minecraft with your chat world.

- **Truly cross‑platform** – Not just QQ, but also Telegram, Discord, Kook, QQ Guilds, and more – a single set of commands works everywhere.
- **Multi‑server interconnection** – Connect to multiple Minecraft servers at the same time, with zero‑latency messaging between them.
- **Full server compatibility** – Supports all major server types, including Fabric, Forge, Spigot, Paper, and even Bedrock editions – plug and play.
- **Extension system** – Five kinds of extensions: commands, services, rendering engines, templates, and resources. All are plug‑and‑play, with automatic isolation if any fails.
- **WebUI management panel** – A modern admin interface featuring visual configuration, real‑time monitoring, and log viewing – ready out of the box.
- **Image rendering mode** – Renders command outputs as beautifully styled images, with full support for custom backgrounds.
- 🆕 **Group-chat mini-games & utilities** – "draw a wife", poke trivia, weather, what-to-eat, guess-the-number, fortune sticks, and a head-pat meme – see [below](#new-in-this-fork).
- 🆕 **Bilibili uploader watcher** – Automatically polls a Bilibili uploader's new videos and pushes snapshots / videos to your groups – see [below](#new-in-this-fork).

</i>

## 🆕 本 Fork 新增功能

所有新增内容都放在 `Plugins/` 目录，并在 `pyproject.toml` 的 `[tool.nonebot] plugins` 中注册，可按需在 `pyproject.toml` 里把 `enabled` 改成 `false` 单独关闭。

| 插件 | 文件 | 触发方式 | 依赖 |
|---|---|---|---|
| [B站 UP 动态监控](#1-b站-up-动态监控与视频推送) | `Plugins/Bili.py` | `.bv` `.biliwatch` `.bilistatus` `.biliclear` `.bilicheckuid` `.bililogin` | `bilibili-api-python`、`curl_cffi`（可选）、`nonebot-adapter-onebot`、ffmpeg（可自动下载） |
| [抽老婆 / 强娶](#2-抽老婆--强娶) | `Plugins/Wife.py` | `抽老婆`、`强娶 @群友` | 无 |
| [戳一戳 MC 冷知识](#3-戳一戳-mc-冷知识) | `Plugins/Poke.py` | 群内戳一戳机器人 | 无 |
| [天气查询](#4-天气查询) | `Plugins/Weather.py` | `.weather <城市>` | uapis.cn Token |
| [今天吃什么](#5-今天吃什么) | `Plugins/Meal.py` | `早上/中午/晚上吃什么` | 无 |
| [猜数字](#6-猜数字) | `Plugins/Guessnum.py` | `开始猜数字` | 无 |
| [抽签](#7-抽签) | `Plugins/Lots.py` | `.cq <事宜>` | 无 |
| [摸摸表情包](#8-摸摸表情包) | `Plugins/Motou.py` | 群内 `摸摸` + @某人 | uapis.cn Token |
| [插件共享配置](#9-插件共享配置) | `Plugins/Config.py` | — | 无 |

> **插件元数据与 `/help` 集成**：上述插件都声明了 `__plugin_meta__`（名称 / 描述 / 用法 / 版本 / 作者 **DICEALPHAHU**），因此能在 WebUI 的「插件」页正常显示并可单独启停；`Plugins/HelpEntry.py` 还会把这些指令补登记到 `/help` 的命令列表（含 Bili 的管理指令），群里发 `/help` 即可看到。

### 1. B站 UP 动态监控与视频推送

`Plugins/Bili.py`

- **新投稿监控**：按 `BILI_UP_WAITSEC`（默认 30 秒，建议 ≥300 秒）轮询指定 UP 主的投稿列表，发现新视频后推送到 `Config.toml` 的 `message_groups`。
- **快照 + 视频双形态推送**：以 `BILI_WATCHER_FRAMESEC` 间隔抽帧生成快照图；视频体积不超过 `BILI_VIDEO_MAX_MB` 时直接转发视频，超限则只发快照。
- **开头黑场自动裁剪**：QQ / NapCat 会取视频第 0 帧当封面，若视频开头是黑场淡入，封面就是全黑。插件用 ffmpeg 的 `blackdetect` 自动裁掉开头黑场（可用 `BILI_TRIM_BLACK_ENABLED` 关闭）。
- **ffmpeg 自动获取**：系统里没有 ffmpeg 时，会按「华为云 PyPI 镜像 → PyPI 官方 → GitHub」顺序自动下载一份到 `Temp/ffmpeg/`（约 40MB，只下一次），也可以手动指定 `BILI_FFMPEG_PATH`。
- **防重复推送**：视频 ID 缓存到 `Temp/Bili/bvtemp.json`，进程重启也不会重复推送；推送过程有重试与锁保护。判定「新视频」以**发布时间戳**为准（BV 号只用于同一时间戳下的删稿重发），接口返回更旧的稿件时忽略且不会回退缓存；首次运行（无缓存）时超过 3 天的旧稿只记缓存不推送。
- **临时文件自动清理**：成品 / 临时文件按 `BILI_VIDEO_KEEP_DAYS`（默认 3 天）清理，超期缓存 7 天清理。
- **手动指令**（均需超级用户）：

  | 指令 | 说明 |
  |---|---|
  | `.bv <B站视频链接>` | 解析视频（支持 `b23.tv` 短链）并返回快照图 |
  | `.biliwatch` | 立即手动触发一次监控 |
  | `.bilistatus` | 查看当前监控状态 |
  | `.biliclear` | 清理缓存与临时文件 |
  | `.bilicheckuid` | 校验配置的 UP UID 是否有效 |
  | `.bililogin` | 生成 B 站扫码登录二维码（只写文件与控制台，不发到群里） |

> 数据来源是 **`bilibili_api` 的用户投稿接口**（`space/wbi/arc/search`），只取最新一页 5 条、按发布时间取最新，不再依赖任何第三方聚合接口。
>
> **匿名可用**：不登录也能取到列表；登录（`/bililogin` 扫码，或 `.env` 里填 `BILI_COOKIE`）后风控更宽松、部分高清源更全。
>
> **数据源开关**：`BILI_LIST_SOURCE=auto`（默认：官方接口优先，失败回退 uapis）/ `bilibili_api`（只用官方）/ `uapis`（只用第三方聚合接口，即老行为）。要回退到 old 方案只需改这一个值。
>
> **反风控开关**：`BILI_HTTP_BACKEND=auto`（默认，装了 `curl_cffi` 就用浏览器 TLS 指纹）/ `httpx`（强制纯 httpx）。视频下载固定走 httpx，并会避开已知会 403 的 PCDN 域名。
>
> ⚠️ 实测对比（2026-10-21）：对同一个 UP，官方接口返回 `BV17LJd6uEkx`（2026-10-20），uapis 返回 `BV1qqNR6PEf4`（2026-07-18）—— **落后 94 天**。uapis 的 `(ps, pn)` 分键缓存会冻结在某个时间点，这正是当初换数据源的原因。

### 2. 抽老婆 / 强娶

`Plugins/Wife.py`

- 群内发送 **`抽老婆`**：从当天群成员里随机抽一位（默认不抽自己），附带头像与今日份文案。
- 群内发送 **`强娶 @某群友`**：已有老婆时可以改嫁指定群友，**每人每天限一次**。
- 记录按「群 + 日期」分开存放，抽取记录在 `Temp/Wife.json`，强娶记录在 `Temp/WifeForce.json`，自动清理超过 7 天的历史；会自动把旧版 `Data/Wife.json` 迁移到新位置。

### 3. 戳一戳 MC 冷知识

`Plugins/Poke.py` + `Plugins/WikiDatabase.json`

- 群内戳一戳机器人，随机回复一条 **Minecraft 冷知识**（带日期、星期、时间与分类出处）。
- 带 **120 秒冷却**，冷却中会从 10 条随机吐槽里选一条回复。
- 冷知识库是外部 JSON（`Plugins/WikiDatabase.json`），格式为 `[{ title, content, category }, ...]`，可以自行增删。

### 4. 天气查询

`Plugins/Weather.py`

- 群内发送 **`.weather <城市名>`**，返回该城市的实时天气、温度、湿度、风向风力与未来 1 天预报。
- 数据来自 uapis.cn，需要在 `.env` 里配置 `UAPIPRO_TOKEN`。

### 5. 今天吃什么

`Plugins/Meal.py` + `Plugins/MealChoose.json`

- 群内发送 **`早上吃什么`** / **`中午吃什么`** / **`晚上吃什么`**，随机推荐一道菜并附带图片。
- 菜品与图片在 `Plugins/MealChoose.json` 里按「早上 / 中午 / 晚上」三个时段分组配置，可自由增删。

### 6. 猜数字

`Plugins/Guessnum.py`

- 群内发送 **`开始猜数字`** 开局，机器人在 **1~1000** 里想一个数字。
- 之后群友**直接发纯数字**猜测即可，会提示「大了 / 小了」；每人 2 秒冷却，猜中即结束本局。

### 7. 抽签

`Plugins/Lots.py`

- 群内发送 **`.cq <你要求签的事情>`**，返回「大吉 / 吉 / 末吉 / 末凶 / 凶 / 大凶」的今日签文，结果池可自行修改。

### 8. 摸摸表情包

`Plugins/Motou.py`

- 群内发送包含 **「摸摸」并且 @ 了某人** 的消息，机器人回一张随机摸摸表情包。
- 图片来自 uapis.cn，需要配置 `UAPIPRO_TOKEN`。

### 9. 插件共享配置

`Plugins/Config.py`

- 用 `nonebot.get_plugin_config` 把上面各插件的配置集中到一个 Pydantic 模型里，统一从 `.env` / 环境变量读取。
- 注意 NoneBot 取环境变量时按**字段名大写**匹配（不做分词），所以 `uapipro_token` 对应的变量名是 **`UAPIPRO_TOKEN`**。

### 新增依赖与配置

`pyproject.toml` 相比上游新增：

```toml
dependencies = [
    # ...
    "bilibili-api-python>=17.4.2",   # B站视频下载 / 解析 / UP 投稿列表
    "curl_cffi>=0.13.0,<0.15.0",     # B站请求的浏览器 TLS 指纹（可选，可用 BILI_HTTP_BACKEND=httpx 关闭）
    "nonebot-adapter-onebot",        # 上述插件基于 OneBot V11 事件（群消息 / 戳一戳 / 主动推送）
]

[[tool.nonebot.adapters]]
name = "OneBot V11"
module_name = "nonebot.adapters.onebot.v11"
```

需要在 `.env` 中配置的变量（完整注释见 [`.env.example`](./.env.example)）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `UAPIPRO_TOKEN` | 空 | uapis.cn 的 API Token（天气 / 摸摸使用；B站监控已不再依赖） |
| `BILI_WATCHER_ENABLED` | `false` | 是否启用 UP 主新视频监控 |
| `BILI_UP_UID` | `0` | 要监控的 UP 主 UID |
| `BILI_UP_WAITSEC` | `30` | 轮询间隔（秒），建议 ≥300 |
| `BILI_WATCHER_FRAMESEC` | `2` | 视频抽帧间隔（秒） |
| `BILI_VIDEO_MAX_MB` | `50` | 推送视频的体积上限（MB） |
| `BILI_VIDEO_KEEP_DAYS` | `3` | 视频临时文件保留天数 |
| `BILI_FFMPEG_PATH` | 空 | ffmpeg 路径，留空自动探测 / 下载 |
| `BILI_FFMPEG_AUTO_DOWNLOAD` | `true` | 找不到 ffmpeg 时自动下载 |
| `BILI_LIST_SOURCE` | `auto` | 监控数据源：`auto`（官方优先，失败回退 uapis）/ `bilibili_api` / `uapis` |
| `BILI_HTTP_BACKEND` | `auto` | B站请求后端：`auto` / `httpx` / `curl_cffi` |
| `BILI_COOKIE` | 空 | 直接注入 B 站 Cookie（`SESSDATA=...; bili_jct=...`），留空则用扫码登录保存的凭证 |
| `BILI_TRIM_BLACK_ENABLED` | `true` | 是否裁掉视频开头黑场 |
| `BILI_BLACK_PIXEL_THRESHOLD` | `0.10` | 黑场亮度阈值 |
| `BILI_BLACK_MIN_DURATION` | `0.3` | 黑场最短持续秒数 |
| `BILI_MAX_TRIM_SECONDS` | `5.0` | 开头最多裁掉多少秒 |
| `BILI_MAX_PROCESS_MINUTES` | `10.0` | 超过该时长的视频不做重编码 |

### 开发提示

- 上游内置插件 `Scripts.Plugins.Events` 以 `priority=1, block=True` 截断群消息事件，因此新增的群聊插件必须使用 **`priority=0`** 才能收到事件。
- `Plugins/` 不在上游的 `[tool.ruff.lint.isort] known-first-party` 列表里，import 顺序如有告警可自行把 `Plugins` 加进去。
- B站视频解析会下载视频到 `Temp/Bili/`，请确保磁盘空间充足，并注意遵守 B站的使用条款与内容版权。

### *New in this fork*

<i>

Everything new lives in `Plugins/` and is registered in `pyproject.toml` under `[tool.nonebot] plugins`, so each one can be toggled individually (author: **DICEALPHAHU**). Each plugin declares `__plugin_meta__`, so the WebUI plugin page shows a proper name, description, version and author, and `Plugins/HelpEntry.py` registers these commands into the `/help` listing.

- **Bilibili uploader watcher** (`Bili.py`) – polls a configured uploader for new videos (default every 30s) and pushes a snapshot image or the video itself into the configured message groups. It auto-trims the black fade-in at the start of a video with ffmpeg (auto-downloaded on demand), de-duplicates pushes across restarts, retries failures, and cleans up temporary files. Admin commands: `.bv <url>`, `.biliwatch`, `.bilistatus`, `.biliclear`, `.bilicheckuid`, `.bililogin`. The video list comes straight from Bilibili (`bilibili_api`, no third-party aggregator) and works anonymously; an optional QR login (`/bililogin`, never posted to the group) or a `BILI_COOKIE` value enables a full credential. `BILI_HTTP_BACKEND=auto` uses a browser TLS fingerprint via `curl_cffi` when it is installed. Newness is decided by publish timestamp only (never by BV number alone), the cache timestamp can never regress, and videos older than 3 days are never pushed on a cold start. See [B站 UP 动态监控](#1-b站-up-动态监控与视频推送) for details.
- **"Draw a wife" / forced marriage** (`Wife.py`) – `抽老婆` picks a random group member once per day; `强娶 @someone` remarries to a specific member (once per day per person).
- **Poke trivia** (`Poke.py` + `WikiDatabase.json`) – poking the bot in a group returns a random Minecraft fun fact, with a 120s cooldown and 10 randomized cooldown replies. The trivia database is an editable JSON file.
- **Weather** (`Weather.py`) – `.weather <city>` returns current conditions plus a 1-day forecast via uapis.cn.
- **What to eat** (`Meal.py` + `MealChoose.json`) – `早上/中午/晚上吃什么` suggests a random dish with a picture; dishes are configured per time of day in JSON.
- **Guess the number** (`Guessnum.py`) – `开始猜数字` starts a 1–1000 game; group members then just send plain numbers, with a 2s per-user cooldown.
- **Fortune sticks** (`Lots.py`) – `.cq <question>` draws one of six fortune results for the day.
- **Head-pat meme** (`Motou.py`) – sending `摸摸` with an @mention returns a random "pat-pat" meme image via uapis.cn.
- **Shared plugin config** (`Config.py`) – a single Pydantic `get_plugin_config` model for all of the above, read from `.env` / environment variables.

New dependencies: `bilibili-api-python`, `opencv-python`, and the `OneBot V11` adapter. All required environment variables are documented in [`.env.example`](./.env.example); no real token, server address or group ID is included in this repository.

</i>

## 📸 效果展示

> 以下截图来自上游项目的自带功能，用于展示框架能力；本 Fork 新增的群聊插件可直接用文字指令体验。

### 🖥️ WebUI 管理面板

| 仪表盘 | 服务器管理 |
|---|---|
| <p align="center"><img src=".github/images/shows/webui/dashboard.png"></p> | <p align="center"><img src=".github/images/shows/webui/server.png"></p> |

| 配置中心 | 适配器管理 |
|---|---|
| <p align="center"><img src=".github/images/shows/webui/config.png"></p> | <p align="center"><img src=".github/images/shows/webui/adapters.png"></p> |

| 插件管理 | 扩展管理 |
|---|---|
| <p align="center"><img src=".github/images/shows/webui/plugins.png"></p> | <p align="center"><img src=".github/images/shows/webui/extensions.png"></p> |

| 插件市场 | 日志查看 |
|---|---|
| <p align="center"><img src=".github/images/shows/webui/market.png"></p> | <p align="center"><img src=".github/images/shows/webui/log.png"></p> |

| 连接聊天平台 | 令牌授权 |
|---|---|
| <p align="center"><img src=".github/images/shows/webui/platform.png"></p> | <p align="center"><img src=".github/images/shows/webui/token.png"></p> |

### 🎨 指令图片渲染

指令输出渲染为精美图片，支持玩家头像与自定义背景：

| `/list` 指令 | `/server` 指令 | `/luck` 指令 |
|---|---|---|
| <p align="center"><img src=".github/images/shows/commands/list.png"></p> | <p align="center"><img src=".github/images/shows/commands/server.png"></p> | <p align="center"><img src=".github/images/shows/commands/luck.png"></p> |

| `/luck rank` 指令 | `/bot about` 指令 | `/help` 指令 | `/help <命令名>` 指令（命令详情） |
|---|---|---|---|
| <p align="center"><img src=".github/images/shows/commands/luck-rank.png"></p> | <p align="center"><img src=".github/images/shows/commands/about.png"></p> | <p align="center"><img src=".github/images/shows/commands/help.png"></p> | <p align="center"><img src=".github/images/shows/commands/help-detailed.png"></p> |

## 快速开始

### 1. 准备配置

```bash
git clone <本仓库地址>
cd <仓库目录>

# 复制环境变量模板并填写（Token、服务器地址、群号等）
cp .env.example .env      # Windows: copy .env.example .env
```

`.env` 至少要配置：

- `SUPERUSERS`：管理员 QQ 号
- `ONEBOT_ACCESS_TOKEN`：OneBot 实现的访问令牌
- `MINECRAFT_WS_URLS` / `MINECRAFT_ACCESS_TOKEN`：Minecraft 鹊桥连接地址与令牌
- `UAPIPRO_TOKEN`（可选）：使用天气 / 摸摸 / B站动态监控时填写

再按需修改 `Config.toml` 里的 `command_groups` / `message_groups`（格式 `"qq_client:群号"`）。

### 2. 启动

```bash
# 使用 uv（推荐，会自动安装依赖并下载 WebUI / 扩展资源）
uv run Watchdog.py

# 或 Windows 下双击上游的 Start.bat（本仓库未随附）
```

完整的安装、配置与使用说明请参阅上游文档：

- [快速开始](https://bot.mcjpg.dev/guide/快速开始.html) — 安装与启动
- [功能特性](https://bot.mcjpg.dev/guide/功能特性.html) — 功能一览
- [指令手册](https://bot.mcjpg.dev/guide/指令手册.html) — 指令说明
- [配置说明](https://bot.mcjpg.dev/unibot/配置说明.html) — 配置文件详解
- [MC 接入](https://bot.mcjpg.dev/adapter/) — 服务器端接入方式

## 与上游的差异

| 内容 | 说明 |
|---|---|
| `Plugins/`（新增） | 8 个群聊娱乐 / 实用插件 + 2 个数据文件 + `HelpEntry.py`（把插件指令接进 `/help`），见上文 |
| `pyproject.toml` | 注册 `Plugins.*`，新增 `bilibili-api-python`、`opencv-python` 依赖与 OneBot V11 适配器 |
| `Config.toml` | 群号等已清空为占位符，其余按个人习惯微调 |
| `Config/Extensions.toml` | 按个人环境调整扩展启停（`Html2Pic` 默认关闭） |
| `.env.example` `.gitignore` `.gitattributes` | 本 Fork 新增，用于模板化配置与避免误提交敏感信息 |
| `CHANGELOG.md` `.github/ISSUE_TEMPLATE/` | 本 Fork 新增：版本变更记录与 issue 模板 |
| `Docs/` `tests/` `.github/workflows/` | 未包含，见上游仓库 |

> 上报的遥测（`Config.toml` 中的 `[telemetry]`）仍然会把匿名聚合数据发给上游官方，介意的话把它设为 `enabled = false`。

## 许可证

本项目采用 **GPL-3.0** 许可证，与上游一致。修改后的代码必须开源并注明出处，**禁止商用**。详见 [LICENSE](./LICENSE)。

本仓库基于 [MineJPGcraft/UniBot](https://github.com/MineJPGcraft/UniBot) 二次开发，原项目版权归 **LonelySail** 及贡献者所有。

## 致谢

- [MineJPGcraft/UniBot](https://github.com/MineJPGcraft/UniBot) — 本项目所基于的上游项目
- [NoneBot2](https://nonebot.dev/) — 高效优雅的异步机器人框架
- [nonebot-adapter-minecraft](https://github.com/17TheWord/nonebot-adapter-minecraft) — Minecraft 协议适配
- [Alconna](https://github.com/ArcletProject/Alconna) — 强大的命令解析库
- [uapis.cn](https://uapis.cn/) — 天气 / 表情包 / B站数据接口
- [bilibili-api-python](https://github.com/nemo2011/bilibili-api) — B站视频解析与下载

---

<p align="center">
  Made with ❤️ by LonelySail · Enhanced by the fork author
</p>
