"""
天气查询小功能。

触发方式：群里发送「.weather <城市名>」。
"""

from httpx import AsyncClient
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from .Config import plugin_config
from .HelpEntry import register_help_commands

# 天气接口
WEATHER_API_URL = 'https://uapis.cn/api/v1/misc/weather'

# priority=0：必须小于 Scripts.Plugins.Events 的 priority=1（它以 block=True 截断事件）。
weather_cmd = on_command('weather', priority=0, block=True)


async def fetch_weather_report(city_name: str) -> str:
    """
    请求天气接口并组装回复文本，只返回文本、不发送消息。

    这里绝不允许出现 matcher.finish()/send()：finish() 依赖抛出
    FinishedException 中断 handler，而它是 Exception 的子类，一旦被本函数的
    except 捕获，就会被误报成「查询失败：FinishedException()」。
    """
    params = {
        'city': city_name,
        'extended': False,
        'forecast': True,
        'hourly': False,
        'minutely': False,
        'indices': False,
        'lang': 'zh',
    }
    headers = {'Authorization': f'Bearer {plugin_config.uapipro_token}'}

    async with AsyncClient(timeout=10) as client:
        response = await client.get(WEATHER_API_URL, params=params, headers=headers)
        if response.status_code != 200:
            return f'天气接口请求失败：HTTP {response.status_code}'
        result = response.json()

    if not result:
        return f'未查询到「{city_name}」的天气信息'

    province = result.get('province', city_name)
    city = result.get('city', city_name)
    cur_weather = result.get('weather', '未知天气')
    cur_temp = result.get('temperature', 0)
    wind_dir = result.get('wind_direction', '未知')
    wind_power = result.get('wind_power', '0')
    humidity = result.get('humidity', 0)
    update_time = result.get('report_time', '数据未更新')

    forecast_data = result.get('forecast', [])
    tomorrow_weather = '暂无预报'
    if forecast_data:
        tomorrow = forecast_data[0]
        tomorrow_weather = (
            f"{tomorrow.get('weather_day', '未知')}，"
            f"{tomorrow.get('temp_min', 0)}℃~{tomorrow.get('temp_max', 0)}℃"
        )

    return f"""
【{province} · {city}】天气信息
实时数据更新：{update_time}
实时天气：{cur_weather}
实时温度：{cur_temp}℃
相对湿度：{humidity}%
风向风力：{wind_dir} {wind_power}
未来1天预报：
{tomorrow_weather}
    """.strip()


@weather_cmd.handle()
async def handle_weather(event: MessageEvent, args: Message = CommandArg()):
    city_name = args.extract_plain_text().strip()
    if not city_name:
        await weather_cmd.finish('请输入城市名！示例：.weather 北京')

    # try 只包住真正可能出错的请求逻辑；finish() 放在 try 之外，
    # 否则它抛出的 FinishedException 会被 except 吃掉并误报成查询失败。
    try:
        reply_msg = await fetch_weather_report(city_name)
    except Exception as error:
        reply_msg = f'查询失败：{str(error)[:40]}'

    await weather_cmd.finish(reply_msg)


__plugin_meta__ = PluginMetadata(
    name='天气查询',
    description='查询城市实时天气与未来一天预报（数据来源：uapis.cn）',
    usage='.weather <城市名>\n示例：.weather 北京',
    extra={'author': 'DICEALPHAHU', 'version': '1.0'},
)

register_help_commands([
    {
        'name': 'weather',
        'description': '查询城市实时天气与未来一天预报',
        'usage': '/weather <城市名>',
    },
])
