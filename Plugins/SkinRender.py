"""纯 Pillow + numpy 的 Minecraft 皮肤三维渲染。

把 64x64 皮肤贴图按模型的长宽高切成六个面，用透视相机投影，
再按深度排序逐面绘制，得到有厚度、能看到第二层（帽子 / 外套 / 袖子）的
正面或背面视图。

坐标约定：x 向右，y 向上，z 朝向镜头；一个模型单位等于皮肤上的 1 像素。
"""

import numpy as np
from PIL import Image

# 相机参数：偏航 31°、俯角 28.6°，配合 100 格的相机距离做轻度透视。
# 正交投影（距离无限远）会让角色看起来像贴纸，这里保留一点近大远小。
VIEW_YAW = 0.55
VIEW_ELEV = 0.5
CAM_DIST = 100.0

# 超采样倍率：先按 3 倍画，再缩小，边缘不锯齿
SUPERSAMPLE = 3
# 最终每模型单位的像素数
PPU = 9

# 各部件包围盒：(名字, 左下后角 (x, y, z), 尺寸 (w, h, d), 第二层外扩量)
PARTS = (
    ('head', (-4, 24, -4), (8, 8, 8), 0.5),
    ('body', (-4, 12, -2), (8, 12, 4), 0.25),
    ('right_arm', (4, 12, -2), (4, 12, 4), 0.25),
    ('left_arm', (-8, 12, -2), (4, 12, 4), 0.25),
    ('right_leg', (0, 0, -2), (4, 12, 4), 0.25),
    ('left_leg', (-4, 0, -2), (4, 12, 4), 0.25),
)

# 每个面的明暗，模拟简单的方向光
FACE_SHADE = {'front': 1.0, 'back': 0.86, 'right': 0.74, 'left': 0.74, 'top': 1.12}
FACE_NORMALS = {
    'front': (0, 0, 1),
    'back': (0, 0, -1),
    'right': (1, 0, 0),
    'left': (-1, 0, 0),
    'top': (0, 1, 0),
}

# 64x64 皮肤里各面的贴图矩形 (u0, v0, u1, v1)
FACE_RECTS = {
    'head': {
        'top': (8, 0, 16, 8),
        'right': (0, 8, 8, 16),
        'front': (8, 8, 16, 16),
        'left': (16, 8, 24, 16),
        'back': (24, 8, 32, 16),
    },
    'body': {
        'top': (20, 16, 28, 20),
        'right': (16, 20, 20, 32),
        'front': (20, 20, 28, 32),
        'left': (28, 20, 32, 32),
        'back': (32, 20, 40, 32),
    },
    'right_arm': {
        'top': (44, 16, 48, 20),
        'right': (40, 20, 44, 32),
        'front': (44, 20, 48, 32),
        'left': (48, 20, 52, 32),
        'back': (52, 20, 56, 32),
    },
    'left_arm': {
        'top': (36, 48, 40, 52),
        'right': (32, 52, 36, 64),
        'front': (36, 52, 40, 64),
        'left': (40, 52, 44, 64),
        'back': (44, 52, 48, 64),
    },
    'right_leg': {
        'top': (4, 16, 8, 20),
        'right': (0, 20, 4, 32),
        'front': (4, 20, 8, 32),
        'left': (8, 20, 12, 32),
        'back': (12, 20, 16, 32),
    },
    'left_leg': {
        'top': (20, 48, 24, 52),
        'right': (16, 52, 20, 64),
        'front': (20, 52, 24, 64),
        'left': (24, 52, 28, 64),
        'back': (28, 52, 32, 64),
    },
}

# 第二层贴图相对第一层的偏移
OVERLAY_OFFSET = {
    'head': (32, 0),
    'body': (0, 16),
    'right_arm': (0, 16),
    'left_arm': (16, 0),
    'right_leg': (0, 16),
    'left_leg': (-16, 0),
}

# Alex（slim）模型手臂只有 3 像素宽
SLIM_RECTS = {
    'right_arm': {
        'top': (44, 16, 47, 20),
        'right': (40, 20, 44, 32),
        'front': (44, 20, 47, 32),
        'left': (47, 20, 51, 32),
        'back': (51, 20, 54, 32),
    },
    'left_arm': {
        'top': (36, 48, 39, 52),
        'right': (32, 52, 36, 64),
        'front': (36, 52, 39, 64),
        'left': (39, 52, 43, 64),
        'back': (43, 52, 46, 64),
    },
}


