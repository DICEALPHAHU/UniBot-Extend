# 更新日志

本项目是 [MineJPGcraft/UniBot](https://github.com/MineJPGcraft/UniBot) 的个人增强 Fork。

- 版本号规则：`<上游版本>-hu.<补丁号>`，例如 `v1.0.6-hu.1` 表示基于上游 `v1.0.6` 的第 1 次发布。
- 上游自身的变更请见 [上游 Releases](https://github.com/MineJPGcraft/UniBot/releases)。
- 格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

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
