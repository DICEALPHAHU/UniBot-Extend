"""B 站凭证：加载、保存、校验、刷新，以及扫码登录。

凭证来源优先级：
1. ``.env`` 里的 ``BILI_COOKIE``（``SESSDATA=xxx; bili_jct=yyy; DedeUserID=zzz``）
2. 凭证文件 ``Data/Bili/credential.json``（扫码登录后自动写入）

只有拿到 ``SESSDATA`` 才算有效；没有凭证时所有 B 站接口走匿名请求。
"""

import io
import json
import time
from pathlib import Path

import httpx
import qrcode
from bilibili_api import Credential, user as bili_user
from nonebot import logger

from .Config import plugin_config

CREDENTIAL_PATH = Path('Data/Bili') / 'credential.json'

QR_GENERATE_URL = 'https://passport.bilibili.com/x/passport-login/web/qrcode/generate'
QR_POLL_URL = 'https://passport.bilibili.com/x/passport-login/web/qrcode/poll'

QR_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    ),
    'Referer': 'https://www.bilibili.com/',
}

# 轮询接口里 data.code 的含义
QR_STATUS_SUCCESS = 0
QR_STATUS_WAITING = 86101
QR_STATUS_SCANNED = 86090
QR_STATUS_EXPIRED = 86038
# 二维码有效期（秒）
QR_TTL_SECONDS = 180

# 需要落盘的 Cookie 键
CREDENTIAL_KEYS = ('SESSDATA', 'bili_jct', 'DedeUserID', 'ac_time_value')

# 凭证缓存：None 表示「尚未加载」
_credential: Credential | None = None
# 匿名凭证（只带 buvid3 / buvid4，用来降低被风控的概率）
_anonymous_credential: Credential | None = None
_loaded = False
_last_refresh_check = 0.0
REFRESH_CHECK_INTERVAL = 3600


def parse_cookie_string(text: str) -> dict[str, str]:
    """把 ``a=1; b=2`` 形式的 Cookie 字符串解析成字典。"""
    cookies: dict[str, str] = {}
    for chunk in (text or '').split(';'):
        name, _, value = chunk.partition('=')
        name, value = name.strip(), value.strip()
        if name and value:
            cookies[name] = value
    return cookies


def _normalize_keys(cookies: dict[str, str]) -> dict[str, str]:
    """兼容小写键与 ``sessdata`` 这类写法。"""
    normalized = dict(cookies)
    for key in CREDENTIAL_KEYS:
        if key not in normalized:
            lowered = key.lower()
            if lowered in normalized:
                normalized[key] = normalized[lowered]
    return normalized


def load_cookies() -> dict[str, str]:
    """按优先级读取 Cookie（环境变量优先于凭证文件）。"""
    from_env = parse_cookie_string(plugin_config.bili_cookie or '')
    if 'SESSDATA' in from_env:
        return from_env
    if CREDENTIAL_PATH.exists():
        try:
            return _normalize_keys(json.loads(CREDENTIAL_PATH.read_text(encoding='utf-8')))
        except Exception:
            return {}
    return {}


