import logging
from pathlib import Path
from textfsm.clitable import CliTable
from typing import Optional, TextIO

logger = logging.getLogger(__name__)

def get_template(platform: str, command: str) -> Optional[TextIO]:
    current_dir = Path(__file__).parent
    template_dir = str(current_dir / 'templates')
    cli_table = CliTable('index', template_dir)
    template_index = cli_table.index.GetRowMatch({'Platform': platform, 'Command': command})
    if not template_index:
        logger.warning(
            f'No match in ntc_templates index for platform `{platform}` and command `{command}`'
        )
        return None
    template_name = cli_table.index.index[template_index]['Template']
    return open(f'{template_dir}/{template_name}', encoding='utf-8')


class CommandExecuter(object):
    def __init__(self, conn):
        self.conn = conn

    async def run(self, command):
        return await command.execute(self)
