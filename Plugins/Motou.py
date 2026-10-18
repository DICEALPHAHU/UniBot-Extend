"""
群内「摸摸」表情包小功能。

触发方式：群里发送包含「摸摸」并 @ 他人的消息。
"""

from httpx import AsyncClient
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from .Config import plugin_config
from .HelpEntry import register_help_commands

# 摸摸表情包接口
MOTOU_API_URL = 'https://uapis.cn/api/v1/image/motou'


async def motou_check(event: GroupMessageEvent) -> bool:
    """匹配规则：消息包含「摸摸」并且有 @ 人。"""
    text = event.message.extract_plain_text()
    has_motou = '摸摸' in text
    has_at = any(seg.type == 'at' for seg in event.message)
    return has_motou and has_at


# 注册监听，无指令前缀，纯规则匹配
# priority=0：必须小于 Scripts.Plugins.Events 的 priority=1（它以 block=True 截断事件）。
motou_cmd = on_message(rule=Rule(motou_check), priority=0, block=True)


async def fetch_motou_image(target_qq: str) -> bytes | None:
    """
    请求表情包接口并返回图片字节，只取数据、不发送消息。

    这里不能出现 matcher.finish()：它依赖抛出 FinishedException 中断 handler，
    而该异常是 Exception 的子类，被本函数的 except 捕获后会误报成请求失败。
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0 Safari/537.36',
        'Authorization': f'Bearer {plugin_config.uapipro_token}',
    }
    params = {'qq': target_qq}

    async with AsyncClient(timeout=10) as client:
        response = await client.get(MOTOU_API_URL, headers=headers, params=params)
        if response.status_code != 200:
            return None
        return response.content


@motou_cmd.handle()
async def handle_motou(event: GroupMessageEvent):
    # 提取被 @ 的第一个 QQ
    target_qq = next(
        (seg.data.get('qq') for seg in event.message if seg.type == 'at'),
        None,
    )
    if not target_qq:
        return

    try:
        gif_data = await fetch_motou_image(str(target_qq))
    except Exception:
        return

    if not gif_data:
        return

    await motou_cmd.finish(MessageSegment.image(gif_data))


__plugin_meta__ = PluginMetadata(
    name='摸摸头',
    description='发送「摸摸」并 @ 某人，回一张摸摸表情包（数据来源：uapis.cn）',
    usage='摸摸 @某人',
    extra={'author': 'DICEALPHAHU', 'version': '1.0'},
)

register_help_commands([
    {
        'name': 'motou',
        'description': '摸摸表情包',
        'usage': '摸摸 @群友',
    },
])