def save_cookies(cookies: dict[str, str]) -> None:
    """把 Cookie 落盘到凭证文件。"""
    if not cookies.get('SESSDATA'):
        return
    CREDENTIAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIAL_PATH.write_text(
        json.dumps({k: v for k, v in cookies.items() if v}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    CREDENTIAL_PATH.chmod(0o600)


def invalidate() -> None:
    """丢弃内存里的凭证缓存，下次使用时重新加载。"""
    global _credential, _anonymous_credential, _loaded
    _credential = None
    _anonymous_credential = None
    _loaded = False


async def _announce_credential(credential: Credential) -> None:
    """首次加载凭证时在控制台说明当前登录的是哪个账号。"""
    uid = getattr(credential, 'dedeuserid', '') or '未知'
    nickname = ''
    try:
        info = await bili_user.User(int(uid), credential=credential).get_user_info()
        nickname = ((info or {}).get('name') or '').strip()
    except Exception:
        pass
    if nickname:
        logger.info(f'B站凭证已加载（账号：{nickname}，UID：{uid}）')
    else:
        logger.info(f'B站凭证已加载（UID：{uid}，昵称获取失败）')


async def get_anonymous_credential() -> Credential | None:
    """匿名凭证：向 B 站要一份 buvid3 / buvid4，让请求更像真实浏览器。"""
    global _anonymous_credential
    if _anonymous_credential is not None:
        return _anonymous_credential
    try:
        credential = Credential()
        # 注意：这个接口只返回字典，不会写回实例属性，得自己赋值
        cookies = await credential.get_buvid_cookies() or {}
        credential.buvid3 = cookies.get('buvid3') or ''
        credential.buvid4 = cookies.get('buvid4') or ''
        _anonymous_credential = credential
        return credential
    except Exception:
        return None


async def get_credential() -> Credential | None:
    """获取当前凭证；未登录时返回带 buvid 的匿名凭证，完全失败才返回 None。

    每小时最多检查一次是否需要刷新（仅当 Cookie 里带 ``ac_time_value`` 时才刷新）。
    """
    global _credential, _loaded, _last_refresh_check

    if not _loaded:
        cookies = load_cookies()
        _credential = Credential.from_cookies(cookies) if cookies.get('SESSDATA') else None
        _loaded = True
        if _credential is not None:
            await _announce_credential(_credential)
        else:
            logger.info('B站未登录，使用匿名凭证（buvid3 / buvid4）')

    credential = _credential
    if credential is None:
        return await get_anonymous_credential()

    now = time.monotonic()
    if now - _last_refresh_check < REFRESH_CHECK_INTERVAL:
        return credential
    _last_refresh_check = now

    try:
        if not await credential.check_valid():
            # 偶发误报很常见，这里只记日志、不丢弃凭证
            pass
        elif credential.has_ac_time_value() and credential.has_bili_jct():
            if await credential.check_refresh():
                await credential.refresh()
                save_cookies(credential.get_cookies())
    except Exception:
        # 校验失败不影响正常请求，保持原凭证
        pass
    return credential


def render_qr_png(url: str) -> bytes:
    """把登录地址渲染成 PNG。"""
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color='black', back_color='white')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def print_qr_ascii(url: str) -> None:
    """在控制台打印 ASCII 二维码（直接 print，避免日志前缀打乱图案）。"""
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True, out=None)


async def generate_login_qr() -> tuple[str, str]:
    """生成登录二维码，返回 (二维码内容 URL, qrcode_key)。"""
    async with httpx.AsyncClient(timeout=10, headers=QR_HEADERS) as client:
        response = await client.get(QR_GENERATE_URL)
    payload = response.json() or {}
    data = payload.get('data') or {}
    url = data.get('url') or ''
    key = data.get('qrcode_key') or ''
    if not url or not key:
        raise RuntimeError(f'生成二维码失败（接口返回 code={payload.get("code")}）')
    return url, key


async def poll_login_qr(key: str) -> tuple[str, Credential | None]:
    """轮询扫码状态，返回 (状态, 凭证)。

    状态取值：``waiting`` / ``scanned`` / ``success`` / ``expired`` / ``error``
    """
    async with httpx.AsyncClient(timeout=10, headers=QR_HEADERS) as client:
        response = await client.get(QR_POLL_URL, params={'qrcode_key': key})
        set_cookies = response.headers.get_list('set-cookie')
    payload = response.json() or {}
    data = payload.get('data') or {}
    status = data.get('code')

    if status == QR_STATUS_SUCCESS:
        cookies = _extract_cookies(data.get('url') or '', set_cookies)
        if not cookies.get('SESSDATA'):
            return 'error', None
        save_cookies(cookies)
        invalidate()
        return 'success', Credential.from_cookies(cookies)
    if status == QR_STATUS_WAITING:
        return 'waiting', None
    if status == QR_STATUS_SCANNED:
        return 'scanned', None
    if status == QR_STATUS_EXPIRED:
        return 'expired', None
    return 'error', None


def _extract_cookies(callback_url: str, set_cookie_headers: list[str]) -> dict[str, str]:
    """从回调 URL 的查询参数和 Set-Cookie 头里提取登录凭证。"""
    cookies: dict[str, str] = {}
    query = callback_url.partition('?')[2]
    for chunk in query.split('&'):
        name, _, value = chunk.partition('=')
        name = name.strip().removeprefix('amp;')
        if name in CREDENTIAL_KEYS and value:
            cookies.setdefault(name, value.strip())
    for header in set_cookie_headers or []:
        name, _, value = header.partition('=')
        name = name.strip()
        if name in CREDENTIAL_KEYS and value:
            cookies.setdefault(name, value.split(';')[0].strip())
    return cookies
