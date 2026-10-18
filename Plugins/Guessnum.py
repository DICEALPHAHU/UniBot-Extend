"""
群内「猜数字」小游戏。

触发方式：群里发送「开始猜数字」开局，随后直接发纯数字猜测。
"""

import random
import time

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from .HelpEntry import register_help_commands

# 游戏全局状态
GAME_RUNNING = False
TARGET_NUM = 0
USER_LAST_TIME: dict[int, float] = {}
CD_SECOND = 2
MIN_NUM = 1
MAX_NUM = 1000


async def start_game_rule(event: GroupMessageEvent) -> bool:
    """开局匹配：消息内容为「开始猜数字」。"""
    text = event.message.extract_plain_text().strip()
    return text == '开始猜数字'


# priority=0：必须小于 Scripts.Plugins.Events 的 priority=1（它以 block=True 截断事件）。
# 两个 matcher 保持原有先后（开局在本局猜测之前，靠各自规则互斥）。
start_game = on_message(rule=Rule(start_game_rule), priority=0, block=True)


@start_game.handle()
async def handle_start(event: GroupMessageEvent):
    global GAME_RUNNING, TARGET_NUM, USER_LAST_TIME
    if GAME_RUNNING:
        await start_game.finish('⚠️ 猜数字游戏正在进行中，先猜完本局再开局！')

    TARGET_NUM = random.randint(MIN_NUM, MAX_NUM)
    GAME_RUNNING = True
    USER_LAST_TIME.clear()
    await start_game.send(
        f'🎮 猜数字游戏开始啦！\n'
        f'我已经想好 {MIN_NUM}~{MAX_NUM} 的数字，**直接发纯数字即可猜测**\n'
        f'💡 规则：每人每2秒只能猜一次，连发会被忽略'
    )


async def guess_rule(event: GroupMessageEvent) -> bool:
    """猜测匹配：游戏进行中且消息为整条纯数字。"""
    if not GAME_RUNNING:
        return False
    text = event.message.extract_plain_text().strip()
    return text.isdigit()


guess_handle = on_message(rule=Rule(guess_rule), priority=0, block=True)


@guess_handle.handle()
async def handle_guess(event: GroupMessageEvent):
    global GAME_RUNNING
    user_id = event.user_id
    now = time.time()

    # 冷却限制
    if user_id in USER_LAST_TIME and now - USER_LAST_TIME[user_id] < CD_SECOND:
        return
    USER_LAST_TIME[user_id] = now

    text = event.message.extract_plain_text().strip()
    guess = int(text)
    user_name = event.sender.nickname

    # 超出范围主动提示
    if guess < MIN_NUM or guess > MAX_NUM:
        await guess_handle.send(
            f'「{user_name}」数字超出范围啦，请输入 {MIN_NUM}~{MAX_NUM} 之间的整数！'
        )
        return

    if guess < TARGET_NUM:
        await guess_handle.send(f'「{user_name}」小了，再往上猜猜看～')
        return
    if guess > TARGET_NUM:
        await guess_handle.send(f'「{user_name}」大了，往小了猜猜～')
        return

    GAME_RUNNING = False
    await guess_handle.send(
        f'🎉 恭喜「{user_name}」猜对啦！\n'
        f'正确数字就是：{TARGET_NUM}\n'
        f'游戏结束，发送「开始猜数字」可重新开局'
    )


__plugin_meta__ = PluginMetadata(
    name='猜数字',
    description='群内猜数字小游戏：1~1000，每人每 2 秒限猜一次',
    usage='开始猜数字 —— 开局\n直接发送纯数字 —— 猜测',
    extra={'author': 'DICEALPHAHU', 'version': '1.0'},
)

register_help_commands([
    {
        'name': 'guessnum',
        'description': '开始一局猜数字小游戏',
        'usage': '开始猜数字',
    },
])
