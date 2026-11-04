"""
Minecraft 玩家统计卡。

触发方式：``/stats [玩家] [服务器]``
- 玩家省略时使用 ``/bound`` 绑定的角色
- 服务器省略时用配置里的默认服（列表第一个）

数据来源（都是只读、不碰 CoreProtect 的大库）：
- 在线 / 活跃 / 挂机：Plan (Player Analytics) 的本地 HTTP API
- 挖掘 / 放置 / 合成 / 击杀 / 死亡：服务器世界目录里的原版 ``stats/<uuid>.json``
  * 原版没有「放置方块」统计，这里用 ``minecraft:used`` 里**方块类物品**的求和近似，
    所以卡片上标了「≈」。水/岩浆桶这类液体放置不计入。

名字 -> UUID 走服务器的 ``usercache.json``；所有文件按 mtime 缓存，重复查询不重复读盘。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import random
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from httpx import AsyncClient
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from PIL import Image, ImageDraw, ImageFont

from Scripts import Globals
from Scripts.Logging import logger
from .HelpEntry import register_help_commands
from .SkinRender import FACE_RECTS, POSE_ITEMS, POSES, item_texture, render_character
from .Watermark import apply_watermark

CONFIG_PATH = Path('Data/MCStats.toml')

DEFAULT_CONFIG = '''# 玩家统计卡配置
#
# Plan (Player Analytics) 的本地接口；不用 Plan 的话把 plan_api 留空即可，
# 在线/活跃/挂机 三项会显示为 ——。
plan_api = "http://127.0.0.1:8804"
# Plan 里没有多服时留空；有代理结构时填服务器 UUID 或名字
plan_server = ""
# Plan 开始统计的日期，会显示在卡片底部（在线/活跃/挂机只从这个日期之后算）
plan_since = ""

# 每个服的统计目录（容器内路径）与 usercache.json（名字 -> UUID）
[servers."生存服"]
stats = "/mcstats/survival/stats"
usercache = "/mcstats/survival/usercache.json"
# SkinsRestorer 的皮肤库，用来给卡片渲染 3D 小人
skins_players = "/mcskins/players"
skins_library = "/mcskins/skins"

# 想加更多服就照抄一段，路径按实际挂载改：
# [servers."机械动力服"]
# stats = "/mcstats/create/stats"
# usercache = "/mcstats/create/usercache.json"
'''

# 原版统计里我们关心的段落
SECTION_MINED = 'minecraft:mined'
SECTION_USED = 'minecraft:used'
SECTION_CRAFTED = 'minecraft:crafted'
CUSTOM = 'minecraft:custom'
STAT_PLAY_TIME = 'minecraft:play_time'
STAT_MOB_KILLS = 'minecraft:mob_kills'
STAT_DEATHS = 'minecraft:deaths'

PLAN_TIMEOUT = 3.0
PLAN_CACHE_SECONDS = 60
STATS_CACHE_SECONDS = 60
BLOCK_SET_CACHE_SECONDS = 6 * 3600

FONT_CANDIDATES = (
    Path('Data/PlayerCard/NotoSansSC.ttf'),
    Path('Data/Kebiao/NotoSansSC.ttf'),
    Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
    Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
)

_FONT_CACHE: dict[int, Any] = {}


@dataclass(slots=True)
class Profile:
    """一个 Minecraft 服务器的统计来源。"""

    name: str
    stats_dir: Path | None = None
    usercache: Path | None = None
    skins_players: Path | None = None
    skins_library: Path | None = None


@dataclass(slots=True)
class Settings:
    """整份配置。"""

    plan_api: str = ''
    plan_server: str = ''
    plan_since: str = ''
    servers: dict[str, Profile] = field(default_factory=dict)


@dataclass(slots=True)
class PlayerStats:
    """一份统计结果，字段为 None 表示该来源没有数据。"""

    name: str
    server: str
    uuid: str = ''
    online: bool | None = None
    playtime_ms: int | None = None
    active_ms: int | None = None
    afk_ms: int | None = None
    mined: int | None = None
    placed: int | None = None
    crafted: int | None = None
    mob_kills: int | None = None
    deaths: int | None = None


# ===== 配置 =====

_settings = Settings()
_settings_mtime: float | None = None
_settings_loaded = False


def load_settings() -> Settings:
    """读取配置文件；不存在时生成带注释的示例。"""
    if not CONFIG_PATH.exists():
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(DEFAULT_CONFIG, encoding='Utf-8')
            logger.info(f'玩家统计：已生成示例配置 {CONFIG_PATH}。')
        except OSError as error:
            logger.warning(f'玩家统计：配置文件写入失败（{error}）。')
            return Settings()
    try:
        data = tomllib.loads(CONFIG_PATH.read_text('Utf-8'))
    except (OSError, tomllib.TOMLDecodeError) as error:
        logger.error(f'玩家统计：配置读取失败（{error}）。')
        return Settings()

    servers: dict[str, Profile] = {}
    for name, item in (data.get('servers') or {}).items():
        if not isinstance(item, dict):
            continue
        stats_dir = str(item.get('stats') or '').strip()
        usercache = str(item.get('usercache') or '').strip()
        skins_players = str(item.get('skins_players') or '').strip()
        skins_library = str(item.get('skins_library') or '').strip()
        servers[str(name)] = Profile(
            name=str(name),
            stats_dir=Path(stats_dir) if stats_dir else None,
            usercache=Path(usercache) if usercache else None,
            skins_players=Path(skins_players) if skins_players else None,
            skins_library=Path(skins_library) if skins_library else None,
        )
    return Settings(
        plan_api=str(data.get('plan_api') or '').strip().rstrip('/'),
        plan_server=str(data.get('plan_server') or '').strip(),
        plan_since=str(data.get('plan_since') or '').strip(),
        servers=servers,
    )


def refresh_settings(force: bool = False) -> Settings:
    """配置有改动（或 force）时重新加载。"""
    global _settings, _settings_mtime, _settings_loaded
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        mtime = None
    if force or not _settings_loaded or mtime != _settings_mtime:
        _settings = load_settings()
        _settings_loaded = True
        try:
            _settings_mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            _settings_mtime = mtime
        logger.info(f'玩家统计：已加载 {len(_settings.servers)} 个服务器配置。')
    return _settings


def pick_profile(settings: Settings, flag: str | None) -> Profile | None:
    """按名字或序号（从 1 开始）挑服务器，flag 为空取第一个。"""
    if not settings.servers:
        return None
    if not flag:
        return next(iter(settings.servers.values()))
    if flag.isdigit():
        index = int(flag)
        names = list(settings.servers)
        if 0 < index <= len(names):
            return settings.servers[names[index - 1]]
    for name, profile in settings.servers.items():
        if name == flag:
            return profile
    # 允许部分匹配，比如「生存」匹配「生存服」
    for name, profile in settings.servers.items():
        if flag in name or name in flag:
            return profile
    return None


# ===== 文件读取（带 mtime 缓存）=====

_usercache: dict[Path, tuple[float, dict[str, str], dict[str, str]]] = {}
_stats_cache: dict[Path, tuple[float, dict[str, Any]]] = {}
_block_set: dict[Path, tuple[float, set[str]]] = {}


def _read_usercache(path: Path | None) -> tuple[dict[str, str], dict[str, str]]:
    """读 usercache.json，返回（小写名 -> UUID, UUID -> 官方大小写名字）。"""
    if path is None or not path.exists():
        return {}, {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}, {}
    cached = _usercache.get(path)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]
    try:
        raw = json.loads(path.read_text('Utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        logger.debug(f'玩家统计：读取 usercache 失败（{error}）')
        return {}, {}
    mapping: dict[str, str] = {}
    display: dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        uuid = str(item.get('uuid') or '').strip()
        if name and uuid:
            mapping[name.casefold()] = uuid
            display[uuid] = name
    _usercache[path] = (mtime, mapping, display)
    return mapping, display


def _read_stats(path: Path) -> dict[str, Any] | None:
    """读一个玩家的原版统计文件（带 mtime 缓存）。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _stats_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text('Utf-8')).get('stats') or {}
    except (OSError, json.JSONDecodeError) as error:
        logger.debug(f'玩家统计：读取 {path.name} 失败（{error}）')
        return None
    _stats_cache[path] = (mtime, data)
    return data


