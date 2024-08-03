from typing import List, Dict, Any
from ...command import Command


class Arp(Command):

    async def execute(self, executer) -> List[Dict[str, Any]]:
        cmd = 'show arp'
        response = await executer.conn.send_command(cmd)
        return response.textfsm_parse_output()
