"""
群内「戳一戳」小功能。

触发方式：群内戳一戳机器人，随机回复一条 MC 冷知识。
"""

import json
import random
from datetime import datetime
from pathlib import Path

from nonebot import on_notice
from nonebot.adapters.onebot.v11 import PokeNotifyEvent
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me

from Scripts.Rules import message_group_rule
from Scripts.Utils import turn_message_text
from .HelpEntry import register_help_commands

# 戳一戳冷却配置
POKE_COOLDOWN = 120  # 冷却时间，单位秒
last_poke_time = 0  # 记录最后一次触发戳一戳的时间戳
POKE_COOLDOWN_MSG = [
    '别戳啦～我需要休息一下，稍后再试吧～',
    '住手！再戳零件都要掉啦，等会儿再玩～',
    '戳太快啦，Dream都没你手速快，稍等片刻～',
    '别戳惹，让机器人充个能（×××），马上就好～',
    '再戳我可要钻回下界门啦，120秒后再找我玩～',
    '手速拉满了属于是，冷却中ing，稍等～',
    '我的CPU要烧啦，缓一缓再戳～',
    '暂停戳戳！正在加载MC冷知识，稍后解锁～',
    '别戳了别戳了，再戳我就把你送进末地城～',
    '冷却中！快去找点MC方块玩玩，马上就好～',
]

# 冷知识数据文件路径
MC_WIKI_PATH = Path(__file__).parent / 'WikiDatabase.json'

matcher = on_notice(rule=message_group_rule & to_me(), priority=15, block=False)
week_mapping = ('一', '二', '三', '四', '五', '六', '日')


def load_wiki_data() -> list[dict]:
    """加载冷知识数据，文件缺失或格式错误时返回空列表。"""
    try:
        mc_data_list = json.loads(MC_WIKI_PATH.read_text(encoding='utf-8'))
        if not isinstance(mc_data_list, list) or not mc_data_list:
            raise ValueError('JSON 文件必须是数组格式（[]），且至少包含1条数据！')
        return mc_data_list
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as error:
        print(f'【戳一戳小功能】加载冷知识数据失败：{error}')
        return []


# 模块加载时一次性读取冷知识数据
MC_WIKI_DATA = load_wiki_data()


@matcher.handle()
async def watch_poke(event: PokeNotifyEvent):
    # 未戳到机器人则直接返回
    if not event.is_tome():
        return

    # 冷却判断
    global last_poke_time
    current_time = event.time
    if current_time - last_poke_time < POKE_COOLDOWN:
        cool_msg = random.choice(POKE_COOLDOWN_MSG)
        await matcher.finish(cool_msg, at_sender=True)
    last_poke_time = current_time

    # 从内存随机选择一条冷知识
    sentence = random.choice(MC_WIKI_DATA) if MC_WIKI_DATA else {}
    message = await turn_message_text(poke_handler(sentence))
    await matcher.finish(message)


def poke_handler(sentence: dict):
    """生成戳一戳回复的多行文本。"""
    now = datetime.now()
    yield f'{now.strftime("%Y-%m-%d")} 星期{week_mapping[now.weekday()]}  {now.strftime("%H:%M:%S")}'
    if not sentence:
        yield '\n「冷知识数据未加载，请检查 WikiDatabase.json 文件！」'
        return
    yield f'\n「{sentence["content"]}」'
    yield f' —— {sentence["title"]}《{sentence["category"]}》'


__plugin_meta__ = PluginMetadata(
    name='戳一戳',
    description='被戳一戳时随机回一句话',
    usage='（被动触发）在群里戳一戳机器人',
    extra={'author': 'DICEALPHAHU', 'version': '1.0'},
)
