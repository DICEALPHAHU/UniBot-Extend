"""
新成员入群欢迎。

配置文件：``Data/Welcome.toml``（首次启动自动生成带注释的示例）。
文件改动后自动重载（比较修改时间），管理员可在群里发 ``/欢迎测试`` 预览效果。

只监听 OneBot V11 的入群通知，与 ``Scripts.Plugins.Events`` 里的 Minecraft
玩家进服事件互不影响。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nonebot import on_command, on_notice
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from Scripts.Config import config
from Scripts.Logging import logger
from .HelpEntry import register_help_commands

CONFIG_PATH = Path('Data/Welcome.toml')

DEFAULT_CONFIG = '''# 新成员入群欢迎
#
# 占位符：
#   {at}         @新成员（会拆成真正的 @ 段）
#   {nickname}   新成员昵称（群名片优先）
#   {group}      群名称
#   {group_id}   群号
#   {count}      当前群人数
#   {guide}      下面 guide 里的群规/指引长文

# 总开关
enabled = true

# 只对 Config.toml 里 message_groups 配置的群生效。
# 开着可以避免把机器人拉进临时测试群时也刷欢迎；要全群生效就改成 false。
only_message_groups = true

# 默认欢迎文案（所有群通用）
default = """{at} 欢迎加入 {group}！🎉

{guide}

