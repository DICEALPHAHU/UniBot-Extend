# 更新日志

本项目是 [MineJPGcraft/UniBot](https://github.com/MineJPGcraft/UniBot) 的个人增强 Fork。

- 版本号规则：`<上游版本>-hu.<补丁号>`，例如 `v1.0.6-hu.1` 表示基于上游 `v1.0.6` 的第 1 次发布。
- 上游自身的变更请见 [上游 Releases](https://github.com/MineJPGcraft/UniBot/releases)。
- 格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 新增

- **`Plugins/Wiki.py`：Minecraft Wiki 词条查询**（`.wiki <关键词>`），返回简介、缩略图与原文链接，数据源为 `zh.minecraft.wiki` 的 MediaWiki 接口（无需 Token）。
  - 搜索结果按标题贴合度重新排序（Wiki 全文搜索常把版本日志排在词条前面，例如查 `力量（附魔）` 会命中 `携带版0.12.1.b11`）
  - 查询失败时依次回退：接口「您是不是要查」建议词 → 截掉中文疑问词（`铜有哪些变种` → `铜`）→ 本地近似标题匹配（`lava chiken` → `lava chicken`）
  - 词条标题做 URL 编码，含 `&`、`#` 等字符也能正确拼出原文链接
  - 结果缓存 10 分钟（上限 200 条），避免重复查询触发社区 Wiki 限流

- **`Plugins/Player.py`：Minecraft 玩家卡**（`.player <玩家名或 UUID>`），查询正版 Java 玩家的皮肤与披风，渲染「正面 / 背面 / 披风」卡片。
  - 新增 `Plugins/SkinRender.py`：纯 Pillow + numpy 的三维渲染，按模型长宽高把皮肤切成六个面，用透视相机投影后按深度排序绘制
  - 第二层皮肤（帽子、外套、袖子）按 Minecraft 规则外扩（头 0.5 格、其余 0.25 格），有真实厚度，不再是一张纸上叠另一张图
  - 自动区分经典（classic）与纤细（slim）模型；兼容 1.8 之前的 64×32 老皮肤（跳过第二层，镜像复用右半边贴图）
  - 首次使用自动下载中文字体到 `Data/PlayerCard/`（约 10MB，可用 `PLAYER_FONT_PATH` / `PLAYER_FONT_AUTO_DOWNLOAD` 覆盖）
  - 结果缓存 10 分钟；离线服玩家不在 Mojang 记录里，会给出明确提示

- **B站监控改为直连官方接口**（去 uapis 化）：UP 主最新视频列表改用 `bilibili_api` 的用户投稿接口，昵称改用用户信息接口并加 6 小时缓存。
  - 匿名即可工作；支持 `/bililogin` 扫码登录获取凭证（**二维码写到 `Data/Bili/login_qr.png` 并打印到控制台，绝不发群；私聊时直接发图**，有效期 180 秒，扫完或过期后自动删除文件）
  - 也可用 `BILI_COOKIE` 直接注入 Cookie，凭证落盘 `Data/Bili/credential.json`
  - 新增 `BILI_HTTP_BACKEND=auto|httpx|curl_cffi` 开关：装了 `curl_cffi` 就用浏览器 TLS 指纹请求，抗风控更强
  - 下载侧加入 PCDN 域名过滤与 `hvc1` 编码归一化；接口失败改为「静默跳过本轮」，不再刷屏
  - 新增 `BILI_LIST_SOURCE=auto|bilibili_api|uapis` 开关：默认 `auto`（官方接口优先，失败且配了 token 时回退 uapis），可一键切回老方案
  - 抽帧改用 ffmpeg（移除 `opencv-python` 依赖），并同步了线上此前未入库的修复：NapCat 共享临时目录、多适配器下正确选取 OneBot bot、图片改发 bytes

- **`/bot about` 支持自定义维护者信息**：新增「维护者（名字 / QQ）」「上游项目」两行，仓库地址改为本 fork；
  全部可用环境变量覆盖（`BOT_MAINTAINER` / `BOT_MAINTAINER_QQ` / `BOT_REPO_URL` / `BOT_DOCUMENT_URL` / `BOT_INVITE_GROUP`），
  QQ 建议只写在 `.env` 里。修复了 `os.getenv` 读不到 `.env` 的问题（NoneBot 用 `dotenv_values` 解析、不写入进程环境）。

## [v1.0.6-hu.1] - 2026-10-18

### 新增

- **8 个群聊插件**（`Plugins/`，均在 `pyproject.toml` 中注册、可单独启停）：
  - `Bili.py` —— B 站 UP 主动态监控与视频推送
  - `Wife.py` —— 抽老婆 / 强娶
  - `Poke.py` —— 戳一戳 MC 冷知识
  - `Weather.py` —— 天气查询
  - `Meal.py` —— 今天吃什么
  - `Guessnum.py` —— 猜数字小游戏
  - `Lots.py` —— 抽签
  - `Motou.py` —— 摸摸表情包
- **插件元数据**：上述插件均声明 `__plugin_meta__`（名称 / 描述 / 用法 / 版本 / 作者），WebUI 插件页可正常展示与启停。
- **`/help` 集成**：新增 `Plugins/HelpEntry.py`，把 `on_command` 体系的指令补登记进 UniBot 扩展的命令管理器，群里 `/help` 即可看到这些指令（含 Bili 的管理指令）。
- **配置模板化**：新增 `.env.example`、`.gitignore`、`.gitattributes`，避免误提交真实 Token / 群号 / 服务器地址。

### 修复

- **B 站监控推送过期视频**（`Plugins/Bili.py`）：
  - uapis.cn 的 `archives` 接口按 `(ps, pn)` 分键缓存且各键冻结时间不同（`ps=1` 曾长期返回 3 个月前的旧稿，`ps=5` 后来也冻结在 7 月），因此固定请求 `ps=30`，并自行按 `publish_time` 取最新一条；
  - 只有发布时间戳变大才算新视频，接口返回更旧的稿件时忽略且**绝不回退缓存**，避免「最新视频 ↔ 旧视频」反复刷屏；
  - 新增 `FIRST_PUSH_MAX_AGE_DAYS = 3`：首次运行 / 缓存缺失时，超过 3 天的旧稿只写缓存不推送。
- **配置字段名不一致**（`Plugins/Config.py`）：`bili_up_waits_ec` 更正为 `bili_up_waitsec`，使 `.env` 中的 `BILI_UP_WAITSEC` 真正生效（旧名会被忽略并静默退回 30 秒）。
- **日志中的密钥明文**（`Scripts/Logging.py`）：新增 `REDACT_PATTERNS` / `redact_secrets()`，`uapi-*` Token 与 B 站 Cookie 关键字段在落盘前统一替换为 `***`。

### 变更

- **同步上游 v1.0.6**：引入任务中心（`Scripts/Managers/TaskCenter.py`、`Scripts/Api/Tasks.py`）、适配器加载修复、WebUI 资源 `v1.0.6`、`cryptography` 版本区间修正等；`pyproject.toml` 以 v1.0.6 为底重新合入本 Fork 的依赖与插件注册。

### 兼容性说明

- 本版本要求 Python `>=3.11`（`requires-python`），与上游一致。
- 上游在 v1.0.6 中未改动 `Scripts/Extensions/Command.py`、`Scripts/Extensions/Builtin/Commands/Help.py`、`Scripts/Plugins/Events.py`、`Scripts/Rules.py` 与 `Config.toml`，因此本 Fork 的插件与 `/help` 集成无需调整。
- 依赖变更后请在项目根目录执行一次 `uv sync`（Docker 部署则重建镜像）。
