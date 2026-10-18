"""
抽签小功能，群内专属抽签玩法。

触发方式：群里发送「.cq <需要抽签的事宜>」（. 为默认命令前缀，可随配置修改）。
"""

import random
from datetime import datetime

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from .HelpEntry import register_help_commands

# 抽签结果池（可自行修改）
LOTTERY_RESULTS = ['大吉', '吉', '末吉', '末凶', '凶', '大凶']


def get_today_date() -> str:
    """返回今天的日期字符串，格式为「X年X月X日」。"""
    now = datetime.now()
    return f'{now.year}年{now.month}月{now.day}日'


# priority=0：必须小于 Scripts.Plugins.Events 的 priority=1（它以 block=True 截断事件）。
lots_cmd = on_command('cq', priority=0, block=True)


@lots_cmd.handle()
async def handle_lots(event: GroupMessageEvent, args: Message = CommandArg()):
    lottery_content = args.extract_plain_text().strip()
    if not lottery_content:
        await lots_cmd.finish(
            MessageSegment.reply(event.message_id)
            + Message('宝子要输入抽签的事宜哦～\n用法：.cq <你要求签的事情>')
        )

    today_date = get_today_date()
    sender_name = event.sender.card or event.sender.nickname or '群友'
    lottery_result = random.choice(LOTTERY_RESULTS)

    final_msg = (
        f'今天是{today_date}\n'
        f'{sender_name} 所求事项：【{lottery_content}】\n\n'
        f'结果：【{lottery_result}】'
    )
    await lots_cmd.finish(MessageSegment.reply(event.message_id) + Message(final_msg))


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
