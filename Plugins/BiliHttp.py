"""B 站请求的 HTTP 后端封装。

默认用 httpx；把 ``BILI_HTTP_BACKEND`` 设为 ``auto``（默认）或 ``curl_cffi`` 时，
如果环境里装了 ``curl_cffi``，就改用浏览器 TLS 指纹发请求 —— 机房 IP 访问
``api.bilibili.com`` 时更容易过风控（返回 412 / -352 的概率更低）。

设计取舍：
- 开关只作用于 **API 请求**（UP 投稿列表、用户信息、短链解析）。
- 视频本体下载仍走 httpx：CDN 一般不看 TLS 指纹，且 httpx 的流式下载更可控。
"""

import importlib.util
from typing import Any

import httpx

from .Config import plugin_config

BACKEND_HTTPX = 'httpx'
BACKEND_CURL_CFFI = 'curl_cffi'
# 伪装目标：与主流 Chrome 的 TLS/HTTP2 指纹一致
CURL_CFFI_IMPERSONATE = 'chrome131'

DEFAULT_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    ),
    'Referer': 'https://www.bilibili.com/',
    'Origin': 'https://www.bilibili.com',
}


def curl_cffi_available() -> bool:
    """环境里是否装了 curl_cffi。"""
    return importlib.util.find_spec('curl_cffi') is not None


def resolve_backend() -> str:
    """解析配置，返回实际使用的后端名。"""
    configured = (plugin_config.bili_http_backend or 'auto').strip().lower()
    if configured in (BACKEND_HTTPX, 'async'):
        return BACKEND_HTTPX
    if configured in (BACKEND_CURL_CFFI, 'curl', 'chrome'):
        return BACKEND_CURL_CFFI if curl_cffi_available() else BACKEND_HTTPX
    # auto：有 curl_cffi 就用，没有就回退 httpx
    return BACKEND_CURL_CFFI if curl_cffi_available() else BACKEND_HTTPX


async def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict | None]:
    """发起 GET 请求并解析 JSON，返回 (状态码, 数据)。网络异常会向上抛。"""
    request_headers = {**DEFAULT_HEADERS, **(headers or {})}
    if resolve_backend() == BACKEND_CURL_CFFI:
        from curl_cffi.requests import AsyncSession

        async with AsyncSession(impersonate=CURL_CFFI_IMPERSONATE) as session:
            response = await session.get(url, params=params, headers=request_headers, timeout=timeout)
        status = response.status_code
        try:
            return status, response.json()
        except Exception:
            return status, None

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, params=params, headers=request_headers)
    status = response.status_code
    try:
        return status, response.json()
    except Exception:
        return status, None


async def get_text(url: str, *, headers: dict[str, str] | None = None, timeout: float = 10.0) -> tuple[int, str]:
    """发起 GET 请求并返回文本（用于短链跳转等）。"""
    request_headers = {**DEFAULT_HEADERS, **(headers or {})}
    if resolve_backend() == BACKEND_CURL_CFFI:
        from curl_cffi.requests import AsyncSession

        async with AsyncSession(impersonate=CURL_CFFI_IMPERSONATE) as session:
            response = await session.get(url, headers=request_headers, timeout=timeout)
        return response.status_code, response.text

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=request_headers)
    return response.status_code, response.text
