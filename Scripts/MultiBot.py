"""多适配器共存下的发送修正（全局补丁）。

解决三件事
----------
1. **选错 bot**：OneBot 与 QQ 官方同时在线且同群时，``Matcher.send`` 依赖
   ``current_bot``，可能把消息发到另一条链路（``ActionFailed: result 120``）。
2. **消息类型不兼容**：插件常用 OneBot 的 ``Message`` / ``MessageSegment``，
   官方 QQ 适配器不认，报 ``validation error for Component``。
3. **UniMessage 未导出**：uniseg 的 ``UniMessage`` 需 ``await export(bot)``
   才能变成目标适配器原生消息段。

策略
----
按事件来源挑 bot；发送前把消息统一转成目标适配器能接受的形式；
失败自动切换 bot 重试。单适配器场景行为与原来一致。
"""

from __future__ import annotations

from typing import Any

from nonebot.log import logger
from nonebot.matcher import Matcher

_PATCHED = False


def _all_bots() -> list[Any]:
    try:
        import nonebot

        return list(nonebot.get_bots().values())
    except Exception:
        return []


def _prefer(event: Any, bots: list[Any]) -> list[Any]:
    """把与事件同适配器的 bot 排在最前面。"""
    if event is None:
        return bots
    module = type(event).__module__
    key = 'nonebot.adapters.qq' if module.startswith('nonebot.adapters.qq') else 'nonebot.adapters.onebot'
    same = [b for b in bots if type(b).__module__.startswith(key)]
    other = [b for b in bots if not type(b).__module__.startswith(key)]
    return same + other


async def _adapt(message: Any, bot: Any, event: Any) -> Any:
    """把消息转成 bot 能接受的形式。

    - ``UniMessage`` → ``await export(bot)``
    - 官方 QQ bot + OneBot ``Message``/``MessageSegment`` → 逐段转换
    - 其余原样返回
    """
    # 1) uniseg 封装：导出成原生
    export = getattr(message, 'export', None)
    if callable(export):
        try:
            return await export(bot)
        except Exception:
            pass

    bot_module = type(bot).__module__
    target_is_qq = bot_module.startswith('nonebot.adapters.qq')

    # 2) 官方 QQ 且拿到的是 OneBot 消息对象：转成字符串或 QQ 消息段
    if target_is_qq:
        src_module = type(message).__module__
        if src_module.startswith('nonebot.adapters.onebot'):
            return _onebot_to_qq(message)
    return message


def _onebot_to_qq(message: Any) -> Any:
    """把 OneBot 的 Message/MessageSegment 转成官方 QQ 可接受的形式。

    官方 QQ 的富媒体只认 ``file_image`` / ``file_video`` 段（需 Path 或 bytes），
    因此图片段要把 ``data['content']`` 的字节取出落盘后再包装；纯文本直接返回
    字符串。无法识别的段降级为其文本，绝不返回空消息。
    """
    import tempfile
    from pathlib import Path as _P

    try:
        from nonebot.adapters.qq import MessageSegment as QqSegment
        from nonebot.adapters.qq import Message as QqMessage
    except Exception:
        return str(message)

    def convert_segment(seg: Any) -> Any:
        seg_type = getattr(seg, 'type', '')
        data = getattr(seg, 'data', {}) or {}
        if seg_type == 'text':
            return str(data.get('text') or '')
        if seg_type in ('image', 'file_image'):
            import base64 as _b64

            def _write(data_bytes: bytes) -> Any:
                """把图片字节落盘并包装成 QQ 的 file_image 段。"""
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as handle:
                        handle.write(data_bytes)
                        tmp = _P(handle.name)
                    return QqSegment.file_image(tmp, tmp.name)
                except Exception:
                    return ''

            # 1) 原始字节
            raw = data.get('content')
            if isinstance(raw, (bytes, bytearray)):
                return _write(bytes(raw))

            # 2) base64 URI / base64: 前缀（OneBot 的 MessageSegment.image(bytes) 会这样）
            candidate = data.get('file') or data.get('path') or ''
            text = str(candidate)
            if text.startswith('base64://'):
                try:
                    return _write(_b64.b64decode(text[len('base64://'):]))
                except Exception:
                    return ''
            if text.startswith('base64:'):
                try:
                    return _write(_b64.b64decode(text[len('base64:'):]))
                except Exception:
                    return ''

            # 3) 本地路径
            if text:
                try:
                    if _P(text).exists():
                        return QqSegment.file_image(_P(text), _P(text).name)
                except (OSError, ValueError):
                    return ''
            return ''
        return data.get('text', '')

    # 单段
    if hasattr(message, 'type') and hasattr(message, 'data'):
        return convert_segment(message)

    # 多段：分别处理，图片单独收集（官方 QQ 一条消息只能带一个媒体）
    if hasattr(message, '__iter__'):
        parts: list[Any] = []
        for seg in message:
            converted = convert_segment(seg)
            if isinstance(converted, str):
                if converted:
                    parts.append(converted)
            else:
                parts.append(converted)
        if not parts:
            return ''
        if len(parts) == 1:
            return parts[0]
        # 多段：文本合并成一条，媒体取第一个单独返回
        texts = [p for p in parts if isinstance(p, str)]
        medias = [p for p in parts if not isinstance(p, str)]
        if medias:
            result = QqMessage()
            merged = '\n'.join(str(t) for t in texts)
            if merged:
                result += QqSegment.text(merged)
            for media in medias[:1]:
                result += media
            return result
        return '\n'.join(texts)
    return str(message)