def _upgrade_legacy(skin: Image.Image) -> Image.Image:
    """把 1.8 之前的 64x32 皮肤补成 64x64：左半身贴图用右半身镜像。

    老皮肤的左右肢共用右半边的贴图，直接按 64x64 取图会取到未定义区域。
    """
    canvas = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    canvas.paste(skin, (0, 0))
    for left, right in (('left_arm', 'right_arm'), ('left_leg', 'right_leg')):
        for face, rect in FACE_RECTS[left].items():
            piece = skin.crop(FACE_RECTS[right][face]).transpose(Image.FLIP_LEFT_RIGHT)
            canvas.paste(piece, (rect[0], rect[1]))
    return canvas


def prepare_skin(skin: Image.Image) -> tuple[Image.Image, bool]:
    """统一成 64x64 并返回 (贴图, 是否老皮肤)。老皮肤没有第二层。"""
    skin = skin.convert('RGBA')
    if skin.height < 64:
        return _upgrade_legacy(skin), True
    if skin.size != (64, 64):
        skin = skin.resize((64, 64), Image.Resampling.NEAREST)
    return skin, False


def _offset_rect(rect, offset):
    return (rect[0] + offset[0], rect[1] + offset[1], rect[2] + offset[0], rect[3] + offset[1])


def _rotate(vec, yaw):
    x, y, z = vec
    c, s = np.cos(yaw), np.sin(yaw)
    return (x * c + z * s, y, -x * s + z * c)


class Camera:
    """绕 y 轴偏航、再抬高俯视模型中心的透视相机。"""

    def __init__(self, yaw=VIEW_YAW, elev=VIEW_ELEV, dist=CAM_DIST, target=(0.0, 16.0, 0.0)):
        self.yaw = yaw
        self.dist = dist
        self.ce = float(np.cos(elev))
        self.se = float(np.sin(elev))
        self.pos = (target[0], target[1] + dist * self.se, target[2] + dist * self.ce)

    def project(self, point):
        x, y, z = _rotate(point, self.yaw)
        dx = x - self.pos[0]
        dy = y - self.pos[1]
        dz = z - self.pos[2]
        depth = -(dy * self.se + dz * self.ce)
        depth = max(depth, 1e-3)
        return ((dx * self.dist) / depth, ((dy * self.ce - dz * self.se) * self.dist) / depth, depth)

    def visible(self, point, normal):
        n = _rotate(normal, self.yaw)
        return n[0] * (point[0] - self.pos[0]) + n[1] * (point[1] - self.pos[1]) + n[2] * (point[2] - self.pos[2]) < 0


def _face_geometry(pos, size, face):
    """返回该面的 (起点, u 方向, v 方向)。"""
    x0, y0, z0 = pos
    w, h, d = size
    x1, y1, z1 = x0 + w, y0 + h, z0 + d
    if face == 'front':
        return (x1, y1, z1), (-w, 0, 0), (0, -h, 0)
    if face == 'back':
        return (x1, y1, z0), (-w, 0, 0), (0, -h, 0)
    if face == 'right':
        return (x1, y1, z0), (0, 0, d), (0, -h, 0)
    if face == 'left':
        return (x0, y1, z1), (0, 0, -d), (0, -h, 0)
    return (x1, y1, z0), (-w, 0, 0), (0, 0, d)  # top


def _collect(camera: Camera, model: str, legacy: bool):
    """收集所有可见面，按远到近排序。"""
    rects = dict(FACE_RECTS)
    if model == 'slim':
        rects.update(SLIM_RECTS)
    faces = []
    for name, pos, size, inflate in PARTS:
        layers = ('base',) if legacy else ('base', 'outer')
        for layer in layers:
            if layer == 'outer':
                p = (pos[0] - inflate, pos[1] - inflate, pos[2] - inflate)
                s = (size[0] + 2 * inflate, size[1] + 2 * inflate, size[2] + 2 * inflate)
                off = OVERLAY_OFFSET[name]
            else:
                p, s = pos, size
                off = (0, 0)
            for face in ('front', 'back', 'right', 'left', 'top'):
                origin, uv, vv = _face_geometry(p, s, face)
                center = tuple(origin[i] + (uv[i] + vv[i]) / 2 for i in range(3))
                if not camera.visible(center, FACE_NORMALS[face]):
                    continue
                corners = [origin]
                for coefs in ((1, 0), (1, 1), (0, 1)):
                    corners.append(tuple(origin[i] + coefs[0] * uv[i] + coefs[1] * vv[i] for i in range(3)))
                projected = [camera.project(corner) for corner in corners]
                depth = sum(point[2] for point in projected) / 4
                faces.append((depth, projected, _offset_rect(rects[name][face], off), face))
    faces.sort(key=lambda item: -item[0])
    return faces


