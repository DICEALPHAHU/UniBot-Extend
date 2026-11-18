"""
抽签小功能，群内专属抽签玩法。

触发方式：群里发送「.cq <需要抽签的事宜>」（. 为默认命令前缀，可随配置修改）。
"""

import random
from datetime import datetime

from nonebot import on_command, on_message
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.plugin import PluginMetadata
from Scripts.CommandRules import command_args, command_rule, ev_message_id, sender_name
from .HelpEntry import register_help_commands

# 抽签结果池（可自行修改）
LOTTERY_RESULTS = ['大吉', '吉', '末吉', '末凶', '凶', '大凶']


def get_today_date() -> str:
    """返回今天的日期字符串，格式为「X年X月X日」。"""
    now = datetime.now()
    return f'{now.year}年{now.month}月{now.day}日'


# priority=0：必须小于 Scripts.Plugins.Events 的 priority=1（它以 block=True 截断事件）。
lots_cmd = on_message(rule=command_rule(('cq',)), priority=0, block=True)


def _reply_prefix(event) -> "Message":
    """引用回复段：仅 OneBot 支持 MessageSegment.reply，官方 QQ 返回空。"""
    try:
        from nonebot.adapters.onebot.v11 import Message, MessageSegment

        if type(event).__module__.startswith('nonebot.adapters.onebot'):
            mid = ev_message_id(event)
            if mid:
                return Message(MessageSegment.reply(mid))
    except Exception:
        pass
    from nonebot.adapters.onebot.v11 import Message as _Msg

    return _Msg()


@lots_cmd.handle()
async def handle_lots(event: Event):
    args = Message(command_args(event, ('cq',)))
    lottery_content = args.extract_plain_text().strip()
    if not lottery_content:
        await lots_cmd.finish(
            _reply_prefix(event)
            + Message('宝子要输入抽签的事宜哦～\n用法：.cq <你要求签的事情>')
        )

    today_date = get_today_date()
    sender_display = sender_name(event)
    lottery_result = random.choice(LOTTERY_RESULTS)

    final_msg = (
        f'今天是{today_date}\n'
        f'{sender_display} 所求事项：【{lottery_content}】\n\n'
        f'结果：【{lottery_result}】'
    )
    await lots_cmd.finish(_reply_prefix(event) + Message(final_msg))


__plugin_meta__ = PluginMetadata(
    name='抽签',
    description='群内抽签求吉凶：大吉 / 吉 / 末吉 / 末凶 / 凶 / 大凶',
    usage='.cq <所求事项>\n示例：.cq 今天考试顺利吗',
    extra={'author': 'DICEALPHAHU', 'version': '1.0'},
)

register_help_commands([
    {
        'name': 'cq',
        'description': '抽签求吉凶',
        'usage': '/cq <所求事项>',
    },
])
