"""
群内关键词自动回复。

配置文件：``Data/KeywordReply.toml``（首次启动自动生成带注释的示例）。
文件改动后自动重载（比较修改时间），也可以用 ``/关键词重载`` 手动刷新。

注意 priority=0 且 **不能 block**：``Scripts.Plugins.Events``（群消息 ↔ 游戏内互转）
在 priority=1，如果这里 block=True，所有群消息都会在这一层被截断，游戏里再也收不到
群消息。命中关键词时用 ``finish`` 中断，未命中的消息原样放行。
"""

from __future__ import annotations

import re
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from Scripts.Config import config
from Scripts.Logging import logger
from .HelpEntry import register_help_commands

CONFIG_PATH = Path('Data/KeywordReply.toml')

DEFAULT_CONFIG = '''# 关键词自动回复配置
#
# 每条规则一个 [[rules]] 块（把行首的 # 去掉即可启用）：
#   keywords = ["关键词", "另一种说法"]   命中任意一个即可
#   reply    = "回复内容"                 支持多行 """..."""
#   match    = "contains"                 匹配方式：contains 包含 / exact 完全相同 / regex 正则
#   groups   = []                         只在这些群生效，留空 = Config.toml 里配置的所有消息群
#   at       = false                      回复时是否 @ 触发的人
#   cooldown = 5                          同一条规则在同一群的冷却秒数，0 = 不限
#   enabled  = true                       单独关掉这条规则
#
# 改完保存即生效（插件会检测修改时间自动重载），也可以发 /关键词重载 手动刷新。

# 总开关
enabled = true

# 示例 1：有人问「在吗」就回一句并 @ 他（去掉 # 即生效）
# [[rules]]
# keywords = ["在吗", "人呢", "有人吗"]
# reply = "在的，有事直接说～"
# at = true
# cooldown = 30

# 示例 2：多行回复
# [[rules]]
# keywords = ["服务器地址", "怎么进服"]
# reply = """服务器地址：play.example.com
# 版本：1.20.1
# 进服前请先看群公告里的白名单登记。"""
# cooldown = 10

# 示例 3：正则匹配
# [[rules]]
# keywords = ["(怎么|如何).{0,8}白名单"]
# reply = "白名单在群公告第 2 条，按里面的链接提交申请即可。"
# match = "regex"
# cooldown = 10

# 示例 4：只在指定群生效（群号要带引号）
# [[rules]]
# keywords = ["抽奖"]
# reply = "本群抽奖只在周五晚 8 点开启。"
# groups = ["123456789"]
'''


@dataclass(slots=True)
class KeywordRule:
    """一条关键词规则。"""

    index: int
    keywords: list[str]
    reply: str
    match: str = 'contains'
    groups: set[str] = field(default_factory=set)
    at: bool = False
    cooldown: int = 5
    enabled: bool = True
    pattern: re.Pattern[str] | None = None

    def hit(self, text: str) -> bool:
        """判断文本是否命中本规则。"""
        if self.match == 'exact':
            folded = text.casefold()
            return any(folded == keyword.casefold() for keyword in self.keywords)
        if self.match == 'regex':
            return bool(self.pattern and self.pattern.search(text))
        folded = text.casefold()
        return any(keyword.casefold() in folded for keyword in self.keywords)


def _as_str_list(value) -> list[str]:
    """把配置里的数组统一成去空白的字符串列表。"""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def load_rules() -> list[KeywordRule]:
    """读取配置文件并解析成规则列表，出错时返回空列表。"""
    if not CONFIG_PATH.exists():
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(DEFAULT_CONFIG, encoding='Utf-8')
            logger.info(f'关键词回复：已生成示例配置 {CONFIG_PATH}，按需修改后自动生效。')
        except OSError as error:
            logger.warning(f'关键词回复：示例配置写入失败（{error}），本次不启用。')
            return []
    global _last_error
    try:
        data = tomllib.loads(CONFIG_PATH.read_text('Utf-8'))
    except (OSError, tomllib.TOMLDecodeError) as error:
        _last_error = str(error)
        logger.error(f'关键词回复：配置读取失败（{error}），本次不启用。')
        return []
    _last_error = ''
    if not data.get('enabled', True):
        return []

    rules: list[KeywordRule] = []
    for position, item in enumerate(data.get('rules') or [], start=1):
        if not isinstance(item, dict):
            continue
        if not item.get('enabled', True):
            continue
        keywords = _as_str_list(item.get('keywords'))
        reply = str(item.get('reply') or '').strip()
        if not keywords or not reply:
            logger.warning(f'关键词回复：第 {position} 条规则缺少 keywords 或 reply，已跳过。')
            continue
        match = str(item.get('match') or 'contains').strip().lower()
        if match not in ('contains', 'exact', 'regex'):
            logger.warning(f'关键词回复：第 {position} 条规则的 match="{match}" 无效，按 contains 处理。')
            match = 'contains'
        pattern = None
        if match == 'regex':
            try:
                pattern = re.compile('|'.join(f'(?:{keyword})' for keyword in keywords), re.IGNORECASE)
            except re.error as error:
                logger.warning(f'关键词回复：第 {position} 条规则正则有误（{error}），已跳过。')
                continue
        try:
            cooldown = max(0, int(item.get('cooldown', 5)))
        except (TypeError, ValueError):
            cooldown = 5
        rules.append(
            KeywordRule(
                index=position,
                keywords=keywords,
                reply=reply,
                match=match,
                groups=set(_as_str_list(item.get('groups'))),
                at=bool(item.get('at', False)),
                cooldown=cooldown,
                pattern=pattern,
            )
        )
    return rules


