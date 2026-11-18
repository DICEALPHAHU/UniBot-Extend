"""
群内「抽老婆」小功能。

触发方式：
  1. 群里发送文字「抽老婆」        —— 随机抽取今日群友老婆
  2. 群里发送「强娶 @某群友」      —— 已有老婆时改嫁指定群友，每人每天限一次
"""

import json
import random
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from nonebot import on_message
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna.uniseg import Image as UniImage
from nonebot_plugin_alconna.uniseg import UniMessage
from Scripts.CommandRules import ev_user_id, ev_group_id, sender_name
from nonebot.rule import Rule
from .HelpEntry import register_help_commands

# 可自定义配置项
FILTER_SELF = True  # True=不抽自己，False=允许抽中自己
RECORD_FILE = Path('Temp/Wife.json')  # 抽取记录（原 Data/Wife.json，已迁移）
LEGACY_RECORD_FILE = Path('Data/Wife.json')  # 旧路径，仅用于一次性迁移
FORCE_RECORD_FILE = Path('Temp/WifeForce.json')  # 强娶记录，与抽取记录分离存储
CLEAN_DAYS = 7  # 自动清理超过 N 天的抽取记录

# 头像接口（QQ 官方高清头像）
AVATAR_URL = 'https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640'

# 强娶触发前缀
FORCE_PREFIX = '强娶'


def _plain_text(message: Message) -> str:
    """取消息纯文本（去掉 @ 段等非文本内容）。"""
    return message.extract_plain_text().strip()


def is_choose_wife() -> Rule:
    """触发规则：群消息且内容为「抽老婆」。"""

    async def _rule(bot, event, state) -> bool:
        in_group = (
            getattr(event, 'group_id', None) is not None
            or getattr(event, 'group_openid', None) is not None
        )
        if not in_group:
            return False
        try:
            text = event.get_plaintext().strip()
        except Exception:
            return False
        import os
        if os.environ.get('WIFE_FORCE_RULE') == '1':
            from nonebot.log import logger as _log
            _log.info(f'[Wife] 规则命中 text={text!r}')
            return True
        return text == '抽老婆'

    async def _safe_rule(bot, event, state) -> bool:
        try:
            return await _rule(bot, event, state)
        except Exception as error:
            from nonebot.log import logger as _log

            _log.warning(f'[Wife] choose 规则异常：{type(error).__name__}: {error}')
            return False

    return Rule(_safe_rule)


def is_force_marry() -> Rule:
    """触发规则：群消息以「强娶」开头且至少 @ 了一个人。"""

    async def _rule(bot, event, state) -> bool:
        in_group = (
            getattr(event, 'group_id', None) is not None
            or getattr(event, 'group_openid', None) is not None
        )
        if not in_group:
            return False
        try:
            text = event.get_plaintext().strip()
        except Exception:
            return False
        return text.startswith(FORCE_PREFIX)

    return Rule(_rule)


# priority=0：必须小于 Scripts.Plugins.Events 的 priority=1。
# 该内置插件在 priority 1 以 block=True 截断事件，priority >= 1 的响应器永远不会被评估。
choose_wife = on_message(rule=is_choose_wife(), block=True, priority=0)
force_marry = on_message(rule=is_force_marry(), block=True, priority=0)


def ensure_dir(path: Path) -> None:
    """自动创建文件所在目录（若不存在）。"""
    if not path.parent.exists():
        path.parent.mkdir(parents=True)
        print(f'【抽老婆小功能】自动创建目录：{path.parent}')


def migrate_legacy_record() -> None:
    """把旧的 Data/Wife.json 迁移到 Temp/Wife.json（只在新位置不存在时执行一次）。"""
    if RECORD_FILE.exists() or not LEGACY_RECORD_FILE.exists():
        return
    ensure_dir(RECORD_FILE)
    try:
        shutil.move(str(LEGACY_RECORD_FILE), str(RECORD_FILE))
        print(f'【抽老婆小功能】已迁移记录：{LEGACY_RECORD_FILE} -> {RECORD_FILE}')
    except Exception as error:
        print(f'【抽老婆小功能】迁移记录失败：{error}')