def _block_items(profile: Profile) -> set[str]:
    """全体玩家 mining 过的方块 id 集合，用来把 used 里的「方块」和「物品」分开。

    只统计挖掘过的方块会漏掉极少数摆设类方块，但对「放置数」来说已经足够接近；
    结果按小时缓存，避免每次查询都扫全部 stats 文件。
    """
    now = time.time()
    if not profile.stats_dir:
        return set()
    cached = _block_set.get(profile.stats_dir)
    if cached and now - cached[0] < BLOCK_SET_CACHE_SECONDS:
        return cached[1]
    block_ids: set[str] = set()
    if profile.stats_dir.is_dir():
        for path in profile.stats_dir.glob('*.json'):
            data = _read_stats(path)
            if data:
                block_ids.update((data.get(SECTION_MINED) or {}).keys())
            if len(block_ids) > 5000:  # 明显异常的数据，别把内存吃光
                break
    _block_set[profile.stats_dir] = (now, block_ids)
    return block_ids


def collect_stats(profile: Profile, player: str) -> PlayerStats:
    """汇总一个服里某个玩家的原版统计。

    没有 stats 文件时不返回 None —— 新玩家可能还没落盘，Plan 那边仍有在线数据，
    最后是否有数据由 ``has_any_data`` 判断。
    """
    mapping, display = _read_usercache(profile.usercache)
    uuid = mapping.get(player.casefold())
    stats = PlayerStats(
        name=display.get(uuid, player) if uuid else player,
        server=profile.name,
        uuid=uuid or '',
    )
    if not uuid or not profile.stats_dir:
        return stats

    data = _read_stats(profile.stats_dir / f'{uuid}.json')
    if data is None:
        return stats

    blocks = _block_items(profile)
    used = data.get(SECTION_USED) or {}
    custom = data.get(CUSTOM) or {}
    stats.mined = sum((data.get(SECTION_MINED) or {}).values())
    stats.placed = sum(count for key, count in used.items() if key in blocks)
    stats.crafted = sum((data.get(SECTION_CRAFTED) or {}).values())
    stats.mob_kills = custom.get(STAT_MOB_KILLS)
    stats.deaths = custom.get(STAT_DEATHS)
    return stats


