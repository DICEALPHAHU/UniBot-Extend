"""Minecraft Wiki 词条查询。

触发方式：群里发送「.wiki <关键词>」，例如「.wiki lava chicken」。

数据来源是中文 Minecraft Wiki 的 MediaWiki 接口：先搜索词条，再取首段简介与
缩略图，最后拼成「图片 + 标题 + 简介 + 原文链接」回复。接口不需要 Token。

搜不到时会先尝试接口给出的「您是不是要查」建议词，再退化为本地近似标题匹配，
避免一个拼写错误就查不到结果。
"""

import asyncio
import difflib
import re
import time
import urllib.parse

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

# 接口返回 5xx 时的重试次数与退避基数（秒）
WIKI_RETRY_TIMES = 2
WIKI_RETRY_DELAY = 0.8

# 查询结果缓存时长（秒）与条目上限：社区 Wiki 有限流，重复查询不应该反复打接口
WIKI_CACHE_TTL = 600
WIKI_CACHE_MAX = 200
# 本地近似标题匹配的相似度阈值（0~1），太低容易匹配到无关词条
WIKI_FUZZY_CUTOFF = 0.72
# 标题与查询的匹配分：低于它说明只是全文命中（容易召回版本日志之类的页面），
# 需要改试拼写建议或截断问句。
WIKI_GOOD_SCORE = 40
# 中文问句里的疑问词：命中后取它前面的部分当关键词重试（「铜有哪些变种」->「铜」）
WIKI_QUESTION_MARKERS = ('有哪些', '有什么', '是什么', '有什么用', '怎么获得', '怎么用', '如何获得', '如何', '为什么')

# priority=0：必须小于 Scripts.Plugins.Events 的 priority=1（它以 block=True 截断事件）。
wiki_cmd = on_command('wiki', priority=0, block=True)

# 关键词 -> (写入时间, (页面, 实际使用的关键词))；页面为 None 表示确认查无此词
_cache: dict[str, tuple[float, tuple[dict | None, str]]] = {}
_CACHE_MISS = object()


class WikiUnavailable(RuntimeError):
    """Wiki 服务端不可用（连续 5xx），通常是站点维护或后端故障。"""

    def __init__(self, status: int):
        super().__init__(f'Wiki 接口返回 HTTP {status}')
        self.status = status


def _cache_get(keyword: str):
    """读缓存，过期自动清理。"""
    key = keyword.strip().lower()
    item = _cache.get(key)
    if item is None:
        return _CACHE_MISS
    created_at, value = item
    if time.monotonic() - created_at > WIKI_CACHE_TTL:
        _cache.pop(key, None)
        return _CACHE_MISS
    return value