def _split_payloads(message: Any, bot: Any) -> list[Any] | None:
    """图文混排拆成多条；非混排返回 None 走常规单条路径。

    官方 QQ（nonebot.adapters.qq）单条消息只带一个媒体，图片+长文本一起发会报
    ``消息content字段不能为空`` 或校验失败，因此拆成「先图后文」两条。
    """
    bot_module = type(bot).__module__
    if not bot_module.startswith('nonebot.adapters.qq'):
        return None
    src_module = type(message).__module__
    if not src_module.startswith('nonebot.adapters.onebot'):
        return None

    segs = list(message) if hasattr(message, '__iter__') else [message]
    texts: list[str] = []
    medias: list[Any] = []
    for seg in segs:
        seg_type = getattr(seg, 'type', '')
        data = getattr(seg, 'data', {}) or {}
        if seg_type == 'text':
            value = data.get('text')
            if value:
                texts.append(str(value))
        elif seg_type in ('image', 'file_image'):
            converted = _onebot_to_qq(seg)
            if converted and not isinstance(converted, str):
                medias.append(converted)
        elif data.get('text'):
            texts.append(str(data['text']))

    if not medias:
        return None  # 纯文本，走常规路径

    out: list[Any] = []
    try:
        from nonebot.adapters.qq import Message as QqMessage
        from nonebot.adapters.qq import MessageSegment as QqSegment

        for media in medias[:1]:
            out.append(media)
        merged = '\n'.join(str(t) for t in texts if t).strip()
        if merged:
            out.append(QqMessage(QqSegment.text(merged)))
    except Exception:
        return None
    return out or None


def apply() -> None:
    """安装补丁（幂等）。"""
    global _PATCHED
    if _PATCHED:
        return

    original_send = Matcher.send.__func__  # type: ignore[attr-defined]

    async def send(cls, message, **kwargs):  # type: ignore[no-untyped-def]
        from nonebot.internal.matcher.matcher import current_bot, current_event

        try:
            event = current_event.get()
        except Exception:
            event = None
        try:
            fallback = current_bot.get()
        except Exception:
            fallback = None

        bots = _all_bots() or ([fallback] if fallback else [])
        if not bots:
            return await original_send(cls, message, **kwargs)
        candidates = _prefer(event, bots)

        last_error: Exception | None = None
        for index, bot in enumerate(candidates):
            try:
                # 官方 QQ 一条消息只允许一个媒体，图文混排拆成多条依次发
                payloads = _split_payloads(message, bot)
                if payloads is None:
                    payload = await _adapt(message, bot, event)
                    payloads = [payload]
                result = None
                for item in payloads:
                    if item is None:
                        continue
                    if event is None:
                        result = await bot.send(message=item, **kwargs)
                    else:
                        result = await bot.send(event=event, message=item, **kwargs)
                return result
            except Exception as error:
                last_error = error
                logger.warning(
                    f'[MultiBot] {type(bot).__name__} 发送失败（第 {index + 1}/{len(candidates)}）：'
                    f'{str(error)[:80]}'
                )
                continue

        if last_error is not None:
            raise last_error
        return await original_send(cls, message, **kwargs)

    Matcher.send = classmethod(send)
    _PATCHED = True
    logger.info('[MultiBot] 已接管 Matcher.send（选 bot + 消息类型转换 + 失败重试）')