def has_any_data(stats: PlayerStats) -> bool:
    """两个来源里至少有一项有值，才算查到了这个人。"""
    return any(
        value is not None
        for value in (
            stats.playtime_ms,
            stats.mined,
            stats.crafted,
            stats.deaths,
            stats.mob_kills,
        )
    )


# ===== Plan =====

_plan_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


async def fetch_plan(player: str) -> dict[str, Any] | None:
    """从 Plan 的本地接口取在线/活跃/挂机（毫秒）。失败返回 None。"""
    settings = _settings
    if not settings.plan_api:
        return None
    key = player.casefold()
    cached = _plan_cache.get(key)
    if cached and time.time() - cached[0] < PLAN_CACHE_SECONDS:
        return cached[1]

    params = {'player': player}
    if settings.plan_server:
        params['server'] = settings.plan_server
    info: dict[str, Any] | None = None
    try:
        async with AsyncClient(timeout=PLAN_TIMEOUT) as client:
            response = await client.get(f'{settings.plan_api}/v1/player', params=params)
        if response.status_code == 200:
            info = (response.json() or {}).get('info') or None
        else:
            logger.debug(f'玩家统计：Plan 返回 {response.status_code}（{player}）')
    except Exception as error:
        logger.debug(f'玩家统计：查询 Plan 失败（{error}）')
    _plan_cache[key] = (time.time(), info)
    return info


async def enrich_with_plan(stats: PlayerStats) -> PlayerStats:
    """把 Plan 的三项时长补进结果；Plan 没数据就留空。"""
    info = await fetch_plan(stats.name)
    if not info:
        return stats
    playtime = info.get('playtime')
    active = info.get('active_playtime')
    afk = info.get('afk_time')
    stats.online = bool(info.get('online'))
    stats.playtime_ms = int(playtime) if isinstance(playtime, (int, float)) else None
    stats.active_ms = int(active) if isinstance(active, (int, float)) else None
    stats.afk_ms = int(afk) if isinstance(afk, (int, float)) else None
    return stats


# ===== 渲染 =====

CARD_WIDTH = 660
ROW_HEIGHT = 46
PADDING = 36


def _font(size: int):
    """按字号取字体，取不到就用 Pillow 默认字体。"""
    cached = _FONT_CACHE.get(size)
    if cached is not None:
        return cached
    font = None
    for path in FONT_CANDIDATES:
        try:
            if path.exists():
                font = ImageFont.truetype(str(path), size)
                break
        except Exception:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size)
        except TypeError:
            font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


