"""用 skinview3d + Chromium 渲染 Minecraft 皮肤（替换手写的软件光栅化）。

为什么要换：手写的渲染器没有骨骼、没有正确的关节支点，肢体一转就插进身体/脑袋里，
接缝、遮挡全靠补丁。skinview3d 是皮肤站常用的 WebGL 渲染库，模型、层级、遮挡都是对的。

实现：容器里本来就有 Playwright + Chromium（图片模式在用），这里复用同一个浏览器，
加载本地 viewer.html，把皮肤塞进去、摆好姿势、截一张透明背景的图。

渲染不可用时返回 None，调用方回落到旧的 SkinRender。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from Scripts.Logging import logger

VIEWER_DIR = Path('Data/SkinView')
VIEWER_PAGE = VIEWER_DIR / 'viewer.html'
BUNDLE = VIEWER_DIR / 'skinview3d.bundle.js'
CANVAS_SIZE = 420
RENDER_TIMEOUT = 20.0

# 姿势表：要么用 skinview3d 自带动画（幅度自然），要么手摆关节（注意旋转轴是
# three.js 的本地坐标，手臂竖直向下是 0，绕 z 转到 3.9 左右才是举手）。
ANIMATIONS = ('idle', 'walk', 'run')

POSES: dict[str, dict[str, list[float]]] = {
    # 手臂是一整根刚性长方体，没有肘关节，所以只保留这类「整条手臂摆动」的姿势；
    # 摸下巴/扶额/敬礼这类需要弯肘的动作做不出来，硬凑会穿模。
    'wave': {'rightArm': [0, 0, 3.9], 'head': [0, 0.15, 0.05]},
    'cheer': {'rightArm': [0, 0, 3.9], 'leftArm': [0, 0, -3.9]},
    'point': {'rightArm': [-1.5, 0, 0], 'head': [0, -0.2, 0]},
    'shrug': {'rightArm': [0, 0, 1.2], 'leftArm': [0, 0, -1.2], 'head': [-0.1, 0, 0]},
    'stretch': {'rightArm': [0, 0, 3.6], 'leftArm': [0, 0, -3.6], 'head': [-0.15, 0, 0]},
    'lean': {'body': [0, 0, 0.12], 'head': [0, 0.2, 0.08], 'rightArm': [0, 0, 0.5]},
    # 蹲下：像 MC 的潜行那样把上身整体下沉、身体前倾（腿保持直立）
    'sneak': {'_sink': -1.6, 'body': [0.32, 0, 0], 'head': [-0.16, 0, 0]},
    'crouch': {'_sink': -2.8, 'body': [0.48, 0, 0], 'head': [-0.26, 0, 0]},
    'crouch_lite': {'_sink': -2.2, 'body': [0.38, 0, 0], 'head': [-0.2, 0, 0], 'rightArm': [-0.2, 0, 0], 'leftArm': [-0.2, 0, 0]},
    'sit': {
        'rightLeg': [-1.5, 0, 0],
        'leftLeg': [-1.5, 0, 0],
        'rightArm': [-0.4, 0, 0],
        'leftArm': [-0.4, 0, 0],
    },
}

# 所有可选项：动画 + 静态姿势
POSE_NAMES = tuple(ANIMATIONS) + tuple(POSES)

# 蹲/坐这类动作在正面视角看不清腿的弯折，用 45° 以上的侧面视角
SIDE_VIEW_POSES = frozenset({'sneak', 'crouch', 'crouch_lite', 'sit'})


def random_yaw(pose: str) -> float:
    """按姿势挑一个合适的观察角度。"""
    import random as _random

    if pose in SIDE_VIEW_POSES:
        return _random.uniform(0.55, 0.85)
    return _random.uniform(-0.35, 0.55)


# 定格时刻（毫秒）：动画要跑一会儿才自然，静态姿势几十毫秒就够
FRAME_DELAY = {'default': (250, 900), 'pose': (150, 260)}

_browser = None
_playwright = None
_page = None
_ready = False
_lock = asyncio.Lock()


def available() -> bool:
    """渲染依赖是否就绪（JS 包和页面都在）。"""
    return VIEWER_PAGE.exists() and BUNDLE.exists()


async def _drop_browser() -> None:
    """关掉浏览器并停掉 playwright driver。

    不 stop 的话，每次因为浏览器失联而重启，就会多漏一套 driver + Chromium 进程，
    内存一路涨上去。
    """
    global _browser, _playwright, _page, _ready
    if _page is not None:
        try:
            await _page.close()
        except Exception:
            pass
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            pass
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:
            pass
    _page = None
    _browser = None
    _playwright = None
    _ready = False


async def _ensure_page():
    """惰性启动 Chromium 并打开渲染页；复用浏览器，页面被 park 过就重新初始化。"""
    global _browser, _playwright, _page, _ready
    if _browser is not None and not _browser.is_connected():
        await _drop_browser()
    if _browser is None:
        from playwright.async_api import async_playwright

        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            args=['--disable-gpu', '--use-gl=swiftshader', '--enable-unsafe-swiftshader']
        )
    if _page is None or _page.is_closed():
        _page = await _browser.new_page(viewport={'width': CANVAS_SIZE, 'height': CANVAS_SIZE})
        _ready = False
    if not _ready:
        await _page.goto(VIEWER_PAGE.resolve().as_uri())
        await _page.evaluate('(size) => window.init(size)', CANVAS_SIZE)
        _ready = True
    return _page


async def _park() -> None:
    """把渲染页切到空白页，停掉 skinview3d 常驻的 requestAnimationFrame 循环。

    SkinViewer 自带常驻动画循环；无 GPU 时走 SwiftShader 软件渲染，页面不关就会
    以 1~2 个核持续重绘同一帧。截图后必须 park，否则 CPU 永远下不来。
    """
    global _page, _ready
    _ready = False
    if _page is None or _page.is_closed():
        return
    try:
        await _page.goto('about:blank')
    except Exception:
        # 切不过去就把页面关掉：宁可下次重建，也不能让它继续空转
        try:
            await _page.close()
        except Exception:
            pass
        _page = None


def pose_spec(name: str) -> dict:
    """把姿势名转成 viewer 认识的 spec。"""
    if name in POSES:
        return {'pose': POSES[name]}
    if name in ANIMATIONS:
        return {'animation': name}
    return {'animation': 'idle'}


async def render(
    skin: bytes,
    model: str = 'classic',
    pose: str = 'idle',
    yaw: float = 0.35,
    delay_ms: int | None = None,
) -> bytes | None:
    """渲染一张透明背景的皮肤立绘（PNG 字节）；失败返回 None。"""
    if not available():
        return None
    if not skin:
        return None

    import base64

    data_url = 'data:image/png;base64,' + base64.b64encode(skin).decode()
    spec = pose_spec(pose)
    if delay_ms is None:
        low, high = FRAME_DELAY['pose'] if 'pose' in spec else FRAME_DELAY['default']
        import random as _random
        delay_ms = _random.randint(low, high)
    async with _lock:
        try:
            page = await _ensure_page()
            await asyncio.wait_for(
                page.evaluate(
                    '([skin, model, spec, yaw]) => window.play(skin, model, spec, yaw)',
                    [data_url, model, spec, yaw],
                ),
                timeout=RENDER_TIMEOUT,
            )
            # 让动画跑一会儿再定格，姿态才自然
            await asyncio.sleep(max(0, delay_ms) / 1000)
            element = await page.query_selector('#c')
            image = await asyncio.wait_for(
                element.screenshot(omit_background=True), timeout=RENDER_TIMEOUT
            )
            # 截图完成后立刻停掉渲染循环，否则软件渲染会一直空转
            await _park()
            return image
        except Exception as error:
            logger.warning(f'玩家统计：skinview3d 渲染失败（{error}），改用备用渲染。')
            global _page, _ready
            try:
                if _page is not None:
                    await _page.close()
            except Exception:
                pass
            _page = None
            _ready = False
            return None


async def close() -> None:
    """关闭浏览器（退出时调用）。"""
    await _drop_browser()
