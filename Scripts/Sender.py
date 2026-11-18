"""适配器无关的消息发送层。

UniBot 现有插件大量直接使用 OneBot V11 的 ``bot.send_group_msg`` /
``GroupMessageEvent``，因此在 QQ 官方机器人（``nonebot.adapters.qq``）下会
因为事件类型不匹配而静默失效（匹配器跑完但不发消息）。

本模块提供一层薄封装，做到「同一份插件代码，两条链路都能跑」：

- **优先**走 ``nonebot_plugin_alconna`` 的 ``UniMessage``，它本身跨适配器；
- 拿不到 UniMessage 时退回各适配器原生 API（OneBot/QQ）的分支实现；
- 事件侧只依赖 ``nonebot.adapters.Event`` 基类 + 属性探测，不写死具体类型。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nonebot.adapters import Event
from nonebot.log import logger

try:  # alconna 可能未启用，降级不影响 OneBot 路径
    from nonebot_plugin_alconna.uniseg import Image, UniMessage
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    UniMessage = None  # type: ignore[assignment]


# ===== 事件探测（不写死适配器类型）=====


def is_official_qq(event: Event) -> bool:
    """事件是否来自 QQ 官方机器人适配器。"""
    return type(event).__module__.startswith('nonebot.adapters.qq')


def is_onebot(event: Event) -> bool:
    """事件是否来自 OneBot V11 适配器。"""
    return type(event).__module__.startswith('nonebot.adapters.onebot.v11')


def get_group_id(event: Event) -> str | None:
    """取群标识：OneBot 是数字群号，官方 QQ 是 group_openid。"""
    value = getattr(event, 'group_id', None) or getattr(event, 'group_openid', None)
    return str(value) if value is not None else None


def get_user_id(event: Event) -> str | None:
    """取发送者标识；取不到时返回 None（合成事件会抛异常，这里兜住）。"""
    try:
        getter = getattr(event, 'get_user_id', None)
        return str(getter()) if callable(getter) else None
    except Exception:
        return None


def is_private(event: Event) -> bool:
    """事件是否为私聊（C2C）。"""
    return type(event).__name__ in ('C2CMessageCreateEvent', 'PrivateMessageEvent')


def get_openid(event: Event) -> str | None:
    """取私聊对端的 openid；群消息返回 None。"""
    if not is_private(event):
        return None
    author = getattr(event, 'author', None)
    candidate = getattr(author, 'user_openid', None) or getattr(author, 'member_openid', None)
    if candidate:
        return str(candidate)
    try:
        getter = getattr(event, 'get_user_id', None)
        return str(getter()) if callable(getter) else None
    except Exception:
        return None


def get_msg_id(event: Event) -> str | None:
    """取消息 id：群/单聊被动回复应带 msg_id（文档：被动消息=携带 msg_id）。"""
    # 官方 QQ 消息事件里 id 是消息 ID，event_id 是事件 ID
    candidate = getattr(event, 'id', None)
    if candidate:
        return str(candidate)
    message = getattr(event, 'message', None)
    if message is not None:
        inner = getattr(message, 'id', None)
        if inner:
            return str(inner)
    return None


def get_event_id(event: Event) -> str | None:
    """取事件 ID，官方 QQ 的被动回复需要它。"""
    return getattr(event, 'event_id', None) or getattr(event, 'id', None)


# ===== 发送（按适配器分派，调用方无需关心）=====


async def send_group_text(bot: Any, event: Event, text: str) -> bool:
    """向事件所在群发送纯文本；成功返回 True。"""
    group_id = get_group_id(event)
    if group_id is None:
        logger.warning('[Sender] 事件没有群标识，无法发送')
        return False
    try:
        if is_official_qq(event):
            # 官方 QQ：带 event_id 走被动回复，避免「主动消息无权限」
            await bot.send_to_group(group_openid=group_id, message=text, msg_id=get_msg_id(event))
            return True
        # OneBot：保持原行为
        if hasattr(bot, 'send_group_msg'):
            await bot.send_group_msg(group_id=int(group_id), message=text)
        else:
            # bot 与 event 不匹配（跨适配器），跳过
            logger.warning('[Sender] bot 不是 OneBot 实例，跳过群文本发送')
            return False
        return True
    except Exception as error:
        logger.error(f'[Sender] 发送群文本失败：{error}')
        return False


async def send_group_image(bot: Any, event: Event, image: bytes | Path) -> bool:
    """向事件所在会话发送图片（bytes 或本地路径）。"""
    group_id = get_group_id(event)
    if group_id is None and not (is_official_qq(event) and is_private(event)):
        return False

    if is_official_qq(event):
        try:
            data = image if isinstance(image, bytes) else Path(image).read_bytes()
            if is_private(event):
                openid = get_openid(event)
                if not openid:
                    return False
                return await _send_official_file(
                    bot, openid, data, get_event_id(event), private=True
                )
            return await _send_official_file(
                bot, group_id, data, get_event_id(event), target_msg_id=get_msg_id(event)
            )
        except Exception as error:
            logger.error(f'[Sender] 官方 QQ 发送图片失败：{error}')
            return False

    # OneBot：直接发
    try:
        if isinstance(image, bytes):
            from nonebot.adapters.onebot.v11 import MessageSegment

            await bot.send_group_msg(group_id=int(group_id), message=MessageSegment.image(image))
        else:
            from nonebot.adapters.onebot.v11 import MessageSegment

            await bot.send_group_msg(group_id=int(group_id), message=MessageSegment.image(Path(image).as_uri()))
        return True
    except Exception as error:
        logger.error(f'[Sender] OneBot 发送图片失败：{error}')
        return False


async def send_group_video(bot: Any, event: Event, video: Path) -> bool:
    """向事件所在会话发送视频文件（群聊走群接口，私聊走 C2C）。"""
    group_id = get_group_id(event)
    # 私聊没有 group_id，交给下面的 official 分支用 openid 发送
    if group_id is None and not (is_official_qq(event) and is_private(event)):
        logger.warning('[Sender] 事件既无群号也非私聊，无法发送视频')
        return False

    if is_official_qq(event):
        try:
            data = Path(video).read_bytes()
            if is_private(event):
                openid = get_openid(event)
                if not openid:
                    logger.warning('[Sender] 私聊取不到 openid')
                    return False
                return await _send_official_file(
                    bot, openid, data, get_event_id(event),
                    is_video=True, file_name=Path(video).name, private=True,
                )
            return await _send_official_file(
                bot, group_id, data, get_event_id(event),
                is_video=True, file_name=Path(video).name,
                target_msg_id=get_msg_id(event),
            )
        except Exception as error:
            logger.error(f'[Sender] 官方 QQ 发送视频失败：{error}')
            return False

    # OneBot：沿用原有的群文件上传
    try:
        if is_onebot(event):
            await bot.call_api(
                'upload_group_file',
                group_id=int(group_id),
                file=str(video),
                name=Path(video).name,
            )
            return True
        await bot.send_group_msg(group_id=int(group_id), message=str(video))
        return True
    except Exception as error:
        logger.error(f'[Sender] OneBot 发送视频失败：{error}')
        return False


def _usable_event_id(event_id: str | None) -> str | None:
    """过滤掉官方 QQ 不允许回复的事件类型。

    ``GROUP_MESSAGE_CREATE``（全量群消息）不能用于回复，只有
    ``GROUP_AT_MESSAGE_CREATE`` 与 ``C2C_MESSAGE_CREATE`` 可以。
    """
    if not event_id:
        return None
    prefix = event_id.split(':', 1)[0]
    if prefix in ('GROUP_AT_MESSAGE_CREATE', 'C2C_MESSAGE_CREATE', 'AT_MESSAGE_CREATE', 'DIRECT_MESSAGE_CREATE'):
        return event_id
    return None


async def _send_official_file(
    bot: Any,
    target_openid: str,
    data: bytes,
    event_id: str | None,
    *,
    is_video: bool = False,
    file_name: str = '',
    private: bool = False,
    target_msg_id: str | None = None,
) -> bool:
    """官方 QQ 的文件发送。

    关键点（踩过的坑）：

    ``MessageSegment.file_video/file_image`` 的**第一个参数是文件内容**，
    不是 ``post_group_upload`` 返回的 ``file_info``。把 ``file_info``（一段 base64
    字符串）当内容传进去，QQ 会当成损坏文件并报 ``850019 富媒体文件格式不支持``。

    正确做法是把 ``Path`` 直接交给消息段，由适配器内部完成分片上传。
    """
    from nonebot.adapters.qq import MessageSegment as QqSegment

    name = file_name or ('video.mp4' if is_video else 'image.png')
    # 写到临时文件后传 Path（适配器的 file_video/file_image 支持 Path）
    import tempfile

    suffix = Path(name).suffix or ('.mp4' if is_video else '.png')
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(data)
            tmp_path = Path(handle.name)
        segment = (
            QqSegment.file_video(tmp_path, name) if is_video else QqSegment.file_image(tmp_path, name)
        )
        if private:
            # 私聊：C2C_MESSAGE_CREATE 允许回复，走 send_to_c2c
            # 实测：私聊主动消息有权限，带 event_id 反而被拒（40034027）
            await bot.send_to_c2c(openid=target_openid, message=segment)
        else:
            await bot.send_to_group(
                group_openid=target_openid, message=segment, msg_id=target_msg_id
            )
        return True
    except Exception as error:
        logger.error(f'[Sender] 官方 QQ 发送媒体失败：{error}')
        return False
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


async def send_private_text(bot: Any, event: Event, text: str) -> bool:
    """向事件发送者私聊发送纯文本。"""
    user_id = get_user_id(event)
    if user_id is None:
        return False
    try:
        if is_official_qq(event):
            await bot.send_to_c2c(openid=user_id, message=text)
            return True
        await bot.send_private_msg(user_id=int(user_id), message=text)
        return True
    except Exception as error:
        logger.error(f'[Sender] 发送私聊文本失败：{error}')
        return False