_last_error = ''
_rules: list[KeywordRule] = []
_mtime: float | None = None
_loaded = False
_last_fire: dict[tuple[int, str], float] = {}


def refresh(force: bool = False) -> list[KeywordRule]:
    """配置有改动（或 force）时重新加载，返回当前规则列表。"""
    global _rules, _mtime, _loaded
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        mtime = None
    if force or not _loaded or mtime != _mtime:
        _loaded = True
        _rules = load_rules()
        try:
            _mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            _mtime = mtime
        _last_fire.clear()
        logger.info(f'关键词回复：已加载 {len(_rules)} 条规则。')
    return _rules


def _configured_group_ids() -> set[str]:
    """Config.toml 中 message_groups 里的群号集合。"""
    ids: set[str] = set()
    for group_info in config.message_groups:
        _platform, separator, group_id = str(group_info).partition(':')
        if separator and group_id:
            ids.add(group_id)
    return ids


keyword_watcher = on_message(priority=0, block=False)


@keyword_watcher.handle()
async def handle_keyword(event: MessageEvent):
    """命中关键词就回复并中断事件链，否则立即放行给后面的 matcher。"""
    group_id = getattr(event, 'group_id', None)
    if group_id is None:
        # 私聊、游戏内聊天等不是群消息，跳过
        return
    text = event.get_plaintext().strip()
    if not text:
        return
    # 指令消息交给指令系统，不做关键词匹配
    if any(text.startswith(prefix) for prefix in config.command_start):
        return

    group_id = str(group_id)
    allowed_groups = _configured_group_ids()

    now = time.time()
    for rule in refresh():
        if rule.groups:
            if group_id not in rule.groups:
                continue
        elif allowed_groups and group_id not in allowed_groups:
            continue
        if not rule.hit(text):
            continue
        key = (rule.index, group_id)
        if rule.cooldown > 0 and now - _last_fire.get(key, 0.0) < rule.cooldown:
            return
        _last_fire[key] = now
        message = Message()
        if rule.at:
            message += MessageSegment.at(getattr(event, 'user_id', 0))
            message += MessageSegment.text(' ')
        message += MessageSegment.text(rule.reply)
        logger.info(f'关键词回复：群 {group_id} 命中规则 #{rule.index}（{rule.keywords[0]}）')
        await keyword_watcher.finish(message)


reload_command = on_command('关键词重载', aliases={'keywordreload'}, permission=SUPERUSER, block=True, priority=0)


@reload_command.handle()
async def handle_reload():
    """手动重新读取配置文件。"""
    rules = refresh(force=True)
    text = f'已重新加载 {len(rules)} 条关键词规则。'
    if _last_error:
        text += f'\n配置有语法错误，已忽略：{_last_error}'
    await reload_command.finish(text)


list_command = on_command('关键词列表', aliases={'keywordlist'}, permission=SUPERUSER, block=True, priority=0)


@list_command.handle()
async def handle_list():
    """列出当前生效的关键词规则。"""
    rules = refresh()
    if not rules:
        await list_command.finish(f'当前没有生效的关键词规则（配置文件：{CONFIG_PATH}）。')
    lines = [f'共 {len(rules)} 条关键词规则：']
    for rule in rules:
        scope = '、'.join(sorted(rule.groups)) if rule.groups else '所有消息群'
        keywords = '、'.join(rule.keywords[:3])
        if len(rule.keywords) > 3:
            keywords += ' 等'
        lines.append(f'#{rule.index} [{rule.match}] {keywords} → {scope}（冷却 {rule.cooldown}s）')
    await list_command.finish('\n'.join(lines))


__plugin_meta__ = PluginMetadata(
    name='关键词回复',
    description='群消息命中关键词时自动回复，词库可配置',
    usage='（被动触发）在群里发到关键词；管理员可用 /关键词重载、/关键词列表',
    extra={'author': 'DICEALPHAHU', 'version': '1.0'},
)

register_help_commands([
    {
        'name': 'keywordreload',
        'description': '重新加载关键词回复配置（管理员）',
        'usage': '/关键词重载',
    },
    {
        'name': 'keywordlist',
        'description': '查看当前生效的关键词回复规则（管理员）',
        'usage': '/关键词列表',
    },
])


# 启动时先把配置文件落盘，方便直接编辑（不存在时会生成带注释的示例）
refresh()
