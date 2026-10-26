"""Minecraft 玩家卡。

触发方式：群里发送「.player <玩家名或 UUID>」，例如「.player Notch」。

数据来源：
- 用户名 -> UUID：``api.mojang.com``
- 皮肤 / 披风贴图：``sessionserver.mojang.com`` 的 profile 接口

渲染：纯 Pillow + numpy 的三维渲染（见 ``SkinRender``）。按模型的长宽高把皮肤
切成六个面，用透视相机投影后按深度排序绘制，第二层皮肤（帽子、外套、袖子）
会外扩出真实的厚度，不再是两层平面贴在一起。不依赖任何第三方渲染服务。

仅支持正版 Java 账号；离线服玩家不在 Mojang 记录里，会给出提示。
"""

import asyncio
import base64
import io
import json
import time
from pathlib import Path

from httpx import AsyncClient
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from PIL import Image, ImageDraw, ImageFont

from .Config import plugin_config
from .HelpEntry import register_help_commands
from .SkinRender import render_character

# Mojang 接口
MOJANG_USER_URL = 'https://api.mojang.com/users/profiles/minecraft/{name}'
MOJANG_PROFILE_URL = 'https://api.mojang.com/user/profile/{uuid}'
MOJANG_SESSION_URL = 'https://sessionserver.mojang.com/session/minecraft/profile/{uuid}'

MOJANG_TIMEOUT = 12
MOJANG_USER_AGENT = 'UniBot-Extend player card (https://github.com/DICEALPHAHU/UniBot-Extend)'

# 皮肤体积上限（正版皮肤固定 64x64，限制一下避免异常数据）
SKIN_MAX_BYTES = 512 * 1024
# 卡片里人物立绘的高度（像素）
CARD_SPRITE_HEIGHT = 250
# 披风贴图的放大倍率：10x16 -> 80x128
CAPE_SCALE = 8
# 卡片缓存时长（秒）与条目上限
CARD_CACHE_TTL = 600
CARD_CACHE_MAX = 30

# 字体下载地址（按顺序尝试）。第一个是 Noto Sans SC 的完整 TTF（约 10MB）。
DEFAULT_FONT_URLS = (
    'https://fonts.gstatic.com/s/notosanssc/v41/k3kCo84MPvpLmixcA63oeAL7Iqp5IZJF9bmaG-3FnYw.ttf',
    'https://cdn.jsdelivr.net/gh/googlefonts/noto-cjk@main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf',
)
FONT_FILE_NAME = 'NotoSansSC.ttf'

# 字体缓存：字号 -> 字体对象
_FONT_CACHE: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
# 卡片缓存：玩家键 -> (写入时间, PNG 字节)
_CARD_CACHE: dict[str, tuple[float, bytes]] = {}

player_cmd = on_command('player', priority=0, block=True)


class PlayerNotFound(Exception):
    """玩家不存在（非正版玩家或名字拼错）。"""


def _cache_get(key: str):
    item = _CARD_CACHE.get(key)
    if item is None:
        return None
    created_at, data = item
    if time.monotonic() - created_at > CARD_CACHE_TTL:
        _CARD_CACHE.pop(key, None)
        return None
    return data


