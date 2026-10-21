"""
插件共享配置。

从环境变量 / .env 读取，供各插件统一使用。

注意：nonebot 的 get_plugin_config 按「字段名大写」取环境变量，不做分词，
因此字段 `uapipro_token` 对应的环境变量名是 `UAPIPRO_TOKEN`（不是 UAPI_TOKEN）。
"""

from nonebot import get_plugin_config
from pydantic import BaseModel


class Config(BaseModel):
    """插件共享配置模型。"""

    # uapis.cn API Token
    uapipro_token: str = ''

    # B站 UP 动态监控
    bili_watcher_enabled: bool = False
    bili_up_uid: int = 0
    bili_up_waitsec: int = 30
    bili_watcher_framesec: int = 2

    # ===== B站视频推送 =====
    # 发送视频的体积上限（MB）。超过则不再发送视频，退回只发快照。
    # 注意 base64 上传时字符串会膨胀约 33%，实际上限还取决于 OneBot 实现。
    bili_video_max_mb: int = 50

    # 保留最近 N 天的视频临时/成品文件
    bili_video_keep_days: int = 3

    # ===== ffmpeg（用于裁掉视频开头的黑场淡入）=====
    # ffmpeg 可执行文件路径。留空则依次尝试：环境变量 FFMPEG_PATH -> 系统 PATH -> 自动下载。
    # 填了路径就不需要把 ffmpeg 加进 PATH。
    bili_ffmpeg_path: str = ''

    # 系统里找不到 ffmpeg 时，是否自动下载一份到 Temp/ffmpeg/（约 40MB，只下一次）。
    # 关闭后需要自行配置 bili_ffmpeg_path 或加入 PATH。
    bili_ffmpeg_auto_download: bool = True

    # 是否把视频开头的黑场裁掉。裁掉后第 0 帧即亮画面，
    # QQ/NapCat 抽取的封面才不会是一片黑。
    bili_trim_black_enabled: bool = True

    # 黑场判定：亮度低于该值(0-255)且持续超过 bili_black_min_duration 秒，视为黑场
    bili_black_pixel_threshold: float = 0.10
    bili_black_min_duration: float = 0.3

    # 只裁掉开头的黑场，最多裁这么多秒（防止把整条视频误判裁剪掉）
    bili_max_trim_seconds: float = 5.0

    # 超过这个时长的视频不做重编码（重编码是 CPU 密集操作，避免长时间占用）
    bili_max_process_minutes: float = 10.0

    # ===== B站请求后端 =====
    # auto（默认）：装了 curl_cffi 就用浏览器 TLS 指纹请求，没装自动回退 httpx
    # httpx / curl_cffi：强制指定（curl_cffi 未安装时仍会回退 httpx）
    # 只影响 B 站 API 请求（投稿列表、用户信息），视频下载仍走 httpx
    bili_http_backend: str = 'auto'

    # 直接注入 B 站 Cookie（无头部署用），形如：
    # SESSDATA=xxx; bili_jct=yyy; DedeUserID=zzz
    # 留空则使用 Data/Bili/credential.json（扫码登录后自动写入）
    bili_cookie: str = ''

    # ===== 玩家卡（/player）=====
    # 卡片字体路径。留空则使用 Data/PlayerCard/NotoSansSC.ttf，没有时会自动下载。
    player_font_path: str = ''

    # 本地没有字体时是否自动下载一份（约 10MB，只下一次）。
    player_font_auto_download: bool = True

    # 字体下载地址。留空则使用内置的 Noto Sans SC 地址列表。
    player_font_url: str = ''


plugin_config = get_plugin_config(Config)
