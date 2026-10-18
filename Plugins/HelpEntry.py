"""把 Plugins/ 里的指令补登记进 /help 的命令列表。

UniBot 里有两套互不相通的命令体系：

- 扩展体系（``Scripts.Extensions.Command``）：自带结构化参数，``/help`` 读的就是它，
  由 ``Scripts.Plugins.Extensions`` 在其它插件加载**之前**就完成构建；
- NoneBot 体系（``nonebot.on_command`` / ``on_message``）：``Plugins/`` 下的插件都走这套。

所以默认情况下 ``/help`` 看不到 ``Plugins/`` 里的任何指令。本模块在命令管理器构建
完成之后，补登记一批「只用于展示」的节点：不创建 matcher、不接管任何消息，
仅让 Help 扩展能把它们列出来。
"""

from Scripts.Extensions import Command, command_manager
from Scripts.Logging import logger


class DisplayCommand(Command):
    """仅用于 /help 展示的命令节点（不构建 matcher，handler 永远不会有调用方）。"""

    def __init__(
        self,
        name: str,
        description: str = '',
        usage: str | None = None,
        aliases: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.name = name
        self.description = description
        self.usage = usage
        self.aliases = aliases


def _build_node(item: dict) -> DisplayCommand:
    """把一条精简描述转换成展示用命令节点。"""
    return DisplayCommand(
        name=item['name'],
        description=item.get('description', ''),
        usage=item.get('usage'),
        aliases=tuple(item.get('aliases', ())),
    )


def register_help_commands(commands: list[dict]) -> None:
    """登记一组展示用命令。

    命令管理器在插件加载前就已构建完成（``_built`` 会拒绝新登记），这里临时放行
    以复用公开的 ``register_command`` 接口；同名指令只保留先登记的那个。
    """
    was_built = getattr(command_manager, '_built', True)
    command_manager._built = False
    registered: list[str] = []
    try:
        for item in commands:
            try:
                command_manager.register_command(_build_node(item), f'builtin:{item["name"]}')
            except Exception as error:
                logger.debug(f'/help 指令 {item.get("name")} 登记失败，已跳过：{error}')
            else:
                registered.append(item['name'])
        logger.debug(f'/help 已登记 {len(registered)} 条指令：{", ".join(registered)}')
    finally:
        command_manager._built = was_built