def _rasterize(canvas, skin, rect, quad, shade):
    """把一个面的贴图按透视四边形画到画布上（双线性逆映射）。"""
    height, width = canvas.shape[:2]
    p00, p10, p11, p01 = quad
    ax, ay = p10[0] - p00[0], p10[1] - p00[1]
    bx, by = p01[0] - p00[0], p01[1] - p00[1]
    det = ax * by - ay * bx
    if abs(det) < 1e-6:
        return
    xs = [point[0] for point in quad]
    ys = [point[1] for point in quad]
    x0 = max(0, int(np.floor(min(xs))))
    x1 = min(width, int(np.ceil(max(xs))) + 1)
    y0 = max(0, int(np.floor(min(ys))))
    y1 = min(height, int(np.ceil(max(ys))) + 1)
    if x0 >= x1 or y0 >= y1:
        return

    grid_y, grid_x = np.mgrid[y0:y1, x0:x1]
    dx = grid_x - p00[0]
    dy = grid_y - p00[1]
    u = (dx * by - dy * bx) / det
    v = (ax * dy - ay * dx) / det
    mask = (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
    if not mask.any():
        return

    u0, v0, u1, v1 = rect
    tw, th = u1 - u0, v1 - v0
    tx = np.clip((u * tw).astype(np.int32), 0, tw - 1) + u0
    ty = np.clip((v * th).astype(np.int32), 0, th - 1) + v0
    src = skin[ty, tx].astype(np.float32) / 255.0
    src[..., :3] = np.clip(src[..., :3] * shade, 0, 1)
    alpha = src[..., 3] * mask

    dst = canvas[y0:y1, x0:x1]
    out_alpha = alpha + dst[..., 3] * (1 - alpha)
    safe = np.where(out_alpha > 1e-6, out_alpha, 1)
    rgb = (src[..., :3] * alpha[..., None] + dst[..., :3] * (dst[..., 3:4] * (1 - alpha[..., None]))) / safe[..., None]
    canvas[y0:y1, x0:x1, :3] = rgb
    canvas[y0:y1, x0:x1, 3] = out_alpha


def render_character(
    skin: Image.Image,
    model: str = 'classic',
    back: bool = False,
    ppu: int = PPU,
    yaw: float = VIEW_YAW,
) -> Image.Image:
    """把皮肤渲染成有厚度的正面（back=True 时背面）视图，透明背景。"""
    skin, legacy = prepare_skin(skin)
    angle = yaw + (np.pi if back else 0.0)
    camera = Camera(yaw=angle)
    faces = _collect(camera, model, legacy)

    points = [corner for _, quad, _, _ in faces for corner in quad]
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    pad = 0.6
    scale = ppu * SUPERSAMPLE
    px_w = int(np.ceil((max_x - min_x + pad * 2) * scale))
    px_h = int(np.ceil((max_y - min_y + pad * 2) * scale))
    canvas = np.zeros((px_h, px_w, 4), dtype=np.float32)

    def to_pixel(point):
        return ((point[0] - min_x + pad) * scale, (max_y - point[1] + pad) * scale)

    pixels = np.array(skin)
    for _, quad, rect, face in faces:
        _rasterize(canvas, pixels, rect, [to_pixel(corner) for corner in quad], FACE_SHADE[face])

    image = Image.fromarray((np.clip(canvas, 0, 1) * 255).astype(np.uint8), 'RGBA')
    return image.resize((max(1, px_w // SUPERSAMPLE), max(1, px_h // SUPERSAMPLE)), Image.Resampling.LANCZOS)
