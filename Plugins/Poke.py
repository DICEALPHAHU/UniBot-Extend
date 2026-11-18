"""群内「戳一戳」小功能。

触发方式：
- **OneBot/NapCat**：群内戳一戳机器人（notice.notify.poke）；
- **QQ 官方机器人**：平台以 INTERACTION_CREATE(type=20) 推送，由 ``Plugins.QqPoke``
  转成可处理事件。

两条链路共用同一套冷知识逻辑，回复统一走 ``Scripts.Sender``。
"""

import json
import random
from datetime import datetime
from pathlib import Path

from nonebot import on_notice
from nonebot.adapters import Event
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from Scripts.Rules import message_group_rule
from Scripts.Sender import get_user_id, send_group_text
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


def _is_poke_event(event: Event) -> bool:
    """识别两条链路的戳一戳事件，不写死具体适配器类型。"""
    # 官方 QQ：由 Plugins.QqPoke 构造的事件
    if hasattr(event, 'is_poke') and callable(getattr(event, 'is_poke')):
        try:
            return bool(event.is_poke())
        except Exception:
            return False
    # OneBot：notice.notify.poke 且目标是自己
    module = type(event).__module__
    if module.startswith('nonebot.adapters.onebot.v11'):
        if type(event).__name__ != 'PokeNotifyEvent':
            return False
        try:
            return bool(event.is_tome())
        except Exception:
            return False
    return False


matcher = on_notice(rule=Rule(_is_poke_event), priority=15, block=False)


@matcher.handle()
async def watch_poke(bot, event: Event):
    """被戳时回一条 MC 冷知识；官方 QQ 拿不到具体戳的人，因此不 @。"""
    global last_poke_time
    # 冷却判断
    current_time = getattr(event, 'time', None) or int(datetime.now().timestamp())
    if current_time - last_poke_time < POKE_COOLDOWN:
        await send_group_text(bot, event, random.choice(POKE_COOLDOWN_MSG))
        return
    last_poke_time = current_time

    # 从内存随机选择一条冷知识
    sentence = random.choice(MC_WIKI_DATA) if MC_WIKI_DATA else {}
    message = await turn_message_text(poke_handler(sentence))
    await send_group_text(bot, event, message)


def poke_handler(sentence: dict):
    """生成戳一戳回复的多行文本。"""
    now = datetime.now()
    yield f'{now.strftime("%Y-%m-%d")} 星期{week_mapping[now.weekday()]}  {now.strftime("%H:%M:%S")}'
    if not sentence:
        yield '\n「冷知识数据未加载，请检查 WikiDatabase.json 文件！」'
        return
    yield f'\n「{sentence["content"]}」'
    yield f' —— {sentence["title"]}《{sentence["category"]}》'


week_mapping = ('一', '二', '三', '四', '五', '六', '日')

__plugin_meta__ = PluginMetadata(
    name='戳一戳',
    description='被戳一戳时随机回一句话',
    usage='（被动触发）在群里戳一戳机器人',
    extra={'author': 'DICEALPHAHU', 'version': '1.1'},
)
