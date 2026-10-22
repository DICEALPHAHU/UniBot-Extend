"""
B站 UP 动态监控插件。

监控 UP 主最新视频（bilibili_api 用户投稿接口），下载视频本体、生成快照并推送。
不再依赖第三方聚合接口；B 站接口需要时自动带上扫码登录的凭证。
"""

import asyncio
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

import subprocess
from bilibili_api import user, video
from bilibili_api.exceptions import ResponseCodeException
from bilibili_api.utils.aid_bvid_transformer import bvid2aid
from bilibili_api.video import (
    VideoCodecs,
    VideoDownloadURLDataDetecter,
    VideoQuality,
)
from httpx import AsyncClient
from nonebot import get_bots, get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from . import BiliCredential
from .Config import plugin_config

from Scripts.Config import config
from .HelpEntry import register_help_commands

# 核心路径配置
# ★ 必须落在「UniBot 与 NapCat 两个容器里路径完全相同」的共享目录上，
#   否则 NapCat 收到 file:// 路径后在自己容器里找不到文件（ENOENT）。
#   宿主机 /srv/napcat/config/unibot-temp 同时挂给了两个容器。
_SHARED_TEMP = Path('/app/napcat/config/unibot-temp')
try:
    (_SHARED_TEMP / 'Bili').mkdir(parents=True, exist_ok=True)
    TEMP_ROOT = _SHARED_TEMP
except OSError as _error:
    logger.warning(
        f'共享临时目录不可用（{_error}），退回 ./Temp：'
        f'图片仍可发送（走 base64），但视频会因 NapCat 读不到文件而失败'
    )
    TEMP_ROOT = Path('./Temp')

# 所有临时文件路径都基于动态根目录生成
CACHE_FILE = TEMP_ROOT / 'Bili' / 'bvtemp.json'  # 缓存文件
VIDEO_DIR = TEMP_ROOT / 'Bili' / 'videos'  # 临时视频目录
FRAME_DIR = TEMP_ROOT / 'Bili' / 'frames'  # 视频帧目录

