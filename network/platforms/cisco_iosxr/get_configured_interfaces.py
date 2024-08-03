from io import BytesIO, TextIOWrapper

template = '''\
Value List INTERFACES (\S+)

Start
  ^interface(\s+preconfigure)?\s+${INTERFACES} -> Continue
'''


class GetConfiguredInterfaces(object):
    '''
    Returns interfaces in configuration
    '''

    async def execute(self, executer):
        cmd = 'show run | inc ^interface'
        response = await executer.conn.send_command(cmd)
        bytes_io = BytesIO(template.encode())
        text_io = TextIOWrapper(bytes_io)
        return response.textfsm_parse_output(text_io)[0]['interfaces']
