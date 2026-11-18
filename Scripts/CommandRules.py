"""适配器无关的命令匹配工具。

问题背景
--------
UniBot 的插件大量使用 ``nonebot.on_command`` + ``nonebot.params.CommandArg``，
这两个都是**基于 OneBot 协议**的抽象：

- ``on_command`` 依赖 ``CommandArg`` 解析，官方 QQ 适配器的事件没有这个概念；
- 事件参数写死 ``GroupMessageEvent`` 时，官方 QQ 的事件类型不匹配，
  handler 会被静默跳过（日志只看得到 ``running complete``，既不报错也不发消息）。

本模块提供等价能力，但只依赖 ``nonebot.adapters.Event`` 基类：

- :func:`command_rule` —— 生成正则命令匹配规则（自动读取 command_start 前缀）；
- :func:`command_args` —— 从事件正文里取出命令后的参数文本；
- :func:`is_superuser` —— 跨适配器判断超管。

这两条链路（OneBot / QQ 官方）用同一份规则，行为保持一致。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from nonebot.adapters import Event
from nonebot.rule import Rule

from Scripts.Config import config

# 命令分隔符（与 OneBot 侧一致）
_SEPARATORS = r'[\s\u3000]*'


def _start_chars() -> str:
    """命令起始符集合；配置为空时退化为 ``/ . #``。"""
    starts = list(getattr(config, 'command_start', []) or ['/', '.', '#'])
    return ''.join(part for part in starts if part)


def build_pattern(names: Iterable[str], aliases: Iterable[str] = ()) -> re.Pattern[str]:
    """按命令名与别名生成匹配「前缀+命令名+参数」的正则。"""
    all_names = [name for name in [*names, *aliases] if name]
    # 长名优先，避免 `周课表` 被 `课表` 抢先匹配
    all_names.sort(key=len, reverse=True)
    head = '[' + re.escape(_start_chars()) + ']?'
    body = '|'.join(re.escape(name) for name in all_names)
    # 允许正文前面残留 @提及（OneBot 的 get_plaintext 可能保留 @ 段文本）
    return re.compile(
        rf'^\s*(?:@\S+\s*)*{head}(?P<cmd>{body}){_SEPARATORS}(?P<arg>.*)$',
        re.S | re.I,
    )


def command_rule(names: Iterable[str], aliases: Iterable[str] = ()) -> Rule:
    """生成命令匹配规则：任意适配器的事件都能命中。"""
    pattern = build_pattern(names, aliases)

    async def _checker(event: Event) -> bool:
        try:
            text = event.get_plaintext().strip()
        except Exception:
            return False
        return bool(text) and bool(pattern.match(text))

    return Rule(_checker)


def command_args(event: Event, names: Iterable[str], aliases: Iterable[str] = ()) -> str:
    """取出命令后面的参数文本（等价于 ``CommandArg().extract_plain_text()``）。"""
    try:
        text = event.get_plaintext().strip()
    except Exception:
        return ''
    matched = build_pattern(names, aliases).match(text)
    return (matched.group('arg') if matched else '').strip()


# ===== 事件字段兼容（OneBot 与官方 QQ 字段名不同）=====


def ev_user_id(event: Event) -> str:
    """取发送者 ID：OneBot 是 QQ 号，官方 QQ 是 openid。"""
    value = getattr(event, 'user_id', None)
    if value is not None:
        return str(value)
    author = getattr(event, 'author', None)
    for attr in ('member_openid', 'user_openid', 'id'):
        candidate = getattr(author, attr, None)
        if candidate:
            return str(candidate)
    try:
        getter = getattr(event, 'get_user_id', None)
        if callable(getter):
            return str(getter() or '')
    except Exception:
        pass
    return ''


def ev_group_id(event: Event) -> str:
    """取群 ID：OneBot 是数字群号，官方 QQ 是 group_openid。"""
    for attr in ('group_id', 'group_openid'):
        value = getattr(event, attr, None)
        if value is not None:
            return str(value)
    return ''


def ev_message_id(event: Event) -> str:
    """取消息 ID：用于引用回复。取不到返回空串。"""
    for attr in ('message_id', 'id'):
        value = getattr(event, attr, None)
        if value is not None:
            return str(value)
    message = getattr(event, 'message', None)
    inner = getattr(message, 'id', None) if message is not None else None
    if inner:
        return str(inner)
    return ''


def ev_time(event: Event) -> int:
    """取事件时间戳（秒）；取不到返回 0。"""
    value = getattr(event, 'time', None)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        from datetime import datetime

        stamp = getattr(event, 'timestamp', None)
        if stamp is not None:
            return int(datetime.fromisoformat(str(stamp)).timestamp())
    except Exception:
        pass
    import time as _t

    return int(_t.time())



def sender_name(event: Event, default: str = '群友') -> str:
    """取发送者昵称，兼容 OneBot 的 sender 与官方 QQ 的 author。"""
    # OneBot: event.sender.nickname / .card
    sender = getattr(event, 'sender', None)
    if sender is not None:
        for attr in ('card', 'nickname'):
            value = getattr(sender, attr, None)
            if value:
                return str(value)
    # 官方 QQ: event.author 里通常没有昵称，退化成 openid 片段
    author = getattr(event, 'author', None)
    if author is not None:
        for attr in ('nickname', 'username', 'member_openid', 'user_openid'):
            value = getattr(author, attr, None)
            if value:
                return str(value)[:12]
    try:
        getter = getattr(event, 'get_user_id', None)
        if callable(getter):
            value = getter()
            if value:
                return str(value)[:12]
    except Exception:
        pass
    return default


def is_superuser(event: Event) -> bool:
    """跨适配器判断超管。

    OneBot 事件的 ``get_user_id()`` 是真实 QQ 号，可直接比对 ``SUPERUSERS``；
    官方 QQ 给的是 ``user_openid``，无法与 QQ 号对应，因此对官方适配器
    退化为「群内放行、由命令自身控制」，避免误拦管理员。
    """
    module = type(event).__module__
    if module.startswith('nonebot.adapters.qq'):
        return True
    try:
        getter = getattr(event, 'get_user_id', None)
        user_id = str(getter()) if callable(getter) else ''
    except Exception:
        return False
    superusers = {str(uid) for uid in getattr(config, 'superusers', [])}
    return user_id in superusers


def superuser_rule() -> Rule:
    """超管专用规则。"""
    return Rule(lambda event: is_superuser(event))