def _cache_put(keyword: str, value: tuple[dict | None, str]) -> None:
    """写缓存，超过条目上限时清理最早的一半。"""
    if len(_cache) >= WIKI_CACHE_MAX:
        for key in sorted(_cache, key=lambda k: _cache[k][0])[: WIKI_CACHE_MAX // 2]:
            _cache.pop(key, None)
    _cache[keyword.strip().lower()] = (time.monotonic(), value)


async def _request(client: AsyncClient, params: dict) -> dict:
    """调用 MediaWiki 接口，返回 JSON；5xx 会退避重试几次。"""
    payload = {'action': 'query', 'format': 'json', 'formatversion': 2, **params}
    status = 0
    for attempt in range(WIKI_RETRY_TIMES + 1):
        response = await client.get(WIKI_API_URL, params=payload, headers={'User-Agent': WIKI_USER_AGENT})
        status = response.status_code
        if status == 200:
            return response.json()
        if status < 500:
            raise RuntimeError(f'Wiki 接口返回 HTTP {status}')
        if attempt < WIKI_RETRY_TIMES:
            await asyncio.sleep(WIKI_RETRY_DELAY * (attempt + 1))
    raise WikiUnavailable(status)


def _normalize(text: str) -> str:
    """去掉空白并转小写，用于比较标题与关键词。"""
    return ''.join((text or '').split()).lower()


def _strip_parenthetical(text: str) -> str:
    """去掉括号里的内容：「力量（附魔）」->「力量」。"""
    return re.sub(r'[（(][^）)]*[）)]', '', text).strip()


def _trim_question(text: str) -> str:
    """截掉疑问词，留下真正要查的主干：「铜有哪些变种」->「铜」。"""
    for marker in WIKI_QUESTION_MARKERS:
        index = text.find(marker)
        if index > 0:
            return text[:index].strip('，,。 ?？!！')
    return ''


def pick_best_page(keyword: str, pages: list[dict]) -> tuple[dict | None, float]:
    """在搜索结果里挑与关键词最贴合的页面，返回 (页面, 匹配分)。

    Wiki 的全文搜索经常把版本日志排在真正词条前面（例如查「力量（附魔）」
    的第一条是「携带版0.12.1.b11」），所以按标题贴合度重新排一遍；
    全都不贴合时再退回 Wiki 自己的排序。
    """
    if not pages:
        return None, 0.0
    query = _normalize(keyword)
    base = _strip_parenthetical(query) or query
    best_page, best_score = None, -1.0
    for page in pages:
        title = _normalize(page.get('title') or '')
        rank = page.get('index', 99)
        if title == query:
            score = 100.0
        elif base and title == base:
            score = 95.0
        elif base and base in title:
            score = 80.0
        elif title and title in query:
            score = 70.0
        else:
            score = 60.0 * difflib.SequenceMatcher(None, base, title).ratio()
        # 搜索排名只做微弱加权，避免同分时选错
        score -= rank * 0.5
        if score > best_score:
            best_page, best_score = page, score
    if best_score < WIKI_GOOD_SCORE:
        return min(pages, key=lambda item: item.get('index', 99)), best_score
    return best_page, best_score


async def _search_best(client: AsyncClient, keyword: str) -> tuple[dict | None, float]:
    """搜索并挑出最贴合的页面，返回 (页面, 匹配分)。"""
    data = await _request(
        client,
        {
            'generator': 'search',
            'gsrsearch': keyword,
            'gsrlimit': 5,
            'prop': 'pageimages|extracts',
            'exintro': 1,
            'explaintext': 1,
            'exlimit': 5,
            'pithumbsize': 400,
        },
    )
    pages = (data.get('query') or {}).get('pages') or []
    return pick_best_page(keyword, pages)


async def _suggest(client: AsyncClient, keyword: str) -> str | None:
    """取接口给出的「您是不是要查」建议词（拼写纠错）。"""
    data = await _request(
        client,
        {'list': 'search', 'srsearch': keyword, 'srlimit': 1, 'srinfo': 'suggestion'},
    )
    suggestion = (data.get('query') or {}).get('searchinfo', {}).get('suggestion')
    return suggestion.strip() if suggestion else None


async def _search_titles(client: AsyncClient, keyword: str, limit: int = 6) -> list[str]:
    """取搜索结果里的候选标题，供本地近似匹配兜底。"""
    data = await _request(
        client,
        {'list': 'search', 'srsearch': keyword, 'srlimit': limit},
    )
    return [item['title'] for item in (data.get('query') or {}).get('search', []) if item.get('title')]


async def search_wiki_page(keyword: str) -> tuple[dict | None, str]:
    """搜索词条，返回 (页面, 实际使用的关键词)。

    依次尝试：直接搜索 -> 拼写建议 -> 截断中文问句 -> 本地近似标题，
    全部失败返回 (None, 原关键词)。
    """
    cached = _cache_get(keyword)
    if cached is not _CACHE_MISS:
        return cached

    used = keyword
    async with AsyncClient(timeout=WIKI_TIMEOUT) as client:
        page, score = await _search_best(client, keyword)

        # 1) 只是全文命中（比如召回版本日志）时，先试接口给的拼写建议
        if score < WIKI_GOOD_SCORE:
            suggestion = await _suggest(client, keyword)
            if suggestion:
                candidate, candidate_score = await _search_best(client, suggestion)
                if candidate_score > score:
                    page, score, used = candidate, candidate_score, suggestion

        # 2) 仍然不贴合，就截掉「有哪些 / 怎么获得」这类疑问词再搜一次
        if score < WIKI_GOOD_SCORE:
            trimmed = _trim_question(keyword)
            if trimmed and trimmed != keyword:
                candidate, candidate_score = await _search_best(client, trimmed)
                if candidate_score > score:
                    page, score, used = candidate, candidate_score, trimmed

        # 3) 完全没有结果，才用本地近似标题兜底
        if page is None:
            titles = await _search_titles(client, keyword)
            # 只在标题与输入足够接近时采用，避免把无关词条当成结果
            close = difflib.get_close_matches(keyword, titles, n=1, cutoff=WIKI_FUZZY_CUTOFF)
            if close:
                candidate, _ = await _search_best(client, close[0])
                if candidate is not None:
                    page, used = candidate, close[0]

    result = (page, used)
    _cache_put(keyword, result)
    return result


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
    """把词条标题转成 Wiki 原文链接（空格换下划线，其余按 URL 规则编码）。"""
    return WIKI_PAGE_URL + urllib.parse.quote(title.replace(' ', '_'), safe='')


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


async def build_wiki_reply(page: dict, keyword: str, used: str) -> Message:
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

    hint = f'（已按「{used}」搜索）\n' if used.strip().lower() != keyword.strip().lower() else ''
    message += Message(f'{hint}【{title}】\n{summary}\nWiki 原文指北：{link}')
    return message


@wiki_cmd.handle()
async def handle_wiki(args: Message = CommandArg()):
    keyword = args.extract_plain_text().strip()
    if not keyword:
        await wiki_cmd.finish('请输入要查询的词条。示例：.wiki lava chicken')

    # try 只包住网络请求；finish() 放在 try 之外，避免它的 FinishedException 被误 catch。
    try:
        page, used = await search_wiki_page(keyword)
    except WikiUnavailable as error:
        await wiki_cmd.finish(f'Wiki 服务器暂时不可用（HTTP {error.status}），可能正在维护，稍后再试。')
        return
    except Exception as error:
        await wiki_cmd.finish(f'Wiki 查询失败：{str(error)[:40]}')
        return

    if page is None:
        await wiki_cmd.finish(f'没有找到与「{keyword}」相关的词条，换个关键词试试？')

    await wiki_cmd.finish(await build_wiki_reply(page, keyword, used))


__plugin_meta__ = PluginMetadata(
    name='Minecraft Wiki 查询',
    description='搜索中文 Minecraft Wiki 词条，返回简介、缩略图与原文链接（数据来源：zh.minecraft.wiki）',
    usage='.wiki <关键词>\n示例：.wiki lava chicken',
    extra={'author': 'vvvvdg', 'version': '1.1'},
)

register_help_commands([
    {
        'name': 'wiki',
        'description': '搜索 Minecraft Wiki 词条并返回简介与原文链接',
        'usage': '/wiki <关键词>',
    },
])
