"""Minecraft Wiki 词条查询。

触发方式：群里发送「.wiki <关键词>」，例如「.wiki lava chicken」。

数据来源是中文 Minecraft Wiki 的 MediaWiki 接口：先搜索词条，再取首段简介与
缩略图，最后拼成「图片 + 标题 + 简介 + 原文链接」回复。接口不需要 Token。
"""

from httpx import AsyncClient
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from .HelpEntry import register_help_commands

# 中文 Minecraft Wiki 的 MediaWiki 接口与词条页前缀
WIKI_API_URL = 'https://zh.minecraft.wiki/api.php'
WIKI_PAGE_URL = 'https://zh.minecraft.wiki/w/'

# 单次请求超时（秒）
WIKI_TIMEOUT = 10
# 简介最大保留字数，超出截断，避免超长词条刷屏
WIKI_MAX_SUMMARY_CHARS = 240
# 缩略图体积上限（字节），超过就只发文字
WIKI_MAX_IMAGE_BYTES = 2 * 1024 * 1024
# 带上 UA，部分站点会拒绝空 UA 的抓取
WIKI_USER_AGENT = 'UniBot-Extend wiki plugin (https://github.com/DICEALPHAHU/UniBot-Extend)'

# priority=0：必须小于 Scripts.Plugins.Events 的 priority=1（它以 block=True 截断事件）。
wiki_cmd = on_command('wiki', priority=0, block=True)


async def search_wiki_page(keyword: str) -> dict | None:
    """按关键词搜索词条，返回首条结果的简介与缩略图信息；无结果返回 None。"""
    params = {
        'action': 'query',
        'format': 'json',
        'formatversion': 2,
        # 搜索 + 直接带出首段简介和缩略图，一次请求搞定
        'generator': 'search',
        'gsrsearch': keyword,
        'gsrlimit': 1,
        'prop': 'pageimages|extracts',
        'exintro': 1,
        'explaintext': 1,
        'exlimit': 1,
        'pithumbsize': 400,
    }
    async with AsyncClient(timeout=WIKI_TIMEOUT) as client:
        response = await client.get(WIKI_API_URL, params=params, headers={'User-Agent': WIKI_USER_AGENT})
        if response.status_code != 200:
            raise RuntimeError(f'Wiki 接口返回 HTTP {response.status_code}')
        data = response.json()

    pages = data.get('query', {}).get('pages') or []
    if not pages:
        return None
    # formatversion=2 下 pages 是列表，按搜索排名取第一条
    return min(pages, key=lambda page: page.get('index', 0))


async def fetch_wiki_thumbnail(source: str) -> bytes | None:
    """下载缩略图，失败或超过体积上限时返回 None（此时只发文字）。"""
    async with AsyncClient(timeout=WIKI_TIMEOUT, follow_redirects=True) as client:
        response = await client.get(source, headers={'User-Agent': WIKI_USER_AGENT})
        if response.status_code != 200:
            return None
        content = response.content
    if len(content) > WIKI_MAX_IMAGE_BYTES:
        return None
    return content


def build_wiki_link(title: str) -> str:
    """把词条标题转成 Wiki 原文链接（空格换成下划线，中文保持可读）。"""
    return WIKI_PAGE_URL + title.replace(' ', '_')


def shorten_summary(text: str) -> str:
    """截断过长的简介，保留一句完整的话。"""
    text = text.strip()
    if len(text) <= WIKI_MAX_SUMMARY_CHARS:
        return text
    cut = text[:WIKI_MAX_SUMMARY_CHARS]
    # 尽量在句号/换行处收尾，避免半截话
    for index in range(len(cut) - 1, 0, -1):
        if cut[index] in '。！？\n':
            return cut[: index + 1]
    return cut + '……'


async def build_wiki_reply(page: dict, keyword: str) -> Message:
    """把词条信息组装成「图片 + 文字」消息，发不出去的图自动降级为纯文字。"""
    title = page.get('title') or keyword
    summary = shorten_summary(page.get('extract') or '') or '（该词条暂无简介，点开原文看看吧）'
    link = build_wiki_link(title)

    message = Message()
    thumbnail = (page.get('thumbnail') or {}).get('source')
    if thumbnail:
        try:
            image = await fetch_wiki_thumbnail(thumbnail)
        except Exception:
            image = None
        if image:
            message += MessageSegment.image(image)

    message += Message(f'【{title}】\n{summary}\nWiki 原文指北：{link}')
    return message


@wiki_cmd.handle()
async def handle_wiki(args: Message = CommandArg()):
    keyword = args.extract_plain_text().strip()
    if not keyword:
        await wiki_cmd.finish('请输入要查询的词条。示例：.wiki lava chicken')

    # try 只包住网络请求；finish() 放在 try 之外，避免它的 FinishedException 被误 catch。
    try:
        page = await search_wiki_page(keyword)
    except Exception as error:
        await wiki_cmd.finish(f'Wiki 查询失败：{str(error)[:40]}')
        return

    if page is None:
        await wiki_cmd.finish(f'没有找到与「{keyword}」相关的词条，换个关键词试试？')

    await wiki_cmd.finish(await build_wiki_reply(page, keyword))


__plugin_meta__ = PluginMetadata(
    name='Minecraft Wiki 查询',
    description='搜索中文 Minecraft Wiki 词条，返回简介、缩略图与原文链接（数据来源：zh.minecraft.wiki）',
    usage='.wiki <关键词>\n示例：.wiki lava chicken',
    extra={'author': 'DICEALPHAHU', 'version': '1.0'},
)

register_help_commands([
    {
        'name': 'wiki',
        'description': '搜索 Minecraft Wiki 词条并返回简介与原文链接',
        'usage': '/wiki <关键词>',
    },
])
