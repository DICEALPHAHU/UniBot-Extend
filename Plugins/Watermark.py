"""
给 Pillow 画的图统一加水印。

HTML 模板渲染的图片（``/help``、``/list``、``/server``、``/luck`` 等）由
``Extensions/Default/Templates/Base.html`` + ``Base.css`` 的 ``.watermark`` 负责，
这里只处理插件自己用 Pillow 生成的图：``/player`` 玩家卡、B站视频快照。

用法::

    from .Watermark import apply_watermark, stamp_file

    apply_watermark(card).save(buffer, format='PNG')   # 有 Image 对象
    stamp_file(frame_path)                             # 有图片文件

水印失败不应该影响主功能，所以 ``stamp_file`` 内部吞掉异常并返回布尔值。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 与 HTML 模板里的署名保持一致
WATERMARK_TEXT = 'Powered by DICEALPHAHU Unibot'

# 依次尝试的字体：优先项目内的 Noto Sans SC，其次镜像里装的中文字体，最后 Pillow 自带位图字体。
# 水印是纯 ASCII，用哪个字体都不会缺字。
_FONT_CANDIDATES = (
    Path('Data/PlayerCard/NotoSansSC.ttf'),
    Path('Data/Kebiao/NotoSansSC.ttf'),
    Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
    Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    Path('/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf'),
)

_font_cache: dict[int, object] = {}


def _load_font(size: int):
    """按字号取字体，取不到就退回默认字体（不抛异常）。"""
    cached = _font_cache.get(size)
    if cached is not None:
        return cached
    font = None
    for path in _FONT_CANDIDATES:
        try:
            if path.exists():
                font = ImageFont.truetype(str(path), size)
                break
        except Exception:
            continue
    if font is None:
        try:
            # Pillow >= 10.1 支持带字号的默认字体
            font = ImageFont.load_default(size)
        except Exception:
            font = ImageFont.load_default()
    _font_cache[size] = font
    return font


def _average_luminance(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    """取一块区域的平均亮度（0-255），用来决定水印用黑字还是白字。"""
    try:
        region = image.crop(box).convert('L')
        if region.width > 32 or region.height > 16:
            region = region.resize((max(1, region.width // 8), max(1, region.height // 8)))
        pixels = list(region.getdata())
        return sum(pixels) / max(1, len(pixels))
    except Exception:
        return 0.0


def apply_watermark(image: Image.Image, *, opacity: int = 130, size: int | None = None) -> Image.Image:
    """在右下角叠加水印，返回新图（不修改传入的对象）。

    字号按图片高度自适应（12~26px）；文字颜色按水印区域的背景亮度自动切
    黑/白，再配一层反色投影，深色卡片和浅色视频快照上都看得清。
    """
    base = image.copy() if image.mode == 'RGBA' else image.convert('RGBA')
    width, height = base.size
    if width < 40 or height < 30:
        return base

    # size 传了就用传的；否则按图片高度自适应（12~26px）
    font_size = size if size else max(12, min(26, round(height * 0.030)))
    font = _load_font(font_size)

    overlay = Image.new('RGBA', base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        left, top, right, bottom = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
        text_width, text_height = right - left, bottom - top
    except Exception:
        # 极老的 Pillow 没有 textbbox，退化用粗略估算
        text_width, text_height = font_size * len(WATERMARK_TEXT) // 2, font_size

    margin = max(8, round(min(width, height) * 0.025))
    x = max(0, width - text_width - margin)
    y = max(0, height - text_height - margin)

    luminance = _average_luminance(base, (x, y, min(width, x + text_width), min(height, y + text_height)))
    if luminance > 140:
        fill, shadow = (0, 0, 0, opacity), (255, 255, 255, 90)
    else:
        fill, shadow = (255, 255, 255, opacity), (0, 0, 0, max(0, opacity - 40))

    draw.text((x + 1, y + 1), WATERMARK_TEXT, font=font, fill=shadow)
    draw.text((x, y), WATERMARK_TEXT, font=font, fill=fill)
    return Image.alpha_composite(base, overlay)


def stamp_file(path: Path, *, opacity: int = 120) -> bool:
    """给图片文件原地加水印，成功返回 True；任何失败都返回 False，不影响主流程。"""
    try:
        with Image.open(path) as source:
            image_format = (source.format or 'PNG').upper()
            result = apply_watermark(source, opacity=opacity)
        if image_format in ('JPG', 'JPEG'):
            result.convert('RGB').save(path, format=image_format)
        else:
            result.save(path, format=image_format)
        return True
    except Exception:
        return False
