"""群组白名单规则。

同时兼容两种来源：

- **OneBot（NapCat）**：``event.group_id`` 是数字群号，配置里写成 ``qq_client:<群号>``。
- **QQ 官方机器人**：``event.group_id`` 是 ``group_openid``（一串大写十六进制），
  配置里写成 ``qq:<openid>``；私聊事件没有群 ID，按 scope 前缀 ``qq_private`` 放行。

私聊无法用群白名单表达，因此单独用 ``allow_private`` 控制：默认放行超管的私聊，
其余私聊走白名单。这样官方机器人上线后不会因为陌生人私聊而被滥用。
"""

from nonebot.adapters import Event
from nonebot.rule import Rule

from .Config import config

# 官方 QQ 适配器的 scope 前缀（onebot 侧仍沿用 qq_client）
OFFICIAL_SCOPE = 'qq_api'
ONEBOT_SCOPE = 'qq_client'


def _is_official(event: Event) -> bool:
    """事件是否来自 QQ 官方机器人适配器（模块名为 nonebot.adapters.qq）。"""
    return type(event).__module__.startswith('nonebot.adapters.qq')


def _group_key(event: Event) -> str | None:
    """把事件转成配置里的群标识；非群消息返回 None。"""
    try:
        group_id = getattr(event, 'group_id', None)
    except Exception:
        return None
    if group_id is None:
        # 官方适配器部分事件用 group_openid 字段名
        group_id = getattr(event, 'group_openid', None)
    if group_id is None:
        return None
    scope = OFFICIAL_SCOPE if _is_official(event) else ONEBOT_SCOPE
    return f'{scope}:{group_id}'


def _private_user(event: Event) -> str | None:
    """私聊事件的用户标识；群消息或取不到时返回 None（任何异常都不外抛）。"""
    try:
        if _group_key(event) is not None:
            return None
        getter = getattr(event, 'get_user_id', None)
        return getter() if callable(getter) else None
    except Exception:
        return None


def is_message_group(event: Event) -> bool:
    key = _group_key(event)
    if key is None:
        return _private_allowed(event)
    return key in config.message_groups


def is_command_group(event: Event) -> bool:
    key = _group_key(event)
    if key is None:
        return _private_allowed(event)
    return key in config.command_groups


def _private_allowed(event: Event) -> bool:
    """私聊放行策略。

    官方机器人的私聊事件只有 ``user_openid``（如 ``89361C50...``），**不是 QQ 号**，
    无法直接和 ``SUPERUSERS`` 比对。因此：

    1. 白名单里出现 ``qq_private`` → 全部放行；
    2. 官方私聊事件 → 放行（官方已按好友/沙箱白名单分层，陌生人拉不到）；
    3. OneBot 私聊 → 只放超管（那里拿到的就是真实 QQ 号）。
    """
    try:
        groups = list(config.command_groups) + list(config.message_groups)
        if 'qq_private' in groups:
            return True
        if _is_official(event):
            # 官方私聊：能发进来的只有机器人好友或开发体验用户，平台侧已做过滤
            return True
        user_id = _private_user(event)
        if user_id is None:
            return False
        return str(user_id) in {str(uid) for uid in config.superusers}
    except Exception:
        # 规则层任何异常都不应阻断事件分发，退化为不放行
        return False


message_group_rule = Rule(is_message_group)
command_group_rule = Rule(is_command_group)
