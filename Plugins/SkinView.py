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

# 用 skinview3d 自带的动画当姿势：动作幅度和关节角度都是调好的
ANIMATIONS = ('idle', 'walk', 'run')
# 动画跑起来之后截图的时刻（毫秒），随机取，同一个动画每次定格都不一样
FRAME_DELAY = (250, 900)

_browser = None
_page = None
_lock = asyncio.Lock()


def available() -> bool:
    """渲染依赖是否就绪（JS 包和页面都在）。"""
    return VIEWER_PAGE.exists() and BUNDLE.exists()


async def _ensure_page():
    """惰性启动 Chromium 并打开渲染页，之后复用。"""
    global _browser, _page
    if _page is not None and not _page.is_closed():
        return _page
    if _browser is None or not _browser.is_connected():
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        _browser = await playwright.chromium.launch(
            args=['--disable-gpu', '--use-gl=swiftshader', '--enable-unsafe-swiftshader']
        )
    _page = await _browser.new_page(viewport={'width': CANVAS_SIZE, 'height': CANVAS_SIZE})
    await _page.goto(VIEWER_PAGE.resolve().as_uri())
    await _page.evaluate('(size) => window.init(size)', CANVAS_SIZE)
    return _page


async def render(
    skin: bytes,
    model: str = 'classic',
    animation: str = 'idle',
    yaw: float = 0.35,
    delay_ms: int = 500,
) -> bytes | None:
    """渲染一张透明背景的皮肤立绘（PNG 字节）；失败返回 None。"""
    if not available():
        return None
    if not skin:
        return None

    import base64

    data_url = 'data:image/png;base64,' + base64.b64encode(skin).decode()
    async with _lock:
        try:
            page = await _ensure_page()
            await asyncio.wait_for(
                page.evaluate(
                    '([skin, model, animation, yaw]) => window.play(skin, model, animation, yaw)',
                    [data_url, model, animation, yaw],
                ),
                timeout=RENDER_TIMEOUT,
            )
            # 让动画跑一会儿再定格，姿态才自然
            await asyncio.sleep(max(0, delay_ms) / 1000)
            element = await page.query_selector('#c')
            return await asyncio.wait_for(
                element.screenshot(omit_background=True), timeout=RENDER_TIMEOUT
            )
        except Exception as error:
            logger.warning(f'玩家统计：skinview3d 渲染失败（{error}），改用备用渲染。')
            global _page
            try:
                if _page is not None:
                    await _page.close()
            except Exception:
                pass
            _page = None
            return None


async def close() -> None:
    """关闭浏览器（退出时调用）。"""
    global _browser, _page
    try:
        if _page is not None and not _page.is_closed():
            await _page.close()
    except Exception:
        pass
    try:
        if _browser is not None:
            await _browser.close()
    except Exception:
        pass
    _page = None
    _browser = None