def _cache_put(key: str, data: bytes) -> None:
    if len(_CARD_CACHE) >= CARD_CACHE_MAX:
        for old in sorted(_CARD_CACHE, key=lambda k: _CARD_CACHE[k][0])[: CARD_CACHE_MAX // 2]:
            _CARD_CACHE.pop(old, None)
    _CARD_CACHE[key] = (time.monotonic(), data)


async def _get_json(client: AsyncClient, url: str) -> dict | None:
    """请求 JSON；404 / 204 返回 None。"""
    response = await client.get(url, headers={'User-Agent': MOJANG_USER_AGENT})
    if response.status_code in (204, 404):
        return None
    if response.status_code != 200:
        raise RuntimeError(f'Mojang 接口返回 HTTP {response.status_code}')
    if not response.content:
        return None
    return response.json()


async def resolve_player(client: AsyncClient, target: str) -> tuple[str, str]:
    """把玩家名或 UUID 解析成 (uuid, 名字)。"""
    raw = target.strip().replace('-', '')
    if len(raw) == 32 and all(char in '0123456789abcdefABCDEF' for char in raw):
        data = await _get_json(client, MOJANG_PROFILE_URL.format(uuid=raw))
        if not data:
            raise PlayerNotFound(target)
        return data['id'], data.get('name') or data['id']

    data = await _get_json(client, MOJANG_USER_URL.format(name=target.strip()))
    if not data:
        raise PlayerNotFound(target)
    return data['id'], data.get('name') or target


async def fetch_textures(client: AsyncClient, uuid: str) -> dict:
    """取皮肤的 textures 信息（皮肤 / 披风地址、经典或 slim 模型）。"""
    data = await _get_json(client, MOJANG_SESSION_URL.format(uuid=uuid))
    if not data:
        raise PlayerNotFound(uuid)
    try:
        value = data['properties'][0]['value']
        textures = json.loads(base64.b64decode(value))['textures']
    except (KeyError, IndexError, ValueError) as error:
        raise RuntimeError('皮肤数据解析失败') from error
    return textures


async def download_image(client: AsyncClient, url: str, max_bytes: int = SKIN_MAX_BYTES) -> Image.Image | None:
    """下载贴图并转成 RGBA 图片；失败或超限返回 None。"""
    response = await client.get(url, headers={'User-Agent': MOJANG_USER_AGENT}, follow_redirects=True)
    if response.status_code != 200:
        return None
    content = response.content
    if not content or len(content) > max_bytes:
        return None
    try:
        return Image.open(io.BytesIO(content)).convert('RGBA')
    except Exception:
        return None


def render_view(skin: Image.Image, model: str, back: bool = False) -> Image.Image:
    """渲染正面或背面的立体立绘，并缩放到卡片里需要的高度。"""
    image = render_character(skin, model=model, back=back)
    if image.height != CARD_SPRITE_HEIGHT:
        scale = CARD_SPRITE_HEIGHT / image.height
        image = image.resize(
            (max(1, round(image.width * scale)), CARD_SPRITE_HEIGHT),
            Image.Resampling.LANCZOS,
        )
    return image


def render_cape(cape: Image.Image) -> Image.Image:
    """披风贴图正面在 (1,1)-(11,17)。"""
    return cape.crop((1, 1, 11, 17)).resize((10 * CAPE_SCALE, 16 * CAPE_SCALE), Image.Resampling.NEAREST)


def _font(path: Path | None, size: int):
    """按字号加载字体；没有字体文件时退回 Pillow 内置位图字体。"""
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    try:
        font = ImageFont.truetype(str(path), size) if path else None
    except Exception:
        font = None
    if font is None:
        try:
            font = ImageFont.load_default(size)
        except TypeError:
            font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


async def ensure_font() -> Path | None:
    """确保字体文件存在；返回字体路径，没有可用字体时返回 None。"""
    configured = plugin_config.player_font_path.strip()
    if configured:
        path = Path(configured)
        if path.exists():
            return path

    default_path = Path('Data/PlayerCard') / FONT_FILE_NAME
    if default_path.exists() and default_path.stat().st_size > 1024 * 1024:
        return default_path

    if not plugin_config.player_font_auto_download:
        return None

    urls = [plugin_config.player_font_url.strip()] if plugin_config.player_font_url.strip() else list(DEFAULT_FONT_URLS)
    default_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncClient(timeout=60) as client:
        for url in urls:
            try:
                response = await client.get(url, headers={'User-Agent': MOJANG_USER_AGENT}, follow_redirects=True)
                if response.status_code != 200 or len(response.content) < 1024 * 1024:
                    continue
                default_path.write_bytes(response.content)
                return default_path
            except Exception:
                continue
    return None


def build_card(name: str, uuid: str, skin: Image.Image, cape: Image.Image | None, model: str, font_path: Path | None) -> bytes:
    """把正/背视图与披风拼成一张卡片，返回 PNG 字节。"""
    front = render_view(skin, model, back=False)
    back = render_view(skin, model, back=True)

    width, height = 780, 460
    card = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=22, fill=(32, 33, 36, 255))

    title_font = _font(font_path, 34)
    tag_font = _font(font_path, 19)
    small_font = _font(font_path, 16)

    draw.text((36, 26), name, font=title_font, fill=(245, 245, 245, 255))
    subtitle = f'Minecraft 正版玩家 · {model}'
    draw.text((36, 74), subtitle, font=tag_font, fill=(150, 152, 156, 255))

    # 立绘高度固定，两张竖版面板并排；披风单独一格
    slot_x = 36
    slot_gap = 40
    slot_y = 112
    for image, label in ((front, '皮肤正面'), (back, '皮肤背面')):
        panel = (slot_x - 12, slot_y - 12, slot_x + image.width + 12, slot_y + image.height + 32)
        draw.rounded_rectangle(panel, radius=14, fill=(24, 25, 28, 255))
        card.alpha_composite(image, (slot_x, slot_y))
        draw.text((slot_x, slot_y + image.height + 8), label, font=small_font, fill=(150, 152, 156, 255))
        slot_x += image.width + slot_gap

    if cape is not None:
        cape_image = render_cape(cape)
        x = 564
        draw.rounded_rectangle(
            (x - 12, 108, x + cape_image.width + 12, 120 + cape_image.height + 32), radius=14, fill=(24, 25, 28, 255)
        )
        card.alpha_composite(cape_image, (x, 120))
        draw.text((x, 120 + cape_image.height + 8), '披风', font=small_font, fill=(150, 152, 156, 255))
    else:
        draw.text((564, 150), '无披风', font=tag_font, fill=(120, 122, 126, 255))

    draw.text((36, height - 44), f'UUID: {uuid}', font=small_font, fill=(120, 122, 126, 255))

    buffer = io.BytesIO()
    card.save(buffer, format='PNG')
    return buffer.getvalue()


