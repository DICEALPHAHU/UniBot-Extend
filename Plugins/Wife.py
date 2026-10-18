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
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.plugin import PluginMetadata
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

    async def _rule(event) -> bool:
        return (
            isinstance(event, GroupMessageEvent)
            and _plain_text(event.message) == '抽老婆'
        )

    return Rule(_rule)


def is_force_marry() -> Rule:
    """触发规则：群消息以「强娶」开头且至少 @ 了一个人。"""

    async def _rule(event) -> bool:
        if not isinstance(event, GroupMessageEvent):
            return False
        if not _plain_text(event.message).startswith(FORCE_PREFIX):
            return False
        return any(segment.type == 'at' for segment in event.message)

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


def get_at_targets(message: Message) -> list[int]:
    """取出消息里所有 @ 的 QQ 号（排除 @全体成员）。"""
    targets: list[int] = []
    for segment in message:
        if segment.type != 'at':
            continue
        qq = str(segment.data.get('qq', ''))
        if qq.isdigit():
            targets.append(int(qq))
    return targets


def today_record(record: dict, group_id: str) -> dict:
    """取指定群今天的抽取记录（不存在则初始化）。"""
    return record.setdefault(group_id, {}).setdefault(str(date.today()), {})


async def member_display(bot: Bot, group_id: int, user_id: int) -> str:
    """获取群成员的显示名（群名片优先，其次昵称），失败回退为 QQ 号。"""
    try:
        info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
        return info.get('card') or info.get('nickname') or str(user_id)
    except Exception:
        return str(user_id)


@choose_wife.handle()
async def handle_choose_wife(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)
    user_id = event.user_id
    sender_at = MessageSegment.at(user_id)
    bot_self_id = int(bot.self_id)

    # 只加载一次：today_record 返回的是 record 内部的引用，
    # 后续今日分配写入这里，save_record 整份落盘即可。
    record = load_record()
    today = today_record(record, group_id)

    # 获取群成员列表，过滤机器人自身（可选排除自己）
    member_list = await bot.get_group_member_list(group_id=int(group_id))
    valid_members = [
        member
        for member in member_list
        if member['user_id'] != bot_self_id
        and (not FILTER_SELF or member['user_id'] != user_id)
    ]
    if not valid_members:
        await choose_wife.finish(f'{sender_at} 群里没人能抽啦～')

    # 已有记录且被抽者仍在群里：标记为重复抽取
    is_repeat = False
    lucky_member = None
    if str(user_id) in today:
        lucky_id = int(today[str(user_id)])
        lucky_member = next(
            (member for member in valid_members if member['user_id'] == lucky_id),
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
    lucky_name = lucky_member['card'] or lucky_member['nickname'] or '幸运群友'
    lucky_avatar = MessageSegment.image(AVATAR_URL.format(qq=lucky_id))

    if is_repeat:
        final_msg = (
            f'{sender_at}\n'
            f'你今天已经有老婆了哦~请好好对待呢~\n'
            f'{lucky_avatar}\n'
            f'{lucky_name}'
        )
    else:
        final_msg = (
            f'{sender_at}\n'
            f'你今天的群友老婆是：\n'
            f'{lucky_avatar}\n'
            f'{lucky_name}'
        )
    await choose_wife.finish(Message(final_msg))


@force_marry.handle()
async def handle_force_marry(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)
    user_id = event.user_id
    today_key = str(date.today())
    sender_at = MessageSegment.at(user_id)

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
    bot_self_id = int(bot.self_id)
    targets = [
        qq
        for qq in get_at_targets(event.message)
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

    target_name = await member_display(bot, event.group_id, target_id)
    target_avatar = MessageSegment.image(AVATAR_URL.format(qq=target_id))

    await force_marry.finish(
        Message(
            f'{sender_at}\n'
            f'你强娶了{MessageSegment.at(target_id)}：\n'
            f'{target_avatar}\n'
            f'{target_name}\n'
            f'负心变态抢老婆，真是个杂鱼！'
        )
    )


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
