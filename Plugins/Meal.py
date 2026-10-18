"""
群内「吃什么」推荐小功能。

触发方式：群里发送「早上/中午/晚上吃什么」。
"""

import json
import random
from pathlib import Path

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from .HelpEntry import register_help_commands

# 配置文件路径（根目录 Plugin/MealChoose.json）
CONFIG_PATH = Path(__file__).parent / 'MealChoose.json'

# 触发时段
TIME_PERIODS = ['早上', '中午', '晚上']


def load_meal_config() -> dict:
    """加载外置的 meal_choose.json 配置文件。"""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f'配置文件不存在：{CONFIG_PATH}')
    return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))


# 加载食物 + 图片配置
MEAL_CONFIG = load_meal_config()


def is_ask_meal() -> Rule:
    """触发规则：群消息 + 早上/中午/晚上 + 吃什么。"""

    async def _rule(event) -> bool:
        if not isinstance(event, GroupMessageEvent):
            return False
        plain_text = event.get_plaintext().strip()
        return any(
            plain_text.startswith(period) and '吃什么' in plain_text
            for period in TIME_PERIODS
        )

    return Rule(_rule)


# priority=0：必须小于 Scripts.Plugins.Events 的 priority=1（它以 block=True 截断事件）。
ask_meal = on_message(rule=is_ask_meal(), block=True, priority=0)


@ask_meal.handle()
async def handle_meal(event: GroupMessageEvent):
    # 提取触发时段
    plain_text = event.get_plaintext().strip()
    time_period = next(
        period for period in TIME_PERIODS if plain_text.startswith(period)
    )

    # 从配置中获取对应时段的食物 + 图片
    food_dict = MEAL_CONFIG[time_period]
    random_food = random.choice(list(food_dict.keys()))
    food_img_url = food_dict[random_food]
    # 图片链接非空时拼接尺寸限制（OneBot 协议支持 url?size=宽x高）
    if food_img_url:
        food_img_url = f'{food_img_url}?size=600x450'
    food_img = MessageSegment.image(food_img_url)

    final_msg = (
        f'🍽这里建议你吃🍽\n'
        f'{random_food}\n'
        f'{food_img}'
    )
    await ask_meal.finish(Message(final_msg))


__plugin_meta__ = PluginMetadata(
    name='今天吃什么',
    description='按时段推荐吃什么，附食物图片',
    usage='早上吃什么 / 中午吃什么 / 晚上吃什么',
    extra={'author': 'DICEALPHAHU', 'version': '1.0'},
)

register_help_commands([
    {
        'name': 'meal',
        'description': '按时段推荐吃什么（早上/中午/晚上）',
        'usage': '早上吃什么',
    },
])
