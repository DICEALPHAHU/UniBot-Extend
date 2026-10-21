"""Minecraft 玩家卡。

触发方式：群里发送「.player <玩家名或 UUID>」，例如「.player Notch」。

数据来源：
- 用户名 -> UUID：``api.mojang.com``
- 皮肤 / 披风贴图：``sessionserver.mojang.com`` 的 profile 接口

渲染：纯 Pillow 从 64x64 皮肤贴图按固定 UV 切出各个面，拼成正面 / 背面，
再贴上披风贴图。不依赖任何第三方渲染服务，也不需要浏览器或扩展渲染器。

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

# Mojang 接口
MOJANG_USER_URL = 'https://api.mojang.com/users/profiles/minecraft/{name}'
MOJANG_PROFILE_URL = 'https://api.mojang.com/user/profile/{uuid}'
MOJANG_SESSION_URL = 'https://sessionserver.mojang.com/session/minecraft/profile/{uuid}'

MOJANG_TIMEOUT = 12
MOJANG_USER_AGENT = 'UniBot-Extend player card (https://github.com/DICEALPHAHU/UniBot-Extend)'

# 皮肤体积上限（正版皮肤固定 64x64，限制一下避免异常数据）
SKIN_MAX_BYTES = 512 * 1024
# 单张大图的渲染倍率：皮肤 16x32 -> 128x256
RENDER_SCALE = 8
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


# 64x64 皮肤贴图里各面的坐标：(left, top, right, bottom)
# 键名：front/back 是正面与背面，ofront/oback 是对应的 overlay（第二层皮肤）
CLASSIC_FACES = {
    'head': {'front': (8, 8, 16, 16), 'back': (24, 8, 32, 16), 'ofront': (40, 8, 48, 16), 'oback': (56, 8, 64, 16)},
    'body': {'front': (20, 20, 28, 32), 'back': (32, 20, 40, 32), 'ofront': (20, 36, 28, 48), 'oback': (32, 36, 40, 48)},
    'right_arm': {
        'front': (44, 20, 48, 32), 'back': (52, 20, 56, 32),
        'ofront': (44, 36, 48, 48), 'oback': (52, 36, 56, 48),
    },
    'left_arm': {
        'front': (36, 52, 40, 64), 'back': (44, 52, 48, 64),
        'ofront': (52, 52, 56, 64), 'oback': (60, 52, 64, 64),
    },
    'right_leg': {
        'front': (4, 20, 8, 32), 'back': (12, 20, 16, 32),
        'ofront': (4, 36, 8, 48), 'oback': (12, 36, 16, 48),
    },
    'left_leg': {
        'front': (20, 52, 24, 64), 'back': (28, 52, 32, 64),
        'ofront': (4, 52, 8, 64), 'oback': (12, 52, 16, 64),
    },
}

# Alex（slim）模型手臂只有 3 像素宽
SLIM_ARM_WIDTH = 3

# Alex（slim）模型手臂只有 3 像素宽，正面/背面的起始 x 与经典模型不同
SLIM_FACES = {
    **CLASSIC_FACES,
    'right_arm': {
        'front': (44, 20, 47, 32), 'back': (51, 20, 54, 32),
        'ofront': (44, 36, 47, 48), 'oback': (51, 36, 54, 48),
    },
    'left_arm': {
        'front': (36, 52, 39, 64), 'back': (43, 52, 46, 64),
        'ofront': (52, 52, 55, 64), 'oback': (59, 52, 62, 64),
    },
}

# 正面/背面各自的拼装顺序：(部件, 面, overlay 面, x, y)
FRONT_LAYOUT = [
    ('right_leg', 'front', 'ofront', 4, 20),
    ('left_leg', 'front', 'ofront', 8, 20),
    ('body', 'front', 'ofront', 4, 8),
    ('right_arm', 'front', 'ofront', 0, 8),
    ('left_arm', 'front', 'ofront', 12, 8),
]
BACK_LAYOUT = [
    ('left_leg', 'back', 'oback', 4, 20),
    ('right_leg', 'back', 'oback', 8, 20),
    ('body', 'back', 'oback', 4, 8),
    ('left_arm', 'back', 'oback', 12, 8),
    ('right_arm', 'back', 'oback', 0, 8),
]
FRONT_HEAD = ('head', 'front', 'ofront', 4, 0)
BACK_HEAD = ('head', 'back', 'oback', 4, 0)


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


def _face_table(model: str) -> dict:
    """按模型返回切面坐标表。"""
    return SLIM_FACES if model == 'slim' else CLASSIC_FACES


def render_view(skin: Image.Image, model: str, back: bool = False) -> Image.Image:
    """把皮肤贴图渲染成 16x32 的正面或背面视图。"""
    faces = _face_table(model)
    # 1.8 之前的 64x32 老皮肤：没有 overlay 层，左右肢体共用右半边的贴图（镜像使用）。
    # 这类皮肤未使用的区域常被填成不透明黑色，必须跳过 overlay，否则整个模型会被涂黑。
    legacy = skin.height < 64
    layout = (BACK_LAYOUT + [BACK_HEAD]) if back else (FRONT_LAYOUT + [FRONT_HEAD])
    # slim 手臂只有 3 像素宽，贴身体的那一侧要收进来 1 像素，否则会跟躯干之间留出空隙
    arm_offset = (4 - SLIM_ARM_WIDTH) if model == 'slim' else 0
    canvas = Image.new('RGBA', (16, 32), (0, 0, 0, 0))
    overlays = []
    for part, face, overlay_face, x, y in layout:
        if arm_offset and part.endswith('_arm') and x == 0:
            x += arm_offset
        source = part
        if legacy and part.startswith('left_'):
            source = 'right_' + part.split('_', 1)[1]
        piece = skin.crop(faces[source][face])
        if source != part:
            piece = piece.transpose(Image.FLIP_LEFT_RIGHT)
        canvas.alpha_composite(piece, (x, y))
        if not legacy:
            overlays.append((skin.crop(faces[part][overlay_face]), x, y))
    for overlay, x, y in overlays:
        canvas.alpha_composite(overlay, (x, y))
    return canvas.resize((16 * RENDER_SCALE, 32 * RENDER_SCALE), Image.Resampling.NEAREST)


def render_cape(cape: Image.Image) -> Image.Image:
    """披风贴图正面在 (1,1)-(11,17)。"""
    return cape.crop((1, 1, 11, 17)).resize((10 * RENDER_SCALE, 16 * RENDER_SCALE), Image.Resampling.NEAREST)


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

    for x, image, label in ((36, front, '皮肤正面'), (300, back, '皮肤背面')):
        y = 120
        draw.rounded_rectangle((x - 12, y - 12, x + image.width + 12, y + image.height + 32), radius=14,
                               fill=(24, 25, 28, 255))
        card.alpha_composite(image, (x, y))
        draw.text((x, y + image.height + 8), label, font=small_font, fill=(150, 152, 156, 255))

    if cape is not None:
        cape_image = render_cape(cape)
        x = 564
        draw.rounded_rectangle((x - 12, 108, x + cape_image.width + 12, 120 + cape_image.height + 32), radius=14,
                               fill=(24, 25, 28, 255))
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
    description='查询正版 Java 玩家的皮肤与披风，渲染正面/背面卡片（数据来源：Mojang）',
    usage='.player <玩家名或 UUID>\n示例：.player Notch',
    extra={'author': 'DICEALPHAHU', 'version': '1.0'},
)

register_help_commands([
    {
        'name': 'player',
        'description': '查询正版玩家的皮肤与披风，生成正/背面卡片',
        'usage': '/player <玩家名或 UUID>',
    },
])
