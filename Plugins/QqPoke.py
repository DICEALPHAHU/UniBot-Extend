"""QQ 官方机器人「戳一戳」适配。

平台把戳一戳作为 ``INTERACTION_CREATE`` 推送，但内层 ``type`` 是 **20**，
而 ``nonebot-adapter-qq`` 的 ``ButtonInteraction.type`` 只允许 11/12/13，
于是 pydantic 校验失败、事件在 ``Adapter._handle_event`` 里被 WARNING 丢弃。

本模块只对 **官方 QQ 适配器** 生效，不触碰 OneBot/NapCat 路径：

1. 在 adapter 层挂钩 ``payload_to_event``，识别 ``type == 20`` 时返回宽松事件类；
2. 该事件在群里被触发后，用触发它的 ``event_id`` 被动回复一条 MC 冷知识。

平台 payload 里没有 ``user_openid`` / ``group_member_openid``，因此无法指名道姓，
只能做「群里有人戳了机器人 → 回一条」的降级版本。
"""

from __future__ import annotations

import random
from typing import Any

from nonebot import on_notice
from nonebot.adapters.qq.bot import Bot as QqBot
from nonebot.adapters.qq.event import Event as QqEvent
from nonebot.adapters.qq.event import EventType
from nonebot.compat import PYDANTIC_V2, ConfigDict
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

__plugin_meta__ = PluginMetadata(
    name='QQ 官方戳一戳',
    description='让 QQ 官方机器人也能响应戳一戳（降级版：不指名道姓）',
    usage='在群里戳一下机器人，回一条 Minecraft 冷知识',
    type='application',
    extra={'author': 'DASI', 'version': '1.0'},
)

# 戳一戳在 INTERACTION_CREATE 里的内层类型
POKE_INTERACTION_TYPE = 20
# data.type = 2001 也是戳一戳的标识，两个都认
POKE_DATA_TYPE = 2001

# 冷知识池（与 Plugins/Poke.py 保持一致的风格）
POKE_FACTS = [
    '苦力怕怕猫，也怕豹猫。',
    '末影人怕水，雨天会乱传送。',
    '用床在下界右键会爆炸，别试。',
    '铁傀儡会主动攻击苦力怕。',
    '附魔金苹果不能用普通合成台做，只能开宝箱。',
    '信标的光柱可以穿过基岩。',
    '幻翼在你三天没睡觉时才会生成。',
    '在下界用磁石可以当传送锚点。',
    '猫可以吓跑幻翼。',
    '海龟蛋只在沙滩上孵化。',
]


class QqPokeEvent(QqEvent):
    """戳一戳事件（官方 QQ 的 INTERACTION_CREATE + type=20）。

    基类 ``Event`` 只有一个 ``event_id`` 字段，平台下发的其余字段用
    ``extra='allow'`` 宽松接收，避免因字段增减再次解析失败。
    """

    __type__ = EventType.INTERACTION_CREATE

    if PYDANTIC_V2:
        model_config = ConfigDict(extra='allow')

    type: int = POKE_INTERACTION_TYPE
    scene: str | None = None
    group_openid: str | None = None
    timestamp: str | None = None
    version: int | None = None
    data: dict[str, Any] | None = None

    @classmethod
    def get_event_name(cls) -> str:
        return '戳一戳'

    @classmethod
    def get_event_description(cls) -> str:
        return 'QQ 官方机器人被戳一戳'

    @classmethod
    def get_type(cls) -> str:
        return 'notice'

    def is_poke(self) -> bool:
        inner = (self.data or {}).get('type')
        return self.type == POKE_INTERACTION_TYPE or inner == POKE_DATA_TYPE

    def get_user_id(self) -> str:
        # 平台未下发用户标识，只能留空
        return ''

    def get_session_id(self) -> str:
        return f'qq_poke_{self.group_openid or "unknown"}'


def _patch_payload_to_event() -> None:
    """给 QQ 适配器的 ``payload_to_event`` 打补丁，接管 type=20。"""
    from nonebot.adapters.qq.adapter import Adapter

    if getattr(Adapter, '_poke_patched', False):
        return
    # payload_to_event 是 staticmethod，兼容两种取法
    descriptor = Adapter.__dict__.get('payload_to_event')
    original = descriptor.__func__ if isinstance(descriptor, staticmethod) else descriptor

    def payload_to_event(payload):  # type: ignore[no-untyped-def]
        data = payload.data if isinstance(payload.data, dict) else {}
        # 只处理官方 QQ 的 INTERACTION_CREATE + type=20
        if str(payload.type) == EventType.INTERACTION_CREATE.value and data.get('type') == POKE_INTERACTION_TYPE:
            try:
                return QqPokeEvent(event_id=payload.id, **data)
            except Exception as error:  # 兜底：退回原逻辑，不阻断其它事件
                logger.debug(f'[QqPoke] 构造戳一戳事件失败：{error}')
        return original(payload)

    Adapter.payload_to_event = staticmethod(payload_to_event)
    Adapter._poke_patched = True  # type: ignore[attr-defined]
    logger.info('[QqPoke] 已接管 QQ 官方 INTERACTION_CREATE(type=20)')


_patch_payload_to_event()


async def _is_poke(event: QqEvent) -> bool:
    """只认官方 QQ 的戳一戳事件。"""
    return isinstance(event, QqPokeEvent) and event.is_poke()


poke_matcher = on_notice(rule=Rule(_is_poke), priority=5, block=False)


@poke_matcher.handle()
async def _handle_poke(bot: QqBot, event: QqPokeEvent) -> None:
    """用触发事件的 event_id 被动回复一条冷知识。"""
    fact = random.choice(POKE_FACTS)
    if not event.group_openid:
        return
    try:
        # 被动回复用户应带 msg_id（文档规定），不是 event_id
        await bot.send_to_group(
            group_openid=event.group_openid,
            message=f'（被戳了一下）{fact}',
            msg_id=getattr(event, 'id', None) or None,
        )
    except Exception as error:
        logger.warning(f'[QqPoke] 回复戳一戳失败：{error}')