async def build_player_card(target: str) -> bytes:
    """查询玩家并渲染卡片；结果缓存 10 分钟。"""
    cache_key = target.strip().lower()
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    async with AsyncClient(timeout=MOJANG_TIMEOUT) as client:
        uuid, name = await resolve_player(client, target)
        textures = await fetch_textures(client, uuid)
        skin_entry = textures.get('SKIN') or {}
        skin_url = skin_entry.get('url')
        if not skin_url:
            raise RuntimeError('该玩家没有公开皮肤')
        model = (skin_entry.get('metadata') or {}).get('model', 'classic')
        skin = await download_image(client, skin_url)
        cape = None
        cape_url = (textures.get('CAPE') or {}).get('url')
        if cape_url:
            cape = await download_image(client, cape_url, max_bytes=SKIN_MAX_BYTES)
    if skin is None:
        raise RuntimeError('皮肤贴图下载失败')

    font_path = await ensure_font()
    data = await asyncio.to_thread(build_card, name, uuid, skin, cape, model, font_path)
    _cache_put(cache_key, data)
    return data


@player_cmd.handle()
async def handle_player(args: Message = CommandArg()):
    target = args.extract_plain_text().strip()
    if not target:
        await player_cmd.finish('请输入玩家名或 UUID。示例：.player Notch')

    try:
        card = await build_player_card(target)
    except PlayerNotFound:
        await player_cmd.finish(
            f'没找到正版玩家「{target}」。\n'
            '本指令只查询正版 Java 账号，离线服玩家不在 Mojang 记录里。'
        )
        return
    except Exception as error:
        await player_cmd.finish(f'查询失败：{str(error)[:60]}')
        return

    await player_cmd.finish(MessageSegment.image(card))


__plugin_meta__ = PluginMetadata(
    name='Minecraft 玩家卡',
    description='查询正版 Java 玩家的皮肤与披风，渲染有厚度的正/背面卡片（数据来源：Mojang）',
    usage='.player <玩家名或 UUID>\n示例：.player Notch',
    extra={'author': 'vvvvdg', 'version': '1.1'},
)

register_help_commands([
    {
        'name': 'player',
        'description': '查询正版玩家的皮肤与披风，生成正/背面卡片',
        'usage': '/player <玩家名或 UUID>',
    },
])