def clean_expired_record(record: dict) -> dict:
    """清理超过 CLEAN_DAYS 天的历史记录。"""
    if not record:
        return record
    expire_date = date.today() - timedelta(days=CLEAN_DAYS)
    for group_id in list(record.keys()):
        group_record = record[group_id]
        for day in list(group_record.keys()):
            try:
                day_datetime = datetime.strptime(day, '%Y-%m-%d').date()
                if day_datetime < expire_date:
                    del group_record[day]
                    print(f'【抽老婆小功能】清理过期记录：群{group_id} {day}')
            except ValueError:
                # 日期格式错误则直接删除
                del group_record[day]
        if not group_record:
            del record[group_id]
    return record


def _read_json(path: Path) -> dict:
    """读取 JSON 记录，文件缺失或损坏时返回空字典。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception as error:
        print(f'【抽老婆小功能】加载记录失败，文件损坏：{error}，返回空记录')
        return {}


def _write_json(path: Path, data: dict) -> None:
    """写入 JSON 记录。"""
    ensure_dir(path)
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8'
        )
    except Exception as error:
        print(f'【抽老婆小功能】保存记录失败：{error}')


def load_record() -> dict:
    """加载抽取记录，并清理过期记录。"""
    migrate_legacy_record()
    return clean_expired_record(_read_json(RECORD_FILE))


def save_record(record: dict) -> None:
    """保存抽取记录到文件。"""
    _write_json(RECORD_FILE, record)


def load_force_record() -> dict:
    """加载强娶记录（结构：{群号: {日期: [已强娶的QQ, ...]}}）。"""
    return _read_json(FORCE_RECORD_FILE)


def save_force_record(record: dict) -> None:
    """保存强娶记录到文件。"""
    _write_json(FORCE_RECORD_FILE, record)


def get_at_targets(event) -> list[str]:
    """取出消息里所有 @ 的目标 ID（兼容 OneBot 与官方 QQ）。"""
    targets: list[str] = []
    message = getattr(event, 'message', None)
    if message is None:
        return targets
    try:
        segments = list(message)
    except TypeError:
        return targets
    for segment in segments:
        seg_type = getattr(segment, 'type', '')
        data = getattr(segment, 'data', {}) or {}
        if seg_type in ('at', 'mention'):
            target = data.get('qq') or data.get('user_id') or data.get('id') or data.get('openid')
            if target and str(target) != 'all':
                targets.append(str(target))
    return targets


def today_record(record: dict, group_id: str) -> dict:
    """取指定群今天的抽取记录（不存在则初始化）。"""
    return record.setdefault(group_id, {}).setdefault(str(date.today()), {})


# ===== 群成员来源 =====
# QQ 官方适配器没有「获取群成员列表」的 API，只能从会话里见过的发言者累积。
# 结构：{group_id: {user_id: 显示名}}
_SEEN_MEMBERS: dict[str, dict[str, str]] = {}
_SEEN_MAX = 200


# 成员名单落盘路径（重启后不丢）
MEMBER_FILE = Path('Data/WifeMembers.json')


def _read_members_file() -> dict:
    """从磁盘读取成员名单（唯一数据源）。"""
    if not MEMBER_FILE.exists():
        return {}
    try:
        data = json.loads(MEMBER_FILE.read_text('Utf-8'))
        if isinstance(data, dict):
            return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def _write_members_file(data: dict) -> None:
    """写回成员名单（失败不影响功能）。"""
    try:
        MEMBER_FILE.parent.mkdir(parents=True, exist_ok=True)
        MEMBER_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), 'Utf-8')
    except Exception:
        pass


def _load_members() -> None:
    """启动时把磁盘内容载入内存（只读镜像，写操作以磁盘为准）。"""
    _SEEN_MEMBERS.clear()
    _SEEN_MEMBERS.update(_read_members_file())


def _remember_member(group_id: str, user_id: str, name: str) -> None:
    """记录群里出现过的成员，供抽老婆当候选人。

    以磁盘文件为唯一数据源：先读最新内容再合并写回，
    避免内存里的旧快照覆盖掉历史导入的数据。
    """
    if not group_id or not user_id:
        return
    # 重新从磁盘加载，保证不丢
    disk = _read_members_file()
    bucket = disk.setdefault(str(group_id), {})
    bucket[str(user_id)] = name or str(user_id)
    if len(bucket) > _SEEN_MAX:
        for key in list(bucket)[: len(bucket) - _SEEN_MAX]:
            bucket.pop(key, None)
    # 同步到内存
    _SEEN_MEMBERS.clear()
    _SEEN_MEMBERS.update(disk)
    _write_members_file(disk)


_seen_listener = on_message(priority=1, block=False)


@_seen_listener.handle()
async def _collect_member(event):
    """被动收集群成员（不回复、不阻断）。

    每收到一条群消息就记录发送者，形成抽老婆的候选人池。
    名单以 Data/WifeMembers.json 为准，重启不丢。
    """
    try:
        gid = ev_group_id(event)
        uid = ev_user_id(event)
        name = sender_name(event)
        existed = uid in _read_members_file().get(str(gid), {})
        _remember_member(gid, uid, name)
        if not existed:
            from nonebot.log import logger as _log

            total = len(_read_members_file().get(str(gid), {}))
            _log.info(f'[Wife] 新成员入库：{name}（本群共 {total} 人）')
    except Exception as error:
        from nonebot.log import logger as _log

        _log.warning(f'[Wife] 收集成员失败：{type(error).__name__}: {error}')


async def _get_members(bot, event) -> list[dict]:
    """取群成员列表：OneBot 用 API，官方 QQ 用累积的发言者。

    官方 QQ 场景下，真实 QQ 号（纯数字）排在前面——只有它们能查到真实头像，
    openid 只能画首字头像，所以作为兜底。
    """
    group_id = ev_group_id(event)
    module = type(event).__module__
    if module.startswith('nonebot.adapters.onebot'):
        try:
            return await bot.get_group_member_list(group_id=int(group_id))
        except Exception:
            pass
    # 官方 QQ：用累积的成员，真实 QQ 号优先
    bucket = _read_members_file().get(str(group_id), {})
    real = [(uid, name) for uid, name in bucket.items() if str(uid).isdigit()]
    other = [(uid, name) for uid, name in bucket.items() if not str(uid).isdigit()]
    ordered = real + other
    return [{'user_id': uid, 'card': name, 'nickname': name} for uid, name in ordered]


async def member_display(bot: Bot, group_id: int, user_id: int) -> str:
    """获取群成员的显示名（群名片优先，其次昵称），失败回退为 QQ 号。"""
    try:
        info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
        return info.get('card') or info.get('nickname') or str(user_id)
    except Exception:
        return str(user_id)


# 头像最大边长（像素）
AVATAR_MAX_SIDE = 96


# 首字头像配色（按名字哈希取色，保证同一个人颜色固定）
_LETTER_COLORS = [
    (66, 133, 244), (219, 68, 55), (244, 180, 0), (15, 157, 88),
    (171, 71, 188), (0, 172, 193), (255, 112, 67), (94, 53, 177),
]


def _make_letter_avatar(name: str) -> bytes | None:
    """用昵称首字生成圆形彩色头像（PNG 字节）。"""
    from io import BytesIO

    try:
        from PIL import Image, ImageDraw

        text = (name or '?').strip()[:1] or '?'
        color = _LETTER_COLORS[abs(hash(name)) % len(_LETTER_COLORS)]
        size = AVATAR_MAX_SIDE
        image = Image.new('RGB', (size, size), color)
        draw = ImageDraw.Draw(image)

        font = None
        for font_path in (
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        ):
            try:
                from pathlib import Path as _P

                if _P(font_path).exists():
                    from PIL import ImageFont

                    font = ImageFont.truetype(font_path, int(size * 0.55))
                    break
            except Exception:
                continue
        if font is None:
            from PIL import ImageFont

            font = ImageFont.load_default()

        # 居中绘制
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (size - (bbox[2] - bbox[0])) / 2 - bbox[0]
        y = (size - (bbox[3] - bbox[1])) / 2 - bbox[1]
        draw.text((x, y), text, font=font, fill=(255, 255, 255))

        buffer = BytesIO()
        image.save(buffer, format='PNG', optimize=True)
        return buffer.getvalue()
    except Exception:
        return None


# ===== 抽老婆卡片渲染 =====

CARD_WIDTH = 460
CARD_HEIGHT = 360
CARD_BG = (28, 32, 40)
CARD_ACCENT = (198, 120, 221)
CARD_TEXT = (238, 238, 242)
CARD_SUB = (150, 155, 165)


def _card_font(size: int, bold: bool = False):
    """取中文字体，找不到退默认。"""
    from PIL import ImageFont

    candidates = (
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc' if bold
        else '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        'Data/Kebiao/NotoSansSC.ttf',
    )
    for path in candidates:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _image_mode_on() -> bool:
    """当前是否开启图片模式（Config.toml 的 [image].mode）。"""
    try:
        from Scripts.Config import config

        return bool(getattr(getattr(config, 'image', None), 'mode', False))
    except Exception:
        return False


async def _reply_result(matcher, card: bytes | None, text: str) -> None:
    """按配置发结果：

    - 图片模式且卡片渲染成功 → 发卡片
    - 否则（纯文字模式 / 卡片失败）→ 发文字

    保证任何配置下都有回应，绝不静默。
    """
    from nonebot.log import logger as _log

    if _image_mode_on() and card:
        _log.info(f'[Wife] 发送卡片 {len(card)} bytes')
        await matcher.finish(UniMessage(UniImage(raw=card)))
        return
    _log.info(f'[Wife] 发送文字（图片模式={_image_mode_on()}，卡片={bool(card)}）')
    await matcher.finish(text)




def build_wife_card(
    avatar: bytes | None,
    name: str,
    title: str,
    subtitle: str,
    owner: str = '',
    target_id: str = '',
) -> bytes | None:
    """渲染抽老婆卡片（4 倍超采样抗锯齿 + 右下角水印）。

    owner: 发起抽签的人（显示在标题下方）
    target_id: 被抽到的人的 QQ 号（显示在名字下方，小字）
    """
    from io import BytesIO

    try:
        from PIL import Image, ImageDraw

        # 超采样倍数：先按 4x 绘制再缩回，圆形边缘才平滑
        scale = 4
        w, h = CARD_WIDTH * scale, CARD_HEIGHT * scale

        card = Image.new('RGB', (w, h), CARD_BG)
        draw = ImageDraw.Draw(card)

        # 顶部强调色条
        draw.rectangle([0, 0, w, 6 * scale], fill=CARD_ACCENT)

        # 头像（圆形遮罩）
        avatar_size = 128 * scale
        avatar_x = (w - avatar_size) // 2
        avatar_y = 44 * scale
        box = [avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size]

        if avatar:
            try:
                with Image.open(BytesIO(avatar)) as src:
                    img = src.convert('RGB').resize((avatar_size, avatar_size), Image.LANCZOS)
                # 超采样遮罩：4x 下画圆再缩，边缘自然平滑
                mask = Image.new('L', (avatar_size, avatar_size), 0)
                ImageDraw.Draw(mask).ellipse([0, 0, avatar_size - 1, avatar_size - 1], fill=255)
                card.paste(img, (avatar_x, avatar_y), mask)
            except Exception:
                draw.ellipse(box, fill=(60, 65, 75))
        else:
            draw.ellipse(box, fill=(60, 65, 75))

        # 头像描边：超采样后自动平滑，线宽按比例放大
        draw.ellipse(box, outline=CARD_ACCENT, width=3 * scale)

        # 标题（小字，顶部）
        title_font = _card_font(18 * scale)
        tw = draw.textbbox((0, 0), title, font=title_font)
        draw.text(((w - (tw[2] - tw[0])) / 2, 16 * scale), title, font=title_font, fill=CARD_SUB)

        # QQ 号：名字下方的小字
        if target_id:
            uid_font = _card_font(13 * scale)
            uid_text = f'QQ {target_id}'
            ub = draw.textbbox((0, 0), uid_text, font=uid_font)
            draw.text(
                ((w - (ub[2] - ub[0])) / 2, avatar_y + avatar_size + 58 * scale),
                uid_text, font=uid_font, fill=(120, 125, 135),
            )

        # 名字（大字）
        name_font = _card_font(32 * scale, bold=True)
        nb = draw.textbbox((0, 0), name, font=name_font)
        draw.text(
            ((w - (nb[2] - nb[0])) / 2, avatar_y + avatar_size + 18 * scale),
            name, font=name_font, fill=CARD_TEXT,
        )

        # 副标题
        sub_font = _card_font(16 * scale)
        sb = draw.textbbox((0, 0), subtitle, font=sub_font)
        draw.text(
            ((w - (sb[2] - sb[0])) / 2, avatar_y + avatar_size + 84 * scale),
            subtitle, font=sub_font, fill=CARD_SUB,
        )

        # 缩回目标尺寸（这一步产生抗锯齿效果）
        card = card.resize((CARD_WIDTH, CARD_HEIGHT), Image.LANCZOS)

        # 水印：与 Stats 玩家卡同款
        try:
            from .Watermark import apply_watermark

            card = apply_watermark(card, size=14, opacity=110)
        except Exception:
            pass

        buffer = BytesIO()
        card.save(buffer, format='PNG', optimize=True)
        return buffer.getvalue()
    except Exception:
        return None


async def _fetch_avatar(qq, name: str = '') -> bytes | None:
    """取群友头像：先用 QQ 号查真头像，拿不到就用昵称首字画一个。

    QQ 官方适配器只给 openid，查不到 qlogo 头像（会返回 949 字节的默认图），
    因此对 openid 场景改用昵称首字生成彩色头像，比默认灰图好看。
    """
    from io import BytesIO

    import httpx

    raw = None
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            resp = await client.get(AVATAR_URL.format(qq=qq))
            if resp.status_code == 200 and len(resp.content) > 2048:
                # 小于 2KB 基本是默认灰图，丢弃
                raw = resp.content
    except Exception:
        raw = None

    if raw is None:
        return _make_letter_avatar(name or str(qq))

    try:
        from PIL import Image

        with Image.open(BytesIO(raw)) as image:
            image.load()
            if image.mode != 'RGB':
                image = image.convert('RGB')
            if max(image.size) > AVATAR_MAX_SIDE:
                ratio = AVATAR_MAX_SIDE / max(image.size)
                image = image.resize(
                    (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                    Image.LANCZOS,
                )
            buffer = BytesIO()
            image.save(buffer, format='PNG', optimize=True)
            return buffer.getvalue()
    except Exception:
        return raw


@choose_wife.handle()
async def handle_choose_wife(bot, event):
    from nonebot.log import logger as _log

    group_id = str(ev_group_id(event))
    user_id = ev_user_id(event)
    _log.info(f'[Wife] 开始抽老婆 group={group_id} user={user_id}')
    sender_at = ''
    bot_self_id = str(bot.self_id)

    # 只加载一次：today_record 返回的是 record 内部的引用，
    # 后续今日分配写入这里，save_record 整份落盘即可。
    record = load_record()
    today = today_record(record, group_id)

    # 获取群成员列表，过滤机器人自身（可选排除自己）
    member_list = await _get_members(bot, event)
    _log.info(f'[Wife] 候选成员数={len(member_list)}')
    valid_members = [
        member
        for member in member_list
        if str(member['user_id']) != bot_self_id
        and (not FILTER_SELF or str(member['user_id']) != str(user_id))
    ]
    if not valid_members:
        await choose_wife.finish(f'{sender_at} 群里没人能抽啦～')

    # 已有记录且被抽者仍在群里：标记为重复抽取
    is_repeat = False
    lucky_member = None
    if str(user_id) in today:
        lucky_id = today[str(user_id)]
        lucky_member = next(
            (member for member in valid_members if str(member['user_id']) == str(lucky_id)),
            None,
        )
        if lucky_member:
            is_repeat = True

    # 无记录或已抽的人退群：重新随机抽取并更新记录
    if not lucky_member:
        lucky_member = random.choice(valid_members)
        today[str(user_id)] = lucky_member['user_id']
        save_record(record)

    lucky_id = lucky_member['user_id']
    _log.info(f'[Wife] 抽到 lucky_id={lucky_id}')
    lucky_name = lucky_member['card'] or lucky_member['nickname'] or '幸运群友'
    lucky_avatar_bytes = await _fetch_avatar(lucky_id, lucky_name)

    if is_repeat:
        head = '你今天已经有老婆了哦~请好好对待呢~'
    else:
        head = '你今天的群友老婆是：'
    final_text = f'{head}\n{lucky_name}'

    # 头像小图 + 文字分两条发（官方 QQ 单条只带一个媒体）
    _log.info(f'[Wife] 头像字节数={len(lucky_avatar_bytes) if lucky_avatar_bytes else 0}')
    # 渲染卡片：头像 + 名字 + 说明，一张图解决
    # 发起抽签的人的显示名
    owner_name = sender_name(event) or str(user_id)
    card = build_wife_card(
        lucky_avatar_bytes,
        lucky_name,
        f'{owner_name} 今天抽到的群友老婆' if not is_repeat else f'{owner_name} 今天已经有老婆了',
        '请好好对待哦~' if not is_repeat else '明天再来吧~',
        owner=owner_name,
        target_id=lucky_id,
    )
    _log.info(f'[Wife] 卡片字节数={len(card) if card else 0}')
    await _reply_result(choose_wife, card, final_text)


@force_marry.handle()
async def handle_force_marry(bot, event):
    from nonebot.log import logger as _log

    group_id = str(ev_group_id(event))
    _log.info(f'[Wife] 强娶 group={group_id}')
    user_id = ev_user_id(event)
    today_key = str(date.today())
    sender_at = ''

    # 1. 必须已抽过老婆才能强娶
    record = load_record()
    today = today_record(record, group_id)
    if str(user_id) not in today:
        await force_marry.finish(f'{sender_at}\n你还没抽老婆呢，先发「抽老婆」吧～')

    # 2. 每人每天只能强娶一次
    force_record = load_force_record()
    married_list = force_record.setdefault(group_id, {}).setdefault(today_key, [])
    if str(user_id) in married_list:
        await force_marry.finish(f'{sender_at}\n你是想NTR所有人吗？\n你这个家伙，不可以！')

    # 3. 校验被强娶对象：排除自己与机器人
    bot_self_id = str(bot.self_id)
    targets = [
        qq
        for qq in get_at_targets(event)
        if qq != bot_self_id and qq != user_id
    ]
    if not targets:
        await force_marry.finish(f'{sender_at}\n要 @ 一个群友才能强娶哦～')

    target_id = targets[0]

    # 4. 改写今日分配
    today[str(user_id)] = target_id
    save_record(record)

    # 5. 记录本次强娶（与抽取记录分离存储）
    married_list.append(str(user_id))
    save_force_record(force_record)

    target_name = await member_display(bot, ev_group_id(event), target_id)
    target_avatar_bytes = await _fetch_avatar(target_id, target_name)

    text = f'你强娶了 {target_name}：\n负心变态抢老婆，真是个杂鱼！'
    marry_owner = sender_name(event) or str(user_id)
    card = build_wife_card(
        target_avatar_bytes, target_name,
        f'{marry_owner} 强娶成功', '负心变态抢老婆，真是个杂鱼！',
        owner=marry_owner, target_id=target_id,
    )
    await _reply_result(force_marry, card, text)


# 查看已收集的群成员（抽老婆的候选人池）
def is_member_list() -> Rule:
    """只匹配「老婆名单」等关键词，避免无 rule 截断所有群消息。"""

    async def _rule(bot, event, state) -> bool:
        try:
            text = event.get_plaintext().strip()
        except Exception:
            return False
        return text in ('老婆名单', '抽老婆名单', 'womenlist')

    return Rule(_rule)


member_list_cmd = on_message(rule=is_member_list(), priority=0, block=True)


@member_list_cmd.handle()
async def handle_member_list(event):
    """列出已收集到的群成员，用于确认候选人池。"""

    bucket = _SEEN_MEMBERS.get(str(ev_group_id(event)), {})
    if not bucket:
        await member_list_cmd.finish('还没有收集到群成员～ 让大家在群里说几句话再来看看。')
    lines = [f'已收集 {len(bucket)} 位群成员：']
    for index, (uid, name) in enumerate(list(bucket.items())[:30], 1):
        lines.append(f'{index}. {name}')
    if len(bucket) > 30:
        lines.append(f'... 还有 {len(bucket) - 30} 位')
    await member_list_cmd.finish('\n'.join(lines))


__plugin_meta__ = PluginMetadata(
    name='抽老婆',
    description='群内抽老婆 / 强娶改嫁，每人每天限一次',
    usage='抽老婆 —— 随机抽取今日群友老婆\n强娶 @某人 —— 改嫁给指定群友',
    extra={'author': 'DICEALPHAHU', 'version': '1.0'},
)

register_help_commands([
    {
        'name': 'wife',
        'description': '随机抽取今日群友老婆',
        'usage': '抽老婆',
    },
    {
        'name': 'wife_force',
        'description': '强娶指定群友（每人每天一次）',
        'usage': '强娶 @群友',
    },
])
