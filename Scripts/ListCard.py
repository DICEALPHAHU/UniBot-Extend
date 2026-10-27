"""用 Pillow 把 /list 的在线玩家列表画成一张卡片。

不依赖 Html2Pic / Playwright —— 这两个渲染引擎在当前 Alpine 镜像里装不上
（skia-python 没有 cp312 wheel、playwright 没有 musllinux wheel）。
这里直接用 Pillow 画图和贴头像，和 /player 的玩家卡是同一套思路。
"""

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 卡片尺寸与配色
CARD_WIDTH = 760
PADDING = 28
BACKGROUND = (32, 33, 36, 255)
PANEL = (24, 25, 28, 255)
TITLE_COLOR = (245, 245, 245, 255)
SUBTITLE_COLOR = (154, 160, 166, 255)
NAME_COLOR = (232, 234, 237, 255)
DIVIDER_COLOR = (58, 60, 64, 255)
ACCENT = (129, 182, 128, 255)

# 头像与布局
AVATAR_SIZE = 44
CHIP_HEIGHT = 56
CHIP_MIN_WIDTH = 210
COLUMNS = 3

# 字体候选：优先用 /player 已经下好的字体
FONT_CANDIDATES = (
    Path('Data/PlayerCard/NotoSansSC.ttf'),
    Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc'),
    Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
)

_FONT_CACHE: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _font_path() -> Path | None:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _font(size: int):
    """按字号加载字体，找不到字体文件时退回内置位图字体。"""
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    path = _font_path()
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


def _round_avatar(path: Path | None, size: int = AVATAR_SIZE) -> Image.Image:
    """把头像裁成圆形；没有头像时用灰底占位。"""
    if path is None or not Path(path).exists():
        image = Image.new('RGBA', (size, size), (60, 62, 66, 255))
    else:
        try:
            avatar = Image.open(path).convert('RGBA')
            avatar = avatar.resize((size, size), Image.Resampling.LANCZOS)
            image = avatar
        except Exception:
            image = Image.new('RGBA', (size, size), (60, 62, 66, 255))
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    result = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)
    return result


def _grid_height(count: int, columns: int) -> int:
    """给定条目数计算网格高度。"""
    if count <= 0:
        return 34
    rows = (count + columns - 1) // columns
    return rows * CHIP_HEIGHT


def build_list_card(
    players: dict[str, tuple[list[str], list[str]]],
    avatars: dict[str, Path] | None = None,
    bot_prefix: str = '',
) -> bytes:
    """把玩家列表渲染成 PNG 卡片。

        players: {服务器名: (真实玩家, 假人)}
        avatars: {玩家名: 头像文件路径}
    """
    avatars = avatars or {}
    sections = list(players.items())
    total_players = sum(len(real) + len(fake) for real, fake in players.values())

    # 先画在一张足够高的画布上，最后按实际内容高度裁剪，避免高度算错导致重叠
    canvas_height = 2200
    card = Image.new('RGBA', (CARD_WIDTH, canvas_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((0, 0, CARD_WIDTH - 1, canvas_height - 1), radius=22, fill=BACKGROUND)

    title_font = _font(30)
    subtitle_font = _font(17)
    section_font = _font(19)
    name_font = _font(17)
    small_font = _font(15)

    # 标题
    title = f'{sections[0][0]} 在线玩家' if len(sections) == 1 else '在线玩家'
    draw.text((PADDING, PADDING - 6), title, font=title_font, fill=TITLE_COLOR)
    subtitle = (
        f'共 {len(sections)} 个服务器 · {total_players} 人在线'
        if len(sections) > 1
        else f'{total_players} 人在线'
    )
    draw.text((PADDING, PADDING + 34), subtitle, font=subtitle_font, fill=SUBTITLE_COLOR)

    y = PADDING + 66
    if not sections:
        draw.text((PADDING, y), '当前没有已连接的服务器', font=section_font, fill=SUBTITLE_COLOR)
        y += 44

    for server_name, (real, fake) in sections:
        count = len(real) + len(fake)
        draw.text((PADDING, y), server_name, font=section_font, fill=ACCENT)
        count_text = f'{count} 人'
        width = draw.textlength(count_text, font=small_font)
        draw.text((CARD_WIDTH - PADDING - width, y + 3), count_text, font=small_font, fill=SUBTITLE_COLOR)
        y += 30
        draw.line((PADDING, y, CARD_WIDTH - PADDING, y), fill=DIVIDER_COLOR, width=1)
        y += 12

        if count == 0:
            draw.text((PADDING + 4, y + 6), '没有玩家在线', font=name_font, fill=SUBTITLE_COLOR)
            y += CHIP_HEIGHT
            continue

        groups: list[tuple[str, list[str]]] = [('玩家', real)]
        if bot_prefix:
            groups.append(('假人', fake))
        for label, names in groups:
            if not names:
                continue
            if bot_prefix:
                draw.text((PADDING, y), f'— {label} —', font=small_font, fill=SUBTITLE_COLOR)
                y += 23
            for index, player in enumerate(names):
                row, column = divmod(index, COLUMNS)
                chip_x = PADDING + column * ((CARD_WIDTH - 2 * PADDING) // COLUMNS)
                chip_y = y + row * CHIP_HEIGHT
                draw.rounded_rectangle(
                    (chip_x, chip_y + 4, chip_x + CHIP_MIN_WIDTH - 16, chip_y + CHIP_HEIGHT - 8),
                    radius=10,
                    fill=PANEL,
                )
                avatar = _round_avatar(avatars.get(player))
                card.alpha_composite(avatar, (chip_x + 6, chip_y + 6))
                draw.text((chip_x + 58, chip_y + 18), player, font=name_font, fill=NAME_COLOR)
            y += ((len(names) + COLUMNS - 1) // COLUMNS) * CHIP_HEIGHT + 8
        y += 6

    # 按内容裁剪，再画底部统计（这样永远不会重叠）
    content_height = max(y + 46, 170)
    card = card.crop((0, 0, CARD_WIDTH, content_height))
    draw = ImageDraw.Draw(card)
    footer = f'当前在线人数共 {total_players} 人'
    draw.text((PADDING, content_height - 34), footer, font=small_font, fill=SUBTITLE_COLOR)

    buffer = io.BytesIO()
    card.save(buffer, format='PNG')
    return buffer.getvalue()