# 自动创建所有必要目录
for dir_path in [CACHE_FILE.parent, VIDEO_DIR, FRAME_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# 将项目根目录加入 Python 路径（确保导入正常）
sys.path.insert(0, str(TEMP_ROOT))

# 请求头
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0 Safari/537.36'

# 从共享配置读取（Plugins/Config.py -> plugin_config，可由 .env / 环境变量覆盖）
BILI_WATCHER_ENABLED = plugin_config.bili_watcher_enabled
BILI_UP_UID = plugin_config.bili_up_uid
BILI_UP_WAITSEC = plugin_config.bili_up_waitsec
MESSAGE_GROUPS = config.message_groups
BILI_FRAME_SEC = plugin_config.bili_watcher_framesec

# 视频推送体积上限（MB）：超过则不发视频，退回只发快照。
# 注意 base64 上传时字符串会膨胀约 33%，实际上限还取决于 OneBot 实现。
BILI_VIDEO_MAX_MB = plugin_config.bili_video_max_mb
BILI_VIDEO_MAX_BYTES = BILI_VIDEO_MAX_MB * 1024 * 1024
# 视频成品保留天数
BILI_VIDEO_KEEP_DAYS = plugin_config.bili_video_keep_days
# 视频发送方式：video（视频消息）/ file（群文件）
BILI_VIDEO_SEND_MODE = (plugin_config.bili_video_send_mode or 'video').strip().lower()
# 发送前是否重编码成 QQ 友好格式
BILI_VIDEO_NORMALIZE = plugin_config.bili_video_normalize
BILI_VIDEO_NORMALIZE_MAX_SECONDS = plugin_config.bili_video_normalize_max_min * 60

# ffmpeg：用于裁掉视频开头的黑场淡入（QQ/NapCat 抽第 0 帧当封面，黑场会导致封面全黑）
FFMPEG_CONFIGURED_PATH = (plugin_config.bili_ffmpeg_path or '').strip()
TRIM_BLACK_ENABLED = plugin_config.bili_trim_black_enabled
BLACK_PIXEL_THRESHOLD = plugin_config.bili_black_pixel_threshold
BLACK_MIN_DURATION = plugin_config.bili_black_min_duration
MAX_TRIM_SECONDS = plugin_config.bili_max_trim_seconds
MAX_PROCESS_SECONDS = plugin_config.bili_max_process_minutes * 60
# 找不到 ffmpeg 时是否自动下载（下载到 Temp/ffmpeg/，只下一次）
FFMPEG_AUTO_DOWNLOAD = plugin_config.bili_ffmpeg_auto_download
FFMPEG_DIR = TEMP_ROOT / 'ffmpeg'
# ffmpeg 下载源（依次尝试）。
#
# 优先用 imageio-ffmpeg 的 wheel（约 30MB），而不是 ffmpeg 官方静态包（约 100MB）：
#   1. wheel 小得多，且国内 PyPI 镜像（华为云）速度可达数十 MB/s；
#   2. 解压后即 imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v*.exe，
#      是 gyan.dev essentials 构建，含 blackdetect 滤镜与 libx264。
# GitHub 直连在国内多数网络不可用（ghproxy 类镜像也常失效），故仅作最后兜底。
FFMPEG_DOWNLOAD_URLS = [
    # 华为云 PyPI 镜像（与 pyproject.toml 的 index-url 同源，国内最快）
    'https://repo.huaweicloud.com/repository/pypi/packages/2c/c6/'
    'fa760e12a2483469e2bf5058c5faff664acf66cadb4df2ad6205b016a73d/'
    'imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl',
    # PyPI 官方源（带 CDN，通常也可达）
    'https://files.pythonhosted.org/packages/2c/c6/'
    'fa760e12a2483469e2bf5058c5faff664acf66cadb4df2ad6205b016a73d/'
    'imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl',
    # GitHub 兜底（含 ffmpeg + ffprobe，但国内多半不可达）
    'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/'
    'ffmpeg-master-latest-win64-gpl.zip',
]

# 插件元信息
__plugin_meta__ = PluginMetadata(
    name='B站UP动态监控',
    description='监控UP主最新视频，下载视频本体、生成快照并推送（数据来源：bilibili_api）',
    usage="""
    .bv <B站视频链接> - 解析B站视频（支持b23.tv短链接）并返回截图
    .biliclear - 清理缓存和临时文件
    .biliwatch - 手动触发一次UP主视频监控
    .bilistatus - 查看当前监控状态
    """,
    extra={
        'author': 'DICEALPHAHU',
        'version': '1.1',
        'features': ['防重复推送', '异步优化', '自动清理临时文件', '完善日志'],
    },
)

# 全局变量
DRIVER = get_driver()
BV_PATTERN = re.compile(r'BV[a-zA-Z0-9]+')
PUSH_LOCK = asyncio.Lock()  # 防止高频推送的锁
TEMP_FILE_EXPIRE_DAYS = 7  # 临时文件清理阈值（天）
# 缓存缺失（首次启动 / 缓存被清空）时，超过该天数的旧稿件只记缓存不推送
FIRST_PUSH_MAX_AGE_DAYS = 3


def read_video_cache() -> dict:
    """读取缓存文件，返回最新视频信息，失败时返回默认值。"""
    default_cache = {
        'timestamp': 0,
        'title': '',
        'bvid': '',
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    if not CACHE_FILE.exists():
        logger.debug(f'缓存文件不存在：{CACHE_FILE.absolute()}，使用默认值')
        return default_cache

    try:
        cache = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
        # 兼容旧版缓存格式
        cache.setdefault('timestamp', 0)
        cache.setdefault('title', '')
        cache.setdefault('bvid', '')
        cache.setdefault('update_time', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        cache['timestamp'] = int(cache['timestamp'])
        return cache
    except Exception as error:
        logger.error(f'读取缓存文件失败：{error}，使用默认值')
        return default_cache


def write_video_cache(data: dict) -> None:
    """覆盖写入缓存文件，自动补充更新时间。"""
    try:
        data['update_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        data['timestamp'] = int(data.get('timestamp', 0))
        CACHE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        logger.success(f'缓存文件更新成功：{CACHE_FILE.absolute()}')
        logger.debug(f'缓存内容：{data}')
    except PermissionError:
        logger.error(f'写入缓存文件失败：权限不足（{CACHE_FILE.absolute()}）')
    except Exception as error:
        logger.error(f'写入缓存文件失败：{error}')


async def b23_to_long_url(short_url: str, max_retry: int = 2) -> str:
    """短链接转长链接，支持重试机制。"""
    if not short_url.startswith(('http://', 'https://')):
        short_url = f'https://{short_url}'

    for retry in range(max_retry + 1):
        try:
            async with AsyncClient(timeout=8, follow_redirects=False) as client:
                response = await client.get(
                    short_url,
                    headers={'User-Agent': USER_AGENT},
                )
                if response.status_code in (301, 302) and 'location' in response.headers:
                    long_url = unquote(response.headers['location'])
                    if long_url.startswith('/'):
                        long_url = f'https://www.bilibili.com{long_url}'
                    return long_url
        except Exception as error:
            logger.warning(f'短链接解析失败（重试{retry}/{max_retry}）：{error}')
            if retry < max_retry:
                await asyncio.sleep(0.5)
    
    logger.error(f'短链接解析失败，返回原始链接：{short_url}')
    return short_url


def simplify_bilibili_url(full_url: str) -> str:
    """提取 BV 号并生成标准链接。"""
    bv_match = BV_PATTERN.search(full_url)
    if not bv_match:
        return full_url
    bv_id = bv_match.group()
    return f'https://www.bilibili.com/video/{bv_id}/'


# ---------------- UP 主最新视频 ----------------
# 数据源可选：
#   bilibili_api（默认主路径，直连 B 站官方接口）
#   uapis        （老的第三方聚合接口，作为兜底）
#   auto         （默认：先用 bilibili_api，失败再回退 uapis）
BILI_LIST_SOURCE = (plugin_config.bili_list_source or 'auto').strip().lower()
UAPI_TOKEN = plugin_config.uapipro_token
UAPIS_ARCHIVES_URL = 'https://uapis.cn/api/v1/social/bilibili/archives'
UAPIS_USERINFO_URL = 'https://uapis.cn/api/v1/social/bilibili/userinfo'

# 连续失败计数：接口抖动时只在第一次记 warning，之后降级为 debug，避免刷屏
_list_failure_streak = 0
# UP 主昵称缓存：昵称基本不变，没必要每次推送都查
_nickname_cache: dict[int, tuple[float, str]] = {}
NICKNAME_CACHE_TTL = 6 * 3600


def _parse_duration(text: str) -> int:
    """把「01:23」「1:02:03」解析成秒。"""
    total = 0
    try:
        for part in str(text or '').split(':'):
            total = total * 60 + int(part)
        return total
    except (TypeError, ValueError):
        return 0


async def _get_latest_via_bilibili_api(mid: int | str, max_retry: int = 2) -> dict | None:
    """（主路径）bilibili_api 的用户投稿接口，按发布时间取最新一条。

    - 自动带凭证；没有凭证时用带 buvid 的匿名凭证
    - 只取最新一页 5 条，避免拉全量
    """
    global _list_failure_streak
    if not str(mid).isdigit():
        logger.error('UP主UID无效（非数字）')
        return None

    credential = await BiliCredential.get_credential()
    last_error = '未知错误'

    for retry in range(max_retry + 1):
        try:
            api = user.User(int(mid), credential=credential)
            data = await api.get_videos(ps=5, pn=1, order=user.VideoOrder.PUBDATE)
            videos = ((data or {}).get('list') or {}).get('vlist') or []
            if not videos:
                if _list_failure_streak:
                    logger.info('UP主投稿接口已恢复，但返回空列表')
                _list_failure_streak = 0
                return None

            latest = max(videos, key=lambda item: int(item.get('created') or 0))
            if _list_failure_streak:
                logger.info(f'UP主投稿接口已恢复（之前连续失败 {_list_failure_streak} 次）')
            _list_failure_streak = 0
            return {
                'aid': str(latest.get('aid', '')),
                'bvid': latest.get('bvid', ''),
                'title': (latest.get('title') or '未知标题').strip(),
                'cover': latest.get('pic', ''),
                'duration': _parse_duration(latest.get('length')),
                'play_count': int(latest.get('play') or 0),
                'publish_time': int(latest.get('created') or 0),
                'create_time': int(latest.get('created') or 0),
            }
        except ResponseCodeException as error:
            last_error = f'接口返回 code={getattr(error, "code", "?")}（可能是风控）'
        except Exception as error:
            last_error = f'{type(error).__name__}: {error}'

        if retry < max_retry:
            await asyncio.sleep(1.5 * (retry + 1))

    _list_failure_streak += 1
    if _list_failure_streak == 1:
        logger.warning(f'获取UP主最新视频失败：{last_error}（后续同类失败不再重复提示）')
    else:
        logger.debug(f'获取UP主最新视频失败（连续 {_list_failure_streak} 次）：{last_error}')
    return None


async def _get_latest_via_uapis(mid: int | str, max_retry: int = 2) -> dict | None:
    """（兜底路径）uapis.cn 的 archives 接口，保留原有逻辑。

    ★ ps 固定 30：uapis 按 (ps, pn) 分键缓存且各键冻结时间不同，
      ps=1 会返回置顶/缓存的旧稿；取回后自行按 publish_time 取最大的一条。
    """
    if not str(mid).isdigit():
        logger.error('UP主UID无效（非数字）')
        return None

    headers = {'User-Agent': USER_AGENT}
    if UAPI_TOKEN:
        headers['Authorization'] = f'Bearer {UAPI_TOKEN}'

    for retry in range(max_retry + 1):
        try:
            async with AsyncClient(timeout=10) as client:
                response = await client.get(
                    UAPIS_ARCHIVES_URL,
                    params={'mid': str(mid), 'orderby': 'pubdate', 'ps': 30, 'pn': 1},
                    headers=headers,
                )
                if response.status_code != 200:
                    logger.debug(f'uapis 请求失败（{response.status_code}），重试 {retry}/{max_retry}')
                    if retry < max_retry:
                        await asyncio.sleep(1)
                    continue

                result = response.json() or {}
                videos = result.get('videos') or []
                if not videos:
                    return None
                video_data = max(videos, key=lambda item: int(item.get('publish_time') or 0))
                return {
                    'aid': str(video_data.get('aid', '')),
                    'bvid': video_data.get('bvid', ''),
                    'title': (video_data.get('title') or '未知标题').strip(),
                    'cover': video_data.get('cover', ''),
                    'duration': int(video_data.get('duration') or 0),
                    'play_count': int(video_data.get('play_count') or 0),
                    'publish_time': int(video_data.get('publish_time') or 0),
                    'create_time': int(video_data.get('create_time') or 0),
                }
        except Exception as error:
            logger.debug(f'uapis 请求异常（重试 {retry}/{max_retry}）：{error}')
        if retry < max_retry:
            await asyncio.sleep(1)
    return None


async def get_up_latest_video(mid: int | str, max_retry: int = 2) -> dict | None:
    """按配置选择数据源取 UP 主最新投稿，失败返回 None。

    ``BILI_LIST_SOURCE``：
      - ``bilibili_api``：只用官方接口
      - ``uapis``：只用第三方聚合接口（老行为）
      - ``auto``（默认）：先官方，失败且配了 uapis token 时自动回退
    """
    source = BILI_LIST_SOURCE
    if source == 'uapis':
        return await _get_latest_via_uapis(mid, max_retry)

    result = await _get_latest_via_bilibili_api(mid, max_retry)
    if result is not None or source == 'bilibili_api':
        return result

    if UAPI_TOKEN:
        logger.debug('官方接口取列表失败，回退 uapis')
        return await _get_latest_via_uapis(mid, max_retry)
    return None


async def get_up_nickname(uid: int | str) -> str:
    """获取 UP 主昵称，带 6 小时缓存；失败时沿用上次成功的名字。"""
    if not str(uid).isdigit():
        logger.warning(f'获取UP主昵称失败：UID非法（值：{uid}）')
        return '未知UP主'

    key = int(uid)
    cached = _nickname_cache.get(key)
    if cached and time.time() - cached[0] < NICKNAME_CACHE_TTL:
        return cached[1]

    try:
        credential = await BiliCredential.get_credential()
        info = await user.User(key, credential=credential).get_user_info()
        name = ((info or {}).get('name') or '').strip()
        if name:
            _nickname_cache[key] = (time.time(), name)
            return name
    except Exception as error:
        logger.debug(f'官方接口获取UP主昵称失败（UID:{uid}）：{error}')

    # 兜底：uapis 的 userinfo 接口
    if BILI_LIST_SOURCE != 'bilibili_api' and UAPI_TOKEN:
        try:
            async with AsyncClient(timeout=10, verify=False) as client:
                response = await client.get(
                    UAPIS_USERINFO_URL,
                    params={'uid': str(uid)},
                    headers={'User-Agent': USER_AGENT, 'Authorization': f'Bearer {UAPI_TOKEN}'},
                )
                if response.status_code == 200:
                    result = response.json() or {}
                    name = ''
                    if result.get('code') == 0 and 'data' in result:
                        name = (result['data'].get('name') or '').strip()
                    elif result.get('mid') == int(uid):
                        name = (result.get('name') or '').strip()
                    if name:
                        _nickname_cache[key] = (time.time(), name)
                        return name
        except Exception as error:
            logger.debug(f'uapis 获取昵称失败（UID:{uid}）：{error}')

    return cached[1] if cached else '未知UP主'


# 6. 视频快照生成
# 机房 IP 命中这些 PCDN 域名时经常 403/超时，优先换用备用地址
BLOCKED_PCDN_DOMAINS = (
    'mcdn.bilivideo.com',
    'mcdn.bilivideo.cn',
    'szbdyd.com',
    'mountaintoys.cn',
    'nexusedgeio.com',
    'ahdohpiechei.com',
)


def _is_pcdn_url(url: str) -> bool:
    """判断是否为被屏蔽的 PCDN 地址。"""
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or '').lower()
    except Exception:
        return False
    return any(host == domain or host.endswith('.' + domain) for domain in BLOCKED_PCDN_DOMAINS)


def _pick_stream_url(stream) -> str:
    """取视频流地址；命中 PCDN 黑名单时改用 backup_url 里的干净地址。"""
    urls = [stream.url] + list(getattr(stream, 'backup_url', None) or [])
    for url in urls:
        if url and not _is_pcdn_url(url):
            return url
    return stream.url or ''


async def download_bilibili_video(bv_id: str) -> Path | None:
    """
    下载 B 站视频为本地 mp4 文件，返回文件路径（失败返回 None）。

    B 站当前返回的是 MP4StreamDownloadURL（已合流的 mp4，自带音频），
    因此无需 ffmpeg 合流即可直接发送。

    文件名带时间戳：后台监控与手动 .bv 可能同时下载同一稿件，
    若共用 {bv_id}.mp4 会互相覆盖/删除。
    """
    stamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
    video_path = VIDEO_DIR / f'{bv_id}_{stamp}.mp4'

    if not is_valid_bvid(bv_id):
        logger.error(f'BV 号无效，无法下载：{bv_id!r}（BV 号不含小写 l / 大写 I，注意 1 与 l 易混）')
        return None

    try:
        credential = await BiliCredential.get_credential()
        v = video.Video(bvid=bv_id, credential=credential)
        video_info = await v.get_info()
        cid = video_info['pages'][0]['cid']
        download_data = await v.get_download_url(cid=cid, html5=True)

        # bilibili_api 不认 hvc1 前缀，先归一化成带 "hev" 标记的写法
        for video_data in (download_data.get('dash') or {}).get('video') or []:
            codecs = video_data.get('codecs', '')
            if isinstance(codecs, str) and codecs.startswith('hvc1'):
                video_data['codecs'] = f'hev,{codecs}'

        # 选择最佳视频流
        detecter = VideoDownloadURLDataDetecter(download_data)
        best_streams = detecter.detect_best_streams(
            video_max_quality=VideoQuality._720P,
            video_min_quality=VideoQuality._480P,
            codecs=[VideoCodecs.AVC],
        )

        if not best_streams or not hasattr(best_streams[0], 'url'):
            logger.error('未找到合适的视频流')
            return None

        stream_url = _pick_stream_url(best_streams[0])
        if not stream_url:
            logger.error('视频流地址为空')
            return None
        if not stream_url.startswith(('http://', 'https://')):
            stream_url = f'https:{stream_url}'

        # 流式下载；超过体积上限立即中断，不把磁盘写满
        async with AsyncClient(timeout=120, verify=False) as client, client.stream(
            'GET',
            stream_url,
            headers={'User-Agent': USER_AGENT, 'Referer': 'https://www.bilibili.com/'},
        ) as response:
            response.raise_for_status()
            declared_size = int(response.headers.get('content-length') or 0)
            if declared_size > BILI_VIDEO_MAX_BYTES:
                logger.warning(
                    f'视频 {bv_id} 体积 {declared_size / 1024 / 1024:.1f}MB 超过上限 '
                    f'{BILI_VIDEO_MAX_BYTES / 1024 / 1024:.0f}MB，跳过下载'
                )
                return None
            written = 0
            with open(video_path, 'wb') as file:
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > BILI_VIDEO_MAX_BYTES:
                        raise ValueError(
                            f'视频超过上限 {BILI_VIDEO_MAX_BYTES / 1024 / 1024:.0f}MB，已中断下载'
                        )
                    file.write(chunk)

        logger.info(f'视频下载完成：{video_path.name}（{video_path.stat().st_size / 1024 / 1024:.2f}MB）')
        return video_path
    except Exception as e:
        logger.error(f'下载视频失败：{e}')
        if video_path.exists():
            video_path.unlink(missing_ok=True)
        return None


# ===== ffmpeg：裁掉视频开头的黑场 =====
# QQ / NapCat 用视频第 0 帧当封面，而 B 站视频开头常有黑场淡入，
# 于是封面一片黑。OneBot 协议不支持自定义封面，只能把开头的黑场裁掉。
# ffmpeg 缺失或处理失败时全部优雅降级：直接发原片，不影响其它功能。

_ffmpeg_path: str | None = None
_ffmpeg_probed = False


def _find_binary_in(directory: Path, patterns: tuple[str, ...]) -> Path | None:
    """
    在目录下按模式查找可执行文件，找不到返回 None。

    不同来源的命名/目录结构都不一样，所以用多个模式覆盖：
      - BtbN 包：ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe
      - imageio-ffmpeg wheel：imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe
    """
    if not directory.is_dir():
        return None
    for pattern in patterns:
        try:
            for found in sorted(directory.rglob(pattern)):
                if found.is_file():
                    return found
        except OSError:
            continue
    return None


# Windows 下匹配 ffmpeg.exe 与 ffmpeg-win-x86_64-v7.1.exe；类 Unix 下匹配 ffmpeg
if os.name == 'nt':
    _FFMPEG_PATTERNS = ('ffmpeg.exe', 'ffmpeg-*.exe')
else:
    _FFMPEG_PATTERNS = ('ffmpeg', 'ffmpeg-*')


def _downloaded_ffmpeg_path() -> Path | None:
    """返回插件自下载的 ffmpeg 路径（存在才返回）。"""
    return _find_binary_in(FFMPEG_DIR, _FFMPEG_PATTERNS)


async def download_ffmpeg() -> str | None:
    """
    自动下载 ffmpeg 静态构建并解压到 Temp/ffmpeg/。

    只在系统里确实找不到 ffmpeg 时才会被调用一次。下载失败只告警，
    不影响任何其它功能（黑场裁切自动跳过，视频照常发送）。
    """
    archive_path = FFMPEG_DIR.parent / 'ffmpeg-download.zip'
    errors: list[str] = []
    try:
        FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
        logger.info('未找到 ffmpeg，正在自动下载（约 30MB，仅需一次）')
        downloaded = False
        for url in FFMPEG_DOWNLOAD_URLS:
            try:
                written = await _download_archive(url, archive_path)
                logger.info(f'ffmpeg 下载完成（{written / 1024 / 1024:.1f}MB），正在解压...')
                downloaded = True
                break
            except Exception as error:
                errors.append(f'{url.split("/")[2]}: {error}')
                logger.warning(f'从 {url} 下载失败，尝试下一个源：{error}')
        if not downloaded:
            logger.warning(f'ffmpeg 全部下载源均失败：{errors}（黑场裁切将跳过，不影响视频发送）')
            return None

        def _extract() -> None:
            import zipfile

            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(FFMPEG_DIR)

        await asyncio.to_thread(_extract)
    except Exception as error:
        logger.warning(f'ffmpeg 自动下载失败：{error}（黑场裁切将跳过，不影响视频发送）')
        return None
    finally:
        archive_path.unlink(missing_ok=True)

    found = _downloaded_ffmpeg_path()
    if found is None:
        logger.warning('ffmpeg 解压后未找到可执行文件，黑场裁切将跳过')
        return None
    logger.success(f'ffmpeg 已就绪：{found}')
    return str(found)


async def _download_archive(url: str, archive_path: Path) -> int:
    """下载压缩包到 archive_path，返回写入字节数。"""
    # verify=False：部分机器的根证书缺失或系统时间偏差会导致
    # CERTIFICATE_VERIFY_FAILED，而这里下载的是官方发布的静态构建，
    # 与本项目下载 B 站视频流的做法一致。
    async with AsyncClient(timeout=900, follow_redirects=True, verify=False) as client, client.stream(
        'GET',
        url,
        headers={'User-Agent': USER_AGENT},
    ) as response:
        response.raise_for_status()
        written = 0
        with open(archive_path, 'wb') as file:
            async for chunk in response.aiter_bytes():
                if chunk:
                    written += len(chunk)
                    file.write(chunk)
    if written < 1024 * 1024:
        raise ValueError(f'下载内容过小（{written} 字节），疑似失败')
    return written


def _resolve_ffmpeg_path() -> str | None:
    """按优先级同步解析 ffmpeg 路径（不含自动下载）。"""
    candidates = [
        FFMPEG_CONFIGURED_PATH,
        (os.environ.get('FFMPEG_PATH') or '').strip(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        # 允许配置到目录（自动补 ffmpeg.exe）
        if path.is_dir():
            path = path / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
        if path.is_file():
            logger.info(f'ffmpeg 已启用：{path}')
            return str(path)
        logger.warning(f'配置的 ffmpeg 路径不存在：{candidate}')

    found = shutil.which('ffmpeg')
    if found:
        logger.info(f'ffmpeg 已启用（来自 PATH）：{found}')
        return found

    cached = _downloaded_ffmpeg_path()
    if cached is not None:
        logger.info(f'ffmpeg 已启用（插件自带）：{cached}')
        return str(cached)

    return None


async def ensure_ffmpeg() -> str | None:
    """
    确保 ffmpeg 可用，返回其路径（不可用返回 None）。

    解析顺序：bili_ffmpeg_path -> 环境变量 FFMPEG_PATH -> 系统 PATH
              -> 插件自下载（Temp/ffmpeg/，仅在开启自动下载时）。
    """
    global _ffmpeg_path, _ffmpeg_probed
    if _ffmpeg_probed:
        return _ffmpeg_path

    resolved = _resolve_ffmpeg_path()
    if resolved is None and FFMPEG_AUTO_DOWNLOAD:
        resolved = await download_ffmpeg()

    _ffmpeg_path = resolved
    _ffmpeg_probed = True
    if resolved is None:
        logger.info(
            '未找到 ffmpeg，视频开头黑场裁切已跳过。'
            '可配置 BILI_FFMPEG_PATH 指定路径，或开启 BILI_FFMPEG_AUTO_DOWNLOAD 自动下载。'
        )
    return _ffmpeg_path


def get_ffmpeg_path() -> str | None:
    """
    读取已解析的 ffmpeg 路径（同步、无副作用）。

    真正的解析与自动下载由 `ensure_ffmpeg()` 完成；这里只返回缓存结果，
    避免在事件循环里触发同步阻塞。
    """
    return _ffmpeg_path


def build_ffmpeg_args(*args: str) -> list[str]:
    """构造 ffmpeg 调用参数（保留占位以便扩展）。"""
    ffmpeg = get_ffmpeg_path()
    if ffmpeg is None:
        raise RuntimeError('ffmpeg not available')
    return [ffmpeg, *args]


async def run_ffmpeg(args: list[str], *, timeout: float = 300.0) -> tuple[int, str]:
    """执行 ffmpeg/ffprobe，返回 (返回码, 合并后的输出)。"""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return -1, f'超时（>{timeout:.0f}s）'
    return process.returncode or 0, (stdout or b'').decode('utf-8', errors='replace')


async def probe_video_duration(video_path: Path) -> float | None:
    """
    读取视频时长（秒），失败返回 None。

    刻意不依赖 ffprobe（部分 ffmpeg 发行版不含它），改用 ffmpeg 自身输出里的
    `Duration: HH:MM:SS.ms` 行——只读元数据不转码，因此很快。
    """
    ffmpeg = get_ffmpeg_path()
    if ffmpeg is None:
        return None
    code, output = await run_ffmpeg(
        [ffmpeg, '-hide_banner', '-i', str(video_path)],
        timeout=60.0,
    )
    # ffmpeg 对「只给 -i 不给输出」会以非 0 退出，属正常，仍继续解析输出
    match = re.search(r'Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)', output)
    if match is None:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


async def detect_leading_black_seconds(video_path: Path) -> float | None:
    """
    探测视频**开头**黑场的时长（秒）。

    用 ffmpeg 的 blackdetect 滤镜；只取第一个从 0 秒开始的 black_start，
    避免把视频中间的黑场也算进去。没有黑场时返回 0.0。
    ffmpeg 不可用或探测失败一律返回 None（调用方跳过裁切）。
    """
    if get_ffmpeg_path() is None:
        return None
    try:
        args = build_ffmpeg_args(
            '-hide_banner',
            '-nostats',
            '-i', str(video_path),
            '-vf',
            f'blackdetect=d={BLACK_MIN_DURATION}:pic_th=0.98:pix_th={BLACK_PIXEL_THRESHOLD}',
            '-an',
            '-f', 'null',
            '-',
        )
    except RuntimeError:
        return None
    code, output = await run_ffmpeg(args, timeout=120.0)
    if code != 0:
        return None
    # 典型输出：black_start:0 black_end:0.5 black_duration:0.5
    match = re.search(
        r'black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)',
        output,
    )
    if match is None:
        return 0.0
    start, end = float(match.group(1)), float(match.group(2))
    # 只认从视频开头就开始的黑场
    if start > 0.5:
        return 0.0
    return end


async def trim_leading_black(video_path: Path) -> Path | None:
    """裁掉视频开头的黑场并重编码，返回新文件路径；无需处理或失败时返回 None。"""
    if get_ffmpeg_path() is None:
        return None
    duration = await probe_video_duration(video_path)
    if duration is not None and duration > MAX_PROCESS_SECONDS:
        logger.info(
            f'视频时长 {duration / 60:.1f} 分钟超过阈值 {MAX_PROCESS_SECONDS / 60:.0f} 分钟，跳过黑场裁切'
        )
        return None

    black_seconds = await detect_leading_black_seconds(video_path)
    if black_seconds is None:
        logger.warning('黑场探测失败，跳过黑场裁切')
        return None
    if black_seconds < 0.05:
        logger.info('未检测到开头黑场，保持原片')
        return None
    if black_seconds > MAX_TRIM_SECONDS:
        logger.warning(
            f'开头黑场达 {black_seconds:.1f}s，超过上限 {MAX_TRIM_SECONDS:.0f}s，疑似误判，跳过裁切'
        )
        return None

    trimmed_path = video_path.with_name(f'{video_path.stem}-trimmed{video_path.suffix}')
    logger.info(f'裁掉开头 {black_seconds:.2f}s 黑场并重编码：{video_path.name}')
    code, output = await run_ffmpeg(
        build_ffmpeg_args(
            '-hide_banner',
            '-nostats',
            '-y',
            '-ss', f'{black_seconds:.3f}',
            '-i', str(video_path),
            # 画面重编码（黑场裁切需要重编码）；音频能直通就直通
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-crf', '26',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'copy',
            '-movflags', '+faststart',
            str(trimmed_path),
        ),
        timeout=600.0,
    )
    if code != 0:
        logger.warning(f'ffmpeg 裁切失败（返回码 {code}），改用原片：{output[-300:]}')
        trimmed_path.unlink(missing_ok=True)
        return None
    if not trimmed_path.exists() or trimmed_path.stat().st_size == 0:
        logger.warning('ffmpeg 未产出有效文件，改用原片')
        trimmed_path.unlink(missing_ok=True)
        return None

    # 只有比原片小时才采用（重编码不可控，可能反而变大）
    if trimmed_path.stat().st_size >= video_path.stat().st_size:
        logger.info('裁切后体积未减小，保留原片')
        trimmed_path.unlink(missing_ok=True)
        return None

    logger.success(
        f'黑场裁切完成：{trimmed_path.name}'
        f'（{trimmed_path.stat().st_size / 1024 / 1024:.2f}MB）'
    )
    return trimmed_path


async def normalize_video_for_qq(video_path: Path) -> Path:
    """把视频重编码成 QQ 群视频更容易接受的标准格式。

    B 站直链常见 H.264 High@5.1 + AAC 44.1kHz，部分客户端/服务端在处理
    「视频消息」时会重新转码并丢掉音轨；这里统一成 Main@3.1 + AAC 48kHz。
    任何失败都返回原片，绝不影响发送。
    """
    if not BILI_VIDEO_NORMALIZE:
        return video_path
    ffmpeg = await ensure_ffmpeg()
    if ffmpeg is None:
        return video_path

    duration = await probe_video_duration(video_path)
    if duration is not None and duration > BILI_VIDEO_NORMALIZE_MAX_SECONDS:
        logger.info(
            f'视频时长 {duration / 60:.1f} 分钟超过规范化上限 '
            f'{BILI_VIDEO_NORMALIZE_MAX_SECONDS / 60:.0f} 分钟，跳过重编码'
        )
        return video_path

    normalized = video_path.with_name(f'{video_path.stem}-qq.mp4')
    logger.info(f'正在把视频重编码为 QQ 友好格式：{video_path.name}')
    code, output = await run_ffmpeg(
        [
            ffmpeg, '-hide_banner', '-nostats', '-y',
            '-i', str(video_path),
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
            '-profile:v', 'main', '-level', '3.1', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-ac', '2',
            '-movflags', '+faststart',
            str(normalized),
        ],
        timeout=900.0,
    )
    if code != 0 or not normalized.exists() or normalized.stat().st_size == 0:
        logger.warning(f'规范化转码失败（返回码 {code}），改用原片：{output[-300:]}')
        normalized.unlink(missing_ok=True)
        return video_path

    logger.success(
        f'规范化完成：{normalized.name}'
        f'（{normalized.stat().st_size / 1024 / 1024:.2f}MB）'
    )
    video_path.unlink(missing_ok=True)
    return normalized


async def process_video_or_original(video_path: Path) -> Path:
    """
    对下载好的视频做「开头黑场裁切」后处理。

    任何异常都吞掉并返回原路径——后处理失败绝不能影响正常发送。
    ffmpeg 缺失时（若开启自动下载）会在第一次调用时后台下载一份。
    """
    if await ensure_ffmpeg() is None:
        return video_path

    # 1) 裁掉开头黑场（可关闭）
    if TRIM_BLACK_ENABLED:
        try:
            trimmed = await trim_leading_black(video_path)
        except Exception as error:
            logger.warning(f'黑场裁切异常，改用原片：{error}')
            trimmed = None
        if trimmed is not None:
            # 采用裁切结果，删除原片
            video_path.unlink(missing_ok=True)
            video_path = trimmed

    # 2) 统一重编码成 QQ 友好格式（失败自动回退原片）
    try:
        return await normalize_video_for_qq(video_path)
    except Exception as error:
        logger.warning(f'规范化转码异常，改用原片：{error}')
        return video_path


async def generate_video_frame(video_path: Path, frame_path: Path) -> Path | None:
    """
    从本地视频按 BILI_FRAME_SEC 截取一帧保存为 PNG，返回路径（失败返回 None）。

    用 ffmpeg（不再依赖 opencv）；走 run_ffmpeg 异步子进程，
    避免同步 subprocess 阻塞事件循环导致机器人整体假死。
    """
    global _ffmpeg_path
    ffmpeg = get_ffmpeg_path() or _resolve_ffmpeg_path()
    if ffmpeg is None:
        logger.error('未找到 ffmpeg，无法生成视频快照（请确认容器里装了 ffmpeg）')
        return None
    _ffmpeg_path = ffmpeg

    frame_path.unlink(missing_ok=True)
    # 依次尝试：配置秒数 -> 稍早 0.5 秒 -> 视频开头
    for position in (BILI_FRAME_SEC, max(BILI_FRAME_SEC - 0.5, 0.0), 0.0):
        code, output = await run_ffmpeg(
            [
                ffmpeg, '-hide_banner', '-loglevel', 'error',
                '-ss', f'{position:.3f}',
                '-i', str(video_path),
                '-frames:v', '1', '-y', str(frame_path),
            ],
            timeout=30.0,
        )
        if code == 0 and frame_path.exists() and frame_path.stat().st_size > 0:
            logger.info(f'视频快照生成成功：{frame_path}')
            return frame_path
        logger.debug(f'ffmpeg 抽帧未成功（-ss {position:.3f}，返回码 {code}）：{output[-200:]}')
    logger.error('未能提取到有效视频帧')
    return None


async def get_video_frame_by_VSDU_async(video_url: str) -> Path | None:
    """按视频链接生成快照 PNG（下载临时视频 → 截帧 → 清理临时视频）。"""
    bv_match = BV_PATTERN.search(video_url)
    if not bv_match:
        logger.error('视频链接中未提取到BV号')
        return None
    bv_id = bv_match.group()
    frame_path = FRAME_DIR / f'{bv_id}.png'
    if frame_path.exists():
        frame_path.unlink(missing_ok=True)

    video_path = await download_bilibili_video(bv_id)
    if video_path is None:
        return None
    try:
        return await generate_video_frame(video_path, frame_path)
    except Exception as e:
        logger.error(f'生成视频快照失败：{e}')
        return None
    finally:
        # 只清理本次下载的文件（文件名唯一，不会误删其他任务的）
        if video_path.exists():
            video_path.unlink(missing_ok=True)
            logger.debug(f'临时视频文件已清理：{video_path.name}')

def _get_onebot_bot():
    """
    取出 OneBot V11 的 bot 实例。

    ⚠️ UniBot 同时挂着 Minecraft 适配器，get_bots() 会把两个 bot 都返回。
    直接 list(...)[0] 有几率拿到 Minecraft bot —— 它没有 send_group_msg，
    会抛 ApiNotAvailable()（异常消息为空，极难排查），
    导致「视频推送失败退回快照」并且连文字也发不出去。
    """
    for bot in get_bots().values():
        if isinstance(bot, Bot):
            return bot
    return None


async def _push_to_groups(message: Message, title: str, bvid: str, max_retry: int, retry_interval: int) -> bool:
    """把消息推送到全部群，带重试（至少一个群成功即视为成功）。"""
    retry_count = 0
    push_success = False
    while retry_count < max_retry and not push_success:
        try:
            bot = _get_onebot_bot()
            if bot is None:
                raise RuntimeError('暂无可用的 OneBot Bot 连接，请检查 Bot 是否已上线')
            results = await asyncio.gather(
                *[asyncio.create_task(_send_to_group(bot, group_info, message, title, bvid))
                  for group_info in MESSAGE_GROUPS],
                return_exceptions=True,
            )
            failed_groups = []
            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    failed_groups.append((MESSAGE_GROUPS[idx], str(result)))
                else:
                    push_success = True
            if failed_groups:
                logger.warning(f'部分群推送失败（重试{retry_count + 1}/{max_retry}）：{failed_groups}')
                retry_count += 1
                if retry_count < max_retry:
                    logger.info(f'{retry_interval}秒后进行第{retry_count + 1}次重试...')
                    await asyncio.sleep(retry_interval)
            else:
                push_success = True
        except Exception as e:
            logger.error(f'推送失败（重试{retry_count + 1}/{max_retry}）：{e}')
            retry_count += 1
            if retry_count < max_retry:
                logger.info(f'{retry_interval}秒后进行第{retry_count + 1}次重试...')
                await asyncio.sleep(retry_interval)
    return push_success


# 7. 推送新视频到指定群（加锁防重复+异步优化）
async def push_new_video(video_data: dict, max_retry: int = 3, retry_interval: int = 10):
    """
    推送新视频，加锁防止重复推送，失败自动重试3次（间隔10秒）。

    优先把视频本体发到群里；视频超过 BILI_VIDEO_MAX_MB 上限、下载失败
    或发送失败时，自动退回「只发快照」并在文案中说明原因。
    """
    async with PUSH_LOCK:
        bvid = video_data.get('bvid', '')
        title = video_data.get('title', '未知标题')
        video_url = f'https://www.bilibili.com/video/{bvid}/'

        logger.info(f'开始推送新视频：{title} ({bvid})')

        # 1. 先获取UP主昵称
        up_nickname = await get_up_nickname(BILI_UP_UID)

        # 2. 下载视频（超过体积上限时 download_bilibili_video 返回 None）
        bv_match = BV_PATTERN.search(video_url)
        video_path = await download_bilibili_video(bv_match.group()) if bv_match else None
        # 2b. 裁掉开头黑场（QQ/NapCat 抽第 0 帧当封面，黑场会导致封面全黑）
        if video_path is not None:
            video_path = await process_video_or_original(video_path)

        # 2c. 重编码后体积不可控，再校验一次上限；超了就当没有视频
        if video_path is not None:
            size_bytes = video_path.stat().st_size
            if size_bytes > BILI_VIDEO_MAX_BYTES:
                logger.warning(
                    f'处理后视频 {size_bytes / 1024 / 1024:.1f}MB 超过 '
                    f'{BILI_VIDEO_MAX_MB}MB 上限，退回只发快照'
                )
                video_path.unlink(missing_ok=True)
                video_path = None

        # 3. 尝试把视频本体发到所有群
        # 文件名需要「B站原标题」，push_new_video 的调用方（后台监控 uapis 接口）不带标题，
        # 这里按 bvid 补一次；取不到时回退为 BV 号，不影响发送。
        video_title = await get_video_title(bvid) if bvid else bvid
        video_sent = False
        if video_path is not None:
            bot = _get_onebot_bot()
            if bot is not None:
                try:
                    for group_info in MESSAGE_GROUPS:
                        await _send_video_to_group(bot, group_info, video_path, video_title, bvid)
                    video_sent = True
                except Exception as error:
                    logger.error(f'视频推送失败，本次退回只发快照：{error}')
        if video_path is not None and video_path.exists():
            video_path.unlink(missing_ok=True)

        # 4. 组装通知文案
        msg = Message()
        msg += MessageSegment.text(f'订阅的UP主【{up_nickname}】动态更新啦！\n')
        msg += MessageSegment.text(f'标题：{title}\n')

        if video_sent:
            msg += MessageSegment.text(f'链接：{video_url}')
            push_success = await _push_to_groups(msg, title, bvid, max_retry, retry_interval)
            if push_success:
                logger.success(f'新视频推送完成（含视频）：{title} ({bvid})')
            return

        # 5. 退回只发快照，并说明原因
        if video_path is None:
            reason = f'视频超过 {BILI_VIDEO_MAX_MB}MB 上限或下载失败'
        else:
            reason = '视频发送失败'
        frame_path = await get_video_frame_by_VSDU_async(video_url)
        if frame_path and frame_path.exists():
            msg += MessageSegment.image(frame_path.read_bytes())
            msg += MessageSegment.text('\n')
        msg += MessageSegment.text(f'（{reason}，本次仅发送快照）\n')
        msg += MessageSegment.text(f'链接：{video_url}')

        push_success = await _push_to_groups(msg, title, bvid, max_retry, retry_interval)
        if push_success:
            logger.success(f'新视频推送完成（仅快照）：{title} ({bvid})')
        else:
            logger.error(f'新视频推送失败（已重试{max_retry}次）：{title} ({bvid})')
            logger.info('将等待下一次监控周期再尝试推送')

async def _send_to_group(bot, group_info: str, msg: Message, title: str, bvid: str):
    """内部函数：单独推送单个群，方便异步处理

    MESSAGE_GROUPS 里的元素形如 "qq_client:<群号>"（带平台前缀），
    而 OneBot 的 send_group_msg 只接受纯数字 group_id，必须先把前缀切掉，
    否则会因参数非法抛 ApiNotAvailable()。

    失败时直接抛出，交由 push_new_video 统一判断与重试（不要在这里吞掉异常，
    否则调用方拿不到失败信号，重试逻辑会失效）。
    """
    platform, separator, group_id = group_info.partition(':')
    if not separator or not group_id:
        raise ValueError(f'群配置格式应为「平台:群号」，实际为 {group_info!r}')
    if platform != 'qq_client':
        raise ValueError(f'Bili 插件目前仅支持 qq_client 平台，不支持 {platform}')
    await bot.send_group_msg(group_id=group_id, message=msg)
    logger.info(f'推送至群 {group_info} 成功：{title} ({bvid})')


def is_valid_bvid(bv_id: str) -> bool:
    """
    校验 BV 号是否可能有效。

    B 站 BV 号使用固定字符表，其中不含小写 l、大写 I 等易混字符。
    传错一个字（例如把末尾的 1 看成 l）时 bilibili_api 内部会抛出
    `ValueError: b'l' is not in list`，这里提前拦掉并给出可读提示。
    """
    if not bv_id or not bv_id.startswith('BV'):
        return False
    try:
        bvid2aid(bv_id)
        return True
    except Exception:
        return False


async def get_video_title(bv_id: str) -> str:
    """获取稿件的原始标题（用于组成文件名），失败时回退为 BV 号。"""
    if not is_valid_bvid(bv_id):
        return bv_id
    try:
        info = await video.Video(bvid=bv_id, credential=await BiliCredential.get_credential()).get_info()
        return info.get('title') or bv_id
    except Exception as error:
        logger.warning(f'获取视频标题失败，文件名将使用 BV 号：{error}')
        return bv_id


def _build_named_video(video_path: Path, title: str, bvid: str) -> Path:
    """把下载的临时视频重命名为「<日期>-每日新闻-<B站原标题>.mp4」，返回新路径。"""
    date_prefix = datetime.now().strftime('%Y-%m-%d')
    # 去掉文件名非法字符（Windows 下会重命名失败）
    safe_title = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', title).strip(' .') or bvid
    # 标题过长会超出文件系统上限，截断到 80 字符
    named_path = VIDEO_DIR / f'{date_prefix}-每日新闻-{safe_title[:80]}.mp4'
    if named_path.resolve() != video_path.resolve():
        if named_path.exists():
            named_path.unlink(missing_ok=True)
        video_path.replace(named_path)
    return named_path


async def _send_video_to_group(bot, group_info: str, video_path: Path, title: str, bvid: str) -> None:
    """把本地视频作为群视频发送到 MESSAGE_GROUPS 里的某个群。

    文件名重命名为「<日期>-每日新闻-<B站原标题>.mp4」后上传（NapCat 支持 file:// 本地路径）。
    失败时向上抛出，交由调用方退回「只发快照」。
    """
    platform, separator, group_id = group_info.partition(':')
    if not separator or not group_id:
        raise ValueError(f'群配置格式应为「平台:群号」，实际为 {group_info!r}')
    if platform != 'qq_client':
        raise ValueError(f'Bili 插件目前仅支持 qq_client 平台，不支持 {platform}')

    named_path = _build_named_video(video_path, title, bvid)
    size_mb = named_path.stat().st_size / 1024 / 1024
    logger.info(f'正在向群 {group_info} 发送视频：{named_path.name}（{size_mb:.2f}MB）')
    try:
        if BILI_VIDEO_SEND_MODE == 'file':
            # 群文件方式：音轨一定保留（代价是对方要点开下载）
            await bot.call_api(
                'upload_group_file',
                group_id=int(group_id),
                file=str(named_path.absolute()),
                name=named_path.name,
            )
        else:
            # 必须包成 Message：OneBot 的 send_group_msg 只接受 str/Message，
            # 直接传裸 MessageSegment 会被适配器拒绝
            await bot.send_group_msg(
                group_id=group_id,
                message=Message(MessageSegment.video(named_path.absolute().as_uri())),
            )
        logger.info(f'视频已推送至群 {group_info}：{named_path.name}（方式：{BILI_VIDEO_SEND_MODE}）')
    finally:
        # 发送完毕即删除，避免 Temp 目录堆积大文件
        named_path.unlink(missing_ok=True)


async def _send_video_to_current_group(bot, group_id: int, video_path: Path, title: str, bvid: str = '') -> None:
    """把本地视频发送到指令所在的群（.bv 用）。

    同样按「<日期>-每日新闻-<B站原标题>.mp4」命名；失败时向上抛出，调用方退回发快照。
    """
    named_path = _build_named_video(video_path, title, bvid)
    size_mb = named_path.stat().st_size / 1024 / 1024
    logger.info(f'正在向群 {group_id} 发送视频：{named_path.name}（{size_mb:.2f}MB）')
    try:
        if BILI_VIDEO_SEND_MODE == 'file':
            await bot.call_api(
                'upload_group_file',
                group_id=int(group_id),
                file=str(named_path.absolute()),
                name=named_path.name,
            )
        else:
            await bot.send_group_msg(
                group_id=str(group_id),
                message=Message(MessageSegment.video(named_path.absolute().as_uri())),
            )
        logger.info(f'视频已推送至群 {group_id}：{named_path.name}（方式：{BILI_VIDEO_SEND_MODE}）')
    finally:
        named_path.unlink(missing_ok=True)

# 8. 清理过期临时文件
async def clean_expired_temp_files():
    """定期清理过期的临时文件（帧文件 + 视频成品）"""
    try:
        now = datetime.now()
        expired_time = now - timedelta(days=TEMP_FILE_EXPIRE_DAYS)
        
        # 清理帧文件
        for frame_file in FRAME_DIR.glob('*.png'):
            file_mtime = datetime.fromtimestamp(frame_file.stat().st_mtime)
            if file_mtime < expired_time:
                frame_file.unlink(missing_ok=True)
                logger.debug(f'清理过期帧文件：{frame_file}')

        # 清理视频成品（含 -trimmed 变体）：正常流程发完即删，
        # 这里兜底清理中途崩溃/发送失败遗留的大文件。
        video_expired = now - timedelta(days=BILI_VIDEO_KEEP_DAYS)
        freed = 0.0
        for video_file in VIDEO_DIR.glob('*.mp4'):
            try:
                file_mtime = datetime.fromtimestamp(video_file.stat().st_mtime)
            except OSError:
                continue
            if file_mtime < video_expired:
                freed += video_file.stat().st_size / 1024 / 1024
                video_file.unlink(missing_ok=True)
                logger.debug(f'清理过期视频文件：{video_file.name}')
        if freed:
            logger.info(f'已清理过期视频文件，释放 {freed:.1f}MB')
        
        logger.debug('过期临时文件清理完成')
    except Exception as e:
        logger.error(f'清理过期临时文件失败：{e}')

# ====================== 核心监控逻辑 ======================
async def monitor_up_new_video():
    """优化版监控逻辑：防重复、稳容错、带清理"""
    if not BILI_WATCHER_ENABLED:
        logger.warning('B站UP监控功能未启用（配置不完整）')
        return

    logger.success(
        f'B站UP监控已启动\n'
        f'├── 监控UP主UID：{BILI_UP_UID}\n'
        f'├── 监控间隔：{BILI_UP_WAITSEC}秒\n'
        f'├── 推送群：{", ".join(MESSAGE_GROUPS)}（共 {len(MESSAGE_GROUPS)} 个）\n'
        f'├── 截取秒数：{BILI_FRAME_SEC}秒\n'
        f'├── 缓存文件：{CACHE_FILE.absolute()}\n'
        f'└── 临时文件目录：{VIDEO_DIR.absolute()}'
    )

    # 首次启动清理一次过期文件
    await clean_expired_temp_files()
    
    # 计数器：每24小时清理一次过期文件
    clean_counter = 0
    clean_interval = int(86400 / BILI_UP_WAITSEC)  # 24小时对应的轮询次数

    while True:
        try:
            # 1. 读取缓存
            last_cache = read_video_cache()
            
            # 2. 获取最新视频
            latest_video = await get_up_latest_video(BILI_UP_UID)
            if not latest_video:
                logger.info('未获取到UP主最新视频，持续轮询中')
                await asyncio.sleep(BILI_UP_WAITSEC)
                clean_counter += 1
                continue

            # 3. 提取核心信息
            current_timestamp = int(latest_video.get('publish_time', 0))
            current_title = latest_video.get('title', '')
            current_bvid = latest_video.get('bvid', '')
            
            # 4. 防重复推送（以发布时间戳为准，BV 号只用于「同时间戳删稿重发」）
            is_new_video = False
            if current_timestamp > last_cache['timestamp']:
                is_new_video = True
            elif (
                current_timestamp == last_cache['timestamp']
                and current_bvid
                and current_bvid != last_cache['bvid']
            ):
                # 时间戳相同但BV号不同（UP主删重发）
                is_new_video = True
                logger.warning(f"检测到同时间戳不同BV号：{current_bvid}（原：{last_cache['bvid']}）")
            elif current_bvid and current_bvid != last_cache['bvid']:
                # 接口返回了比缓存更旧的稿件：忽略，并且绝不把缓存回退到旧稿，
                # 否则真正的更新稿件会被反复判定为「新视频」而循环推送。
                logger.warning(
                    f'接口返回的稿件比缓存更旧，已忽略：{current_title}（{current_bvid}，'
                    f'时间戳：{current_timestamp} < 缓存 {last_cache["timestamp"]}）'
                )

            # 4b. 首次运行（无缓存）时，过于陈旧的稿件只写缓存、不推送
            if is_new_video and not last_cache['timestamp']:
                age_days = (datetime.now().timestamp() - current_timestamp) / 86400
                if age_days > FIRST_PUSH_MAX_AGE_DAYS:
                    logger.warning(
                        f'首次运行（无缓存）且该稿件已发布 {age_days:.1f} 天，跳过推送，仅写入缓存'
                    )
                    is_new_video = False
                    write_video_cache({
                        'timestamp': current_timestamp,
                        'title': current_title,
                        'bvid': current_bvid,
                    })

            # 5. 处理新视频
            if is_new_video:
                logger.success(f'发现新视频：{current_title} (BV：{current_bvid}，时间戳：{current_timestamp})')
                # 先推送，后更新缓存
                await push_new_video(latest_video)
                # 更新缓存
                new_cache = {
                    'timestamp': current_timestamp,
                    'title': current_title,
                    'bvid': current_bvid
                }
                write_video_cache(new_cache)
            else:
                logger.debug(f"无新视频，当前最新：{last_cache['title']} (BV：{last_cache['bvid']})")

            # 6. 定期清理过期文件
            clean_counter += 1
            if clean_counter >= clean_interval:
                await clean_expired_temp_files()
                clean_counter = 0

        except Exception as e:
            logger.error(f'监控循环异常：{e}')
            # 异常时延长休眠时间，避免高频报错
            await asyncio.sleep(min(BILI_UP_WAITSEC * 2, 300))
            continue

        # 正常休眠
        await asyncio.sleep(BILI_UP_WAITSEC)

# ====================== 扫码登录 ======================
# 二维码只写文件 + 打印到控制台；群聊绝不发图（谁都能扫），私聊才直接发
_login_task: asyncio.Task | None = None
# 登录二维码放 Data/Bili（和 credential.json 同目录，Data 是挂载出来的，宿主机上直接可见）。
# 注意不能用 TEMP_ROOT —— 那是给视频/图片用的 NapCat 共享目录，二维码不经过 NapCat。
QR_PATH = Path('Data/Bili') / 'login_qr.png'


async def _notify_group(group_id: int | None, text: str) -> None:
    """往指定群发一条纯文本通知（失败只记日志）。"""
    if not group_id:
        return
    bot = _get_onebot_bot()
    if bot is None:
        return
    try:
        await bot.send_group_msg(group_id=group_id, message=Message(text))
    except Exception as error:
        logger.warning(f'登录通知发送失败：{error}')


async def _wait_scan_login(key: str, group_id: int | None) -> None:
    """后台轮询扫码状态。过期时静默结束，只在控制台记一行。"""
    deadline = time.monotonic() + BiliCredential.QR_TTL_SECONDS
    while time.monotonic() < deadline:
        try:
            status, credential = await BiliCredential.poll_login_qr(key)
        except Exception as error:
            logger.debug(f'扫码轮询异常：{error}')
            status, credential = 'waiting', None

        if status == 'success' and credential is not None:
            nickname = '未知账号'
            try:
                info = await user.User(int(credential.dedeuserid), credential=credential).get_user_info()
                nickname = (info or {}).get('name') or nickname
            except Exception:
                pass
            logger.success(f'B站登录成功，凭证已保存（账号：{nickname}）')
            await _notify_group(group_id, f'B站登录成功，凭证已保存（账号：{nickname}）')
            QR_PATH.unlink(missing_ok=True)
            return

        if status == 'expired':
            break
        await asyncio.sleep(2)

    QR_PATH.unlink(missing_ok=True)
    logger.info('B站登录二维码已过期，本次扫码结束（不会在群里提示）')


bili_login = on_command(
    'bililogin',
    aliases={'blogin', 'bilogin'},
    block=True,
    priority=0,
)


@bili_login.handle()
async def handle_bili_login(bot: Bot, event: MessageEvent):
    """生成 B 站扫码登录二维码：写文件 + 控制台 ASCII，群里只回提示。

    群聊里绝不发送图片（谁都能扫）；私聊时直接把二维码发给发指令的人。
    """
    global _login_task
    if not await SUPERUSER(bot, event):
        await bili_login.finish('该指令需要超级用户权限（SUPERUSERS 配置）。')
    if _login_task and not _login_task.done():
        await bili_login.finish('已有一次扫码登录在进行中，请先完成或等它过期。')

    try:
        url, key = await BiliCredential.generate_login_qr()
    except Exception as error:
        await bili_login.finish(f'生成登录二维码失败：{error}')

    qr_path = QR_PATH
    qr_path.parent.mkdir(parents=True, exist_ok=True)
    qr_bytes = BiliCredential.render_qr_png(url)
    qr_path.write_bytes(qr_bytes)
    # 直接 print，绕过 loguru 的前缀，否则 ASCII 图案会被时间戳打断
    BiliCredential.print_qr_ascii(url)
    logger.info(f'B站登录二维码已生成：{qr_path.absolute()}')

    if isinstance(event, PrivateMessageEvent):
        # 私聊只有发指令的人能看到，直接发图最省事
        group_id = None
        _login_task = asyncio.create_task(_wait_scan_login(key, None))
        await bili_login.finish(
            Message(
                MessageSegment.image(qr_bytes)
                + MessageSegment.text(f'\n请用 B 站 App 扫码，有效期 {BiliCredential.QR_TTL_SECONDS} 秒。')
            )
        )

    group_id = event.group_id if isinstance(event, GroupMessageEvent) else None
    _login_task = asyncio.create_task(_wait_scan_login(key, group_id))
    await bili_login.finish(
        f'登录二维码已生成，有效期 {BiliCredential.QR_TTL_SECONDS} 秒。\n'
        f'服务器路径：{qr_path.absolute()}\n'
        f'（也可以私聊机器人发 /bililogin，直接把二维码发给你）'
    )



# ====================== 指令处理器======================
# 1. 手动触发监控
# 注意 priority=0：Scripts.Plugins.Events 在 priority 1 以 block=True 截断事件，
# 本文件原先使用默认 priority=1，会导致这些指令永远不会被评估。
manual_watch = on_command('biliwatch', permission=SUPERUSER, block=True, priority=0)
@manual_watch.handle()
async def handle_manual_watch():
    # 注意：send() 不抛控制流异常，放 try 内是安全的；
    # 但 finish()/skip() 绝不能在 try 内（见天气插件同款问题的说明）。
    await manual_watch.send('开始手动触发UP主视频监控...')

    try:
        latest_video = await get_up_latest_video(BILI_UP_UID)
    except Exception as e:
        logger.error(f'手动监控异常：{e}')
        await manual_watch.finish(f'手动监控失败：{e}')
        return

    if not latest_video:
        await manual_watch.finish('手动监控失败：未获取到UP主视频数据')
        return

    # 更新缓存并推送（push_new_video 内部负责「优先发视频、超限退回快照」）
    write_video_cache({
        'timestamp': int(latest_video.get('publish_time', 0)),
        'title': latest_video.get('title', ''),
        'bvid': latest_video.get('bvid', ''),
    })

    await push_new_video(latest_video)
    await manual_watch.finish(
        f'手动监控完成！\n最新视频：{latest_video.get("title", "")} ({latest_video.get("bvid", "")})'
    )

# 2. BV解析指令
test_vsdu = on_command('bv', block=True, priority=0)
@test_vsdu.handle()
async def handle_test_vsdu(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    if not BILI_WATCHER_ENABLED:
        await test_vsdu.finish('插件已禁用！')

    input_url = args.extract_plain_text().strip()
    if not input_url:
        await test_vsdu.finish('请输入有效的B站视频链接（支持b23.tv短链接）！')

    # 处理短链接
    long_url = input_url
    if 'b23.tv' in input_url:
        await test_vsdu.send('正在解析短链接...')
        long_url = await b23_to_long_url(input_url)

    # 精简链接
    final_url = simplify_bilibili_url(long_url)
    bv_match = BV_PATTERN.search(final_url)
    bv_id = bv_match.group() if bv_match else ''

    # 取原始标题用于文件名
    title = await get_video_title(bv_id) if bv_id else ''

    # 优先发视频本体；超限/下载失败/发送失败则退回发快照
    await test_vsdu.send('正在下载视频...')
    video_path = await download_bilibili_video(bv_id) if bv_id else None
    # 裁掉开头黑场（否则 QQ 封面是一片黑）
    if video_path is not None:
        video_path = await process_video_or_original(video_path)
    video_sent = False
    fail_reason = ''
    if video_path is not None:
        try:
            await _send_video_to_current_group(bot, event.group_id, video_path, title, bv_id)
            video_sent = True
        except Exception as error:
            logger.error(f'视频发送失败，退回快照：{error}')
            fail_reason = '视频发送失败'
        finally:
            if video_path.exists():
                video_path.unlink(missing_ok=True)
    else:
        fail_reason = f'视频超过 {BILI_VIDEO_MAX_MB}MB 上限或下载失败'

    if video_sent:
        await test_vsdu.finish(f'视频已发送：{title}\n标准链接：{final_url}')

    # 退回发快照
    await test_vsdu.send('正在生成视频快照...')
    frame_path = await get_video_frame_by_VSDU_async(final_url)

    msg = Message()
    msg += MessageSegment.text('视频解析结果\n')
    if frame_path and frame_path.exists():
        msg += MessageSegment.image(frame_path.read_bytes())
        msg += MessageSegment.text('\n')
    msg += MessageSegment.text(f'（{fail_reason}，本次仅发送快照）\n')
    msg += MessageSegment.text(f'标准链接：{final_url}')

    await test_vsdu.finish(msg)

# 3. 清理缓存指令
clean_cache = on_command('biliclear', permission=SUPERUSER, block=True, priority=0)
@clean_cache.handle()
async def handle_clean_cache():
    # try 只包住真正可能出错的逻辑；finish() 放在 try 之外，
    # 否则它抛出的 FinishedException 会被 except 吃掉并误报成「操作失败：FinishedException()」。
    try:
        # 1. 删除缓存
        if CACHE_FILE.exists():
            CACHE_FILE.unlink(missing_ok=True)

        # 2. 获取最新视频
        latest_video = await get_up_latest_video(BILI_UP_UID)
        if not latest_video:
            msg = '✅ 缓存已清空，未获取到新动态'
        else:
            # 3. 构造消息
            bvid = latest_video['bvid']
            title = latest_video['title']
            url = f'https://www.bilibili.com/video/{bvid}/'
            up_name = await get_up_nickname(BILI_UP_UID)
            frame = await get_video_frame_by_VSDU_async(url)

            msg = Message(f'✅ 缓存已清空，已重新获取最新动态\nUP主：{up_name}\n标题：{title}\n')
            if frame and frame.exists():
                msg += MessageSegment.image(frame.read_bytes())
            msg += f'\n链接：{url}'
    except Exception as e:
        logger.error(f'biliclear error: {e}')
        msg = f'❌ 操作失败：{e}'

    # 统一用 finish 发送（必须在 try 之外）
    await clean_cache.finish(msg)

# 4. 新增：查看监控状态
status_cmd = on_command('bilistatus', permission=SUPERUSER, block=True, priority=0)
@status_cmd.handle()
async def handle_status():
    # 读取缓存
    cache = read_video_cache()
    
    # 组装状态信息
    # 确保凭证已加载（这步不发额外请求，只是读取并解析本地 Cookie）
    await BiliCredential.get_credential()

    status = Message()
    status += MessageSegment.text('B站UP监控状态\n')
    status += MessageSegment.text(f"├── 功能启用：{'是' if BILI_WATCHER_ENABLED else '❌ 否'}\n")
    status += MessageSegment.text(f'├── B站凭证：{BiliCredential.describe_credential()}\n')
    status += MessageSegment.text(f'├── 数据源：{BILI_LIST_SOURCE}\n')
    status += MessageSegment.text(f'├── 监控UP主UID：{BILI_UP_UID}\n')
    status += MessageSegment.text(f'├── 监控间隔：{BILI_UP_WAITSEC}秒\n')
    status += MessageSegment.text(f'├── 截取秒数：{BILI_FRAME_SEC}秒\n')
    status += MessageSegment.text(f'├── 推送群：{", ".join(MESSAGE_GROUPS)}（共 {len(MESSAGE_GROUPS)} 个）\n')
    status += MessageSegment.text(f'├── 缓存文件路径：{CACHE_FILE.absolute()}\n')
    status += MessageSegment.text(f"├── 最后更新缓存：{cache['update_time']}\n")
    status += MessageSegment.text(f"└── 最新监控视频：{cache['title']} (BV：{cache['bvid']})")
    
    await status_cmd.finish(status)

# 5. 废弃指令（别碰，这是石山代码）
check_uid = on_command('bilicheckuid', permission=SUPERUSER, block=True, priority=0)
@check_uid.handle()
async def handle_check_uid():
    await check_uid.finish(f'该指令已废弃！\n使用 .bilistatus 查看当前监控的UP主UID：{BILI_UP_UID}')

# ====================== 插件启动/关闭 ======================
@DRIVER.on_startup
async def startup():
    """插件启动时初始化"""
    logger.success('B站UP监控插件（优化版）正在启动...')
    
    if BILI_WATCHER_ENABLED:
        # 启动监控任务
        asyncio.create_task(monitor_up_new_video())
        logger.success('B站UP监控任务已启动')
    else:
        logger.warning('B站UP监控任务未启动（配置不完整）')

@DRIVER.on_shutdown
async def shutdown():
    """插件关闭时清理资源"""
    logger.info('B站UP监控插件正在关闭...')
    
    # 最后清理一次临时文件
    await clean_expired_temp_files()
    
    logger.success('B站UP监控插件已安全关闭')


register_help_commands([
    {
        'name': 'bv',
        'description': '（管理员）解析 B 站视频并返回快照',
        'usage': '/bv <视频链接>',
    },
    {
        'name': 'biliwatch',
        'description': '（管理员）立即触发一次 UP 主监控',
        'usage': '/biliwatch',
    },
    {
        'name': 'bilistatus',
        'description': '（管理员）查看监控状态',
        'usage': '/bilistatus',
    },
    {
        'name': 'biliclear',
        'description': '（管理员）清理缓存与临时文件',
        'usage': '/biliclear',
    },
    {
        'name': 'bilicheckuid',
        'description': '（管理员）校验配置的 UP 主 UID',
        'usage': '/bilicheckuid',
    },
    {
        'name': 'bililogin',
        'description': '（管理员）生成 B 站扫码登录二维码（只写文件与控制台）',
        'usage': '/bililogin',
    },
])
