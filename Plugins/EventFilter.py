"""过滤用不到的 Minecraft 指令事件。

MC 服务器会把玩家执行的每条指令都推给机器人（``PlayerCommandEvent``）。
实测有玩家连续执行指令时一分钟能刷上千条，每条都会被适配器解析、打日志、
再走一遍 matcher 链，把事件循环和 WebUI 一起拖死（表现为机器人"没反应"、
CPU 100%、WebUI 打不开）。

UniBot 本体与现有插件都不消费这个事件，所以这里做两层拦截：

1. 在适配器层直接丢弃 —— 打补丁让 ``Adapter.json_to_event`` 对这类事件返回
   ``None``，事件根本不会进入 NoneBot 的事件链（零开销）；
2. 兜底 matcher —— 万一补丁失效，最高优先级 matcher 也会把它拦下来。
"""

from nonebot import on_message
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name='MC 指令事件过滤',
    description='丢弃玩家指令事件，避免事件洪水拖死事件循环（UniBot 不使用该事件）',
    usage='自动生效，无指令',
    extra={'author': 'DICEALPHAHU', 'version': '1.1'},
)

# 需要丢弃的事件名；以后如果发现别的事件也在刷，加到这里即可
DROPPED_EVENTS = {'PlayerCommandEvent'}

try:
    from nonebot.adapters.minecraft import PlayerCommandEvent
except ImportError:
    PlayerCommandEvent = None


def _install_adapter_patch() -> bool:
    """在适配器层丢弃指定事件，返回是否成功。"""
    try:
        from nonebot.adapters.minecraft import Adapter as MinecraftAdapter

        original = MinecraftAdapter.json_to_event.__func__

        @classmethod
        def patched(cls, json_data, self_id=None):
            if isinstance(json_data, dict) and json_data.get('event_name') in DROPPED_EVENTS:
                return None
            return original(cls, json_data, self_id)

        MinecraftAdapter.json_to_event = patched
        return True
    except Exception:
        return False


PATCHED = _install_adapter_patch()


if PlayerCommandEvent is not None:
    # 兜底：priority=0 + block=True，抢在 Collector / Events 之前拦下来
    drop_player_command = on_message(
        priority=0,
        block=True,
        rule=lambda event: isinstance(event, PlayerCommandEvent),
    )

    @drop_player_command.handle()
    async def _drop_player_command():
        """静默丢弃，不转发也不记录指令内容。"""
        return