def format_duration(milliseconds: int | None) -> str:
    """毫秒 -> 「X 小时 Y 分 Z 秒」。"""
    if milliseconds is None:
        return '—'
    seconds = max(0, int(milliseconds // 1000))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f'{hours} 小时 {minutes} 分 {secs} 秒'
    if minutes:
        return f'{minutes} 分 {secs} 秒'
    return f'{secs} 秒'


def format_count(value: int | None) -> str:
    """整数加千位分隔符，没有数据用破折号。"""
    return '—' if value is None else f'{value:,}'


SKIN_SUFFIXES = ('.customskin', '.playerskin', '.urlskin')
SKIN_DISK_DIR = Path('Data/SkinCache')
SKIN_TIMEOUT = 6.0
_skin_cache: dict[str, tuple[float, Image.Image | None, str]] = {}


async def _download_texture(url: str) -> Image.Image | None:
    """下载皮肤贴图，本地按 URL 哈希缓存一份。"""
    name = hashlib.sha1(url.encode()).hexdigest()[:16] + '.png'
    cached = SKIN_DISK_DIR / name
    if cached.exists():
        try:
            with Image.open(cached) as image:
                return image.copy()
        except Exception:
            cached.unlink(missing_ok=True)
    try:
        async with AsyncClient(timeout=SKIN_TIMEOUT) as client:
            response = await client.get(url)
        if response.status_code != 200:
            logger.debug(f'玩家统计：皮肤下载返回 {response.status_code}')
            return None
        raw = response.content
        SKIN_DISK_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(raw)
        with Image.open(io.BytesIO(raw)) as image:
            return image.copy()
    except Exception as error:
        logger.debug(f'玩家统计：皮肤下载失败（{error}）')
        return None


def _read_textures(path: Path) -> tuple[str, str] | None:
    """从 SkinsRestorer 的皮肤文件里取出贴图地址与模型（classic / slim）。"""
    try:
        value = json.loads(path.read_text('Utf-8')).get('value') or ''
        textures = json.loads(base64.b64decode(value))
        skin = textures['textures']['SKIN']
        model = str((skin.get('metadata') or {}).get('model') or 'classic').lower()
        return skin['url'], model
    except Exception as error:
        logger.debug(f'玩家统计：解析皮肤文件失败（{error}）')
        return None


async def fetch_skin(profile: Profile, uuid: str) -> tuple[Image.Image, str] | None:
    """按 UUID 从 SkinsRestorer 的皮肤库取一张皮肤，取不到返回 None。"""
    if not uuid or not profile.skins_players or not profile.skins_library:
        return None
    player_file = profile.skins_players / f'{uuid}.player'
    try:
        mtime = player_file.stat().st_mtime
    except OSError:
        return None
    cached = _skin_cache.get(uuid)
    if cached and cached[0] == mtime:
        return cached[1]

    identifier = ''
    try:
        history = json.loads(player_file.read_text('Utf-8')).get('history') or []
        if history:
            identifier = str((history[-1].get('skinIdentifier') or {}).get('identifier') or '')
    except Exception as error:
        logger.debug(f'玩家统计：读取 {player_file.name} 失败（{error}）')

    result = None
    for suffix in SKIN_SUFFIXES:
        path = profile.skins_library / f'{identifier}{suffix}'
        if not identifier or not path.exists():
            continue
        info = _read_textures(path)
        if not info:
            continue
        image = await _download_texture(info[0])
        if image is not None:
            result = (image, info[1])
            break

    _skin_cache[uuid] = (mtime, result)
    return result


_fallback: Image.Image | None = None


def fallback_skin() -> Image.Image:
    """没有皮肤数据时用一张自绘的默认皮肤（游戏里没设皮肤也是这个样子）。"""
    global _fallback
    if _fallback is not None:
        return _fallback
    skin = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(skin)
    colors = {
        'head': (198, 148, 106, 255),
        'body': (0, 150, 150, 255),
        'right_arm': (198, 148, 106, 255),
        'left_arm': (198, 148, 106, 255),
        'right_leg': (60, 60, 150, 255),
        'left_leg': (60, 60, 150, 255),
    }
    for part, rects in FACE_RECTS.items():
        for rect in rects.values():
            draw.rectangle((rect[0], rect[1], rect[2] - 1, rect[3] - 1), fill=colors[part])
    draw.rectangle(FACE_RECTS['head']['top'], fill=(70, 45, 28, 255))
    _fallback = skin
    return skin


def build_card(stats: PlayerStats, skin: tuple[Image.Image, str] | None = None) -> bytes:
    """画一张统计卡片，返回 PNG 字节。"""
    rows = [
        ('在线时长', format_duration(stats.playtime_ms)),
        ('活跃时长', format_duration(stats.active_ms)),
        ('挂机时长', format_duration(stats.afk_ms)),
        ('挖掘方块', format_count(stats.mined)),
        ('约计放置', format_count(stats.placed)),
        ('合成物品', format_count(stats.crafted)),
        ('击杀生物', format_count(stats.mob_kills)),
        ('死亡次数', format_count(stats.deaths)),
    ]

    # 随机姿势 + 随机朝向，每次查询都有点新鲜感
    character = None
    image, model = skin if skin is not None else (fallback_skin(), 'classic')
    pose_name = random.choice(list(POSES))
    pose = POSES[pose_name]
    item = None
    if pose_name in POSE_ITEMS:
        item = item_texture(random.choice(POSE_ITEMS[pose_name]))
    yaw = random.uniform(-0.35, 0.55)
    try:
        character = render_character(image, model=model, ppu=8, yaw=yaw, pose=pose, item=item)
    except Exception as error:
        logger.debug(f'玩家统计：3D 渲染失败（{error}）')

    char_column = 220 if character is not None else 0
    # 张开手臂的姿势会比站立宽很多，超宽就等比缩回列宽内
    if character is not None and character.width > char_column - 12:
        ratio = (char_column - 12) / character.width
        character = character.resize(
            (max(1, int(character.width * ratio)), max(1, int(character.height * ratio))),
            Image.Resampling.LANCZOS,
        )

    since = _settings.plan_since
    note = f'在线 / 活跃 / 挂机 自 {since} 起统计' if since else ''
    note_height = 30 if note else 0

    width = CARD_WIDTH
    header_height = 128
    rows_height = ROW_HEIGHT * len(rows)
    body_height = max(rows_height, (character.height + 8) if character else 0)
    height = header_height + body_height + PADDING - 4 + note_height

    card = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=22, fill=(32, 33, 36, 255))

    title_font = _font(38)
    tag_font = _font(18)
    row_font = _font(21)
    value_font = _font(21)

    draw.text((PADDING, 26), stats.name, font=title_font, fill=(245, 245, 245, 255))

    tag = stats.server
    tag_width = draw.textlength(tag, font=tag_font)
    tag_x = width - PADDING - tag_width
    draw.rounded_rectangle(
        (tag_x - 14, 32, tag_x + tag_width + 14, 64), radius=16, fill=(52, 54, 60, 255)
    )
    draw.text((tag_x, 38), tag, font=tag_font, fill=(210, 212, 216, 255))

    state = '在线' if stats.online else ('离线' if stats.online is False else '状态未知')
    draw.text((PADDING, 76), f'Minecraft 玩家统计 · {state}', font=tag_font, fill=(150, 152, 156, 255))
    draw.line((PADDING, 110, width - PADDING, 110), fill=(54, 56, 62, 255), width=1)

    rows_x = PADDING + char_column
    rows_width = width - rows_x - PADDING
    if character is not None:
        char_x = PADDING + (char_column - character.width) // 2
        char_y = header_height + (body_height - character.height) // 2
        card.alpha_composite(character, (char_x, max(header_height, char_y)))

    for index, (label, value) in enumerate(rows):
        y = header_height + index * ROW_HEIGHT
        if index % 2 == 0:
            draw.rounded_rectangle(
                (rows_x - 12, y - 6, rows_x + rows_width + 12, y + ROW_HEIGHT - 12),
                radius=10,
                fill=(38, 40, 44, 255),
            )
        draw.text((rows_x, y), label, font=row_font, fill=(160, 162, 168, 255))
        value_width = draw.textlength(value, font=value_font)
        draw.text(
            (rows_x + rows_width - value_width, y), value, font=value_font, fill=(238, 240, 244, 255)
        )

    if note:
        small_font = _font(15)
        draw.text(
            (PADDING, height - PADDING + 2),
            note,
            font=small_font,
            fill=(110, 112, 118, 255),
        )

    buffer = io.BytesIO()
    apply_watermark(card).save(buffer, format='PNG')
    return buffer.getvalue()


def build_text(stats: PlayerStats) -> str:
    """纯文字版本（渲染失败时兜底）。"""
    lines = [
        f'{stats.name} | {stats.server}',
        f'在线时长：{format_duration(stats.playtime_ms)}',
        f'活跃时长：{format_duration(stats.active_ms)}',
        f'挂机时长：{format_duration(stats.afk_ms)}',
        f'挖掘方块：{format_count(stats.mined)}',
        f'约计放置：{format_count(stats.placed)}',
        f'合成物品：{format_count(stats.crafted)}',
        f'击杀生物：{format_count(stats.mob_kills)}',
        f'死亡次数：{format_count(stats.deaths)}',
    ]
    return '\n'.join(lines)


# ===== 指令 =====

stats_cmd = on_command('stats', aliases={'统计', 'mystats'}, priority=0, block=True)


def _bound_player(user_id: str) -> str | None:
    """取某个 QQ 绑定的第一个游戏名。"""
    service = Globals.player_service
    if service is None:
        return None
    players = service.players.get(str(user_id)) or []
    return players[0] if players else None


@stats_cmd.handle()
async def handle_stats(event: MessageEvent, args: Message = CommandArg()):
    """解析参数并返回统计卡片。"""
    settings = refresh_settings()

    target: str | None = None
    server_flag: str | None = None
    for segment in args:
        if segment.type == 'at':
            target = str(segment.data.get('qq') or '')
        elif segment.type == 'text':
            parts = segment.data.get('text', '').split()
            if parts:
                if target is None:
                    target = parts[0]
                if len(parts) > 1:
                    server_flag = parts[1]

    # @ 的是 QQ 号：先换成绑定的游戏名
    if target and target.isdigit():
        target = _bound_player(target)
        if not target:
            await stats_cmd.finish(f'QQ {target} 还没有绑定游戏名，可以让他先发 .bound <游戏名>。')

    if not target:
        target = _bound_player(str(event.user_id))
        if not target:
            await stats_cmd.finish('你还没有绑定游戏名，先发 .bound <游戏名>，或者直接 .stats <玩家名>。')

    profile = pick_profile(settings, server_flag)
    if profile is None:
        await stats_cmd.finish('没有找到对应的服务器，请检查 Data/MCStats.toml 里的配置。')

    stats = collect_stats(profile, target)
    stats = await enrich_with_plan(stats)
    if not has_any_data(stats):
        names = list(settings.servers)
        await stats_cmd.finish(
            f'在「{profile.name}」里没有找到玩家 {target} 的记录。\n'
            f'可能是名字拼错，或者他从没在这个服玩过。（可查询的服：{"、".join(names)}）'
        )
    skin = await fetch_skin(profile, stats.uuid)
    card = None
    try:
        card = build_card(stats, skin)
    except Exception as error:
        # finish() 会抛控制流异常，绝不能放在 try 里
        logger.warning(f'玩家统计：卡片渲染失败（{error}），改发文字。')
    if card is None:
        await stats_cmd.finish(Message(MessageSegment.text(build_text(stats))))
    await stats_cmd.finish(Message(MessageSegment.image(card)))


reload_cmd = on_command('statsreload', aliases={'统计重载'}, priority=0, block=True)


@reload_cmd.handle()
async def handle_reload():
    """重新读取配置（服务器/Plan 地址）。"""
    settings = refresh_settings(force=True)
    await reload_cmd.finish(f'已重新加载玩家统计配置（{len(settings.servers)} 个服务器）。')


__plugin_meta__ = PluginMetadata(
    name='玩家统计',
    description='查询绑定的 MC 玩家在某个服务器的在线时长与方块/生物统计',
    usage='/stats [玩家] [服务器]',
    extra={'author': 'DICEALPHAHU', 'version': '1.0'},
)

register_help_commands([
    {
        'name': 'stats',
        'description': '查询玩家在服务器里的在线时长与挖掘/放置/合成等统计',
        'usage': '/stats [玩家] [服务器]',
        'aliases': ('统计',),
    },
    {
        'name': 'statsreload',
        'description': '重新加载玩家统计的服务器配置（管理员）',
        'usage': '/统计重载',
    },
])