有问题直接在群里问，看到会回～"""

# 群规 / 指引长文，用 {guide} 插到文案里；不需要就留空。
# 多行写三个引号里，改完保存即生效。
guide = """进服前请先看群公告（白名单 / 客户端 / 常见问题都在里面）"""

# 单个群覆盖默认文案（群号要带引号，放在最后）：
# [groups."123456789"]
# enabled = true
# template = "欢迎 {at} 加入！本群是分群，进服须知见群公告。"
# guide = ""
'''

@dataclass(slots=True)
class WelcomeBlock:
    """一套欢迎文案。"""

    enabled: bool = True
    template: str = ''
    guide: str = ''


@dataclass(slots=True)
class WelcomeSettings:
    """整份配置。"""

    enabled: bool = True
    only_message_groups: bool = True
    default: WelcomeBlock = field(default_factory=WelcomeBlock)
    groups: dict[str, WelcomeBlock] = field(default_factory=dict)


class _SafeMapping(dict):
    """缺键时保留原样的 format 映射，避免一个笔误把整条欢迎发崩。"""

    def __missing__(self, key: str) -> str:
        return '{' + key + '}'


def _block(data: Any) -> WelcomeBlock:
    """从字典解析一套文案块。"""
    if not isinstance(data, dict):
        return WelcomeBlock()
    return WelcomeBlock(
        enabled=bool(data.get('enabled', True)),
        template=str(data.get('template') or data.get('default') or ''),
        guide=str(data.get('guide') or ''),
    )


def load_settings() -> WelcomeSettings:
    """读取配置文件，出错时返回「不启用」的默认值。"""
    if not CONFIG_PATH.exists():
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(DEFAULT_CONFIG, encoding='Utf-8')
            logger.info(f'入群欢迎：已生成示例配置 {CONFIG_PATH}，按需修改后自动生效。')
        except OSError as error:
            logger.warning(f'入群欢迎：示例配置写入失败（{error}），本次不启用。')
            return WelcomeSettings(enabled=False)
    try:
        data = tomllib.loads(CONFIG_PATH.read_text('Utf-8'))
    except (OSError, tomllib.TOMLDecodeError) as error:
        logger.error(f'入群欢迎：配置读取失败（{error}），本次不启用。')
        return WelcomeSettings(enabled=False)

    default = _block(
        {
            'enabled': data.get('enabled', True),
            'template': data.get('default') or '',
            'guide': data.get('guide') or '',
        }
    )
    groups: dict[str, WelcomeBlock] = {}
    raw_groups = data.get('groups') or {}
    if isinstance(raw_groups, dict):
        for group_id, item in raw_groups.items():
            groups[str(group_id).strip()] = _block(item)
    return WelcomeSettings(
        enabled=bool(data.get('enabled', True)),
        only_message_groups=bool(data.get('only_message_groups', True)),
        default=default,
        groups=groups,
    )


_settings = WelcomeSettings(enabled=False)
_mtime: float | None = None
_loaded = False


def refresh(force: bool = False) -> WelcomeSettings:
    """配置有改动（或 force）时重新加载。"""
    global _settings, _mtime, _loaded
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        mtime = None
    if force or not _loaded or mtime != _mtime:
        _loaded = True
        _settings = load_settings()
        try:
            _mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            _mtime = mtime
        logger.info(
            f'入群欢迎：已加载配置（{"启用" if _settings.enabled else "关闭"}，'
            f'{len(_settings.groups)} 个群单独配置）。'
        )
    return _settings


def _configured_group_ids() -> set[str]:
    """Config.toml 中 message_groups 里的群号集合。"""
    ids: set[str] = set()
    for group_info in config.message_groups:
        _platform, separator, group_id = str(group_info).partition(':')
        if separator and group_id:
            ids.add(group_id)
    return ids


def _fill(text: str, values: dict[str, Any]) -> str:
    """安全地做占位符替换，花括号写错时原样返回。"""
    try:
        return text.format_map(_SafeMapping(values))
    except (ValueError, IndexError, KeyError):
        return text


def build_message(template: str, values: dict[str, Any], at_user: int | None) -> Message:
    """把模板转成消息：{at} 拆成真正的 @ 段，其余占位符填文本。"""
    message = Message()
    for index, chunk in enumerate(template.split('{at}')):
        if index and at_user is not None:
            message += MessageSegment.at(at_user)
        filled = _fill(chunk, values)
        if filled:
            message += MessageSegment.text(filled)
    return message


async def render(bot: Bot, group_id: int, user_id: int, settings: WelcomeSettings) -> Message | None:
    """按群配置渲染欢迎消息；该群不生效时返回 None。"""
    key = str(group_id)
    block = settings.groups.get(key)
    template = (block.template if block and block.template.strip() else settings.default.template).strip()
    guide = (block.guide if block and block.guide.strip() else settings.default.guide).strip()
    if block is not None and not block.enabled:
        return None
    if not template:
        return None

    need_group = '{group}' in template
    need_count = '{count}' in template
    need_nickname = '{nickname}' in template
    group_name, member_count = key, 0
    if need_group or need_count:
        try:
            info = await bot.get_group_info(group_id=group_id)
            group_name = str(info.get('group_name') or key)
            member_count = int(info.get('member_count') or 0)
        except Exception as error:
            logger.debug(f'入群欢迎：获取群 {key} 信息失败（{error}），使用群号代替群名。')
    nickname = str(user_id)
    if need_nickname:
        try:
            member = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
            nickname = str(member.get('card') or member.get('nickname') or user_id)
        except Exception as error:
            logger.debug(f'入群欢迎：获取成员 {user_id} 昵称失败（{error}），使用 QQ 号代替。')

    values = {
        'nickname': nickname,
        'group': group_name,
        'group_id': key,
        'count': member_count,
        'guide': guide,
    }
    return build_message(template, values, user_id)


welcome_watcher = on_notice(priority=0, block=False)


@welcome_watcher.handle()
async def handle_group_increase(bot: Bot, event: GroupIncreaseNoticeEvent):
    """新成员入群时发欢迎。"""
    # 机器人自己被拉进群时不发
    if event.user_id == event.self_id:
        return
    settings = refresh()
    if not settings.enabled:
        return
    group_id = str(event.group_id)
    if settings.only_message_groups and group_id not in _configured_group_ids():
        return
    message = await render(bot, event.group_id, event.user_id, settings)
    if message is None:
        return
    await welcome_watcher.send(message)
    logger.info(f'入群欢迎：已向群 {group_id} 发送欢迎（新成员 {event.user_id}）。')


preview_command = on_command('欢迎测试', aliases={'welcometest'}, permission=SUPERUSER, block=True, priority=0)


@preview_command.handle()
async def handle_preview(bot: Bot, event: GroupMessageEvent):
    """在当前群发一条欢迎消息，用来预览文案效果。"""
    settings = refresh()
    message = await render(bot, event.group_id, event.user_id, settings)
    if message is None:
        await preview_command.finish('当前群没有生效的欢迎文案，请检查 Data/Welcome.toml。')
    await preview_command.finish(message)


reload_command = on_command('欢迎重载', aliases={'welcomereload'}, permission=SUPERUSER, block=True, priority=0)


@reload_command.handle()
async def handle_reload():
    """手动重新读取配置文件。"""
    settings = refresh(force=True)
    state = '启用' if settings.enabled else '关闭'
    await reload_command.finish(f'已重新加载入群欢迎配置（{state}）。')


__plugin_meta__ = PluginMetadata(
    name='入群欢迎',
    description='新成员入群时发欢迎语，文案与群规可配置',
    usage='（被动触发）有人进群时自动发送；管理员可用 /欢迎测试、/欢迎重载',
    extra={'author': 'DICEALPHAHU', 'version': '1.0'},
)

register_help_commands([
    {
        'name': 'welcometest',
        'description': '在当前群预览入群欢迎文案（管理员）',
        'usage': '/欢迎测试',
    },
    {
        'name': 'welcomereload',
        'description': '重新加载入群欢迎配置（管理员）',
        'usage': '/欢迎重载',
    },
])


# 启动时先把配置文件落盘，方便直接编辑（不存在时会生成带注释的示例）
refresh()
