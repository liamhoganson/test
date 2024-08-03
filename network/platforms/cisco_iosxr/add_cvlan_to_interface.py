from ...command import Command

class AddCVLANToInterface(Command):
    '''
    Find interface having ARP entry for given IP.  Add CVLAN to that interface.
    '''
    def __init__(self, interface, cvlan):
        self.interface = interface
        self.cvlan = cvlan

    async def execute(self, executer):
        for interface in await executer.run('get_configured_interfaces'):
            parts = interface.split('.')
            if len(parts) > 1 and int(parts[1]) == self.cvlan:  # Found matching VLAN
                if parts[0] == self.interface:  # Nothing to do if already on interface
                    break
                configs = [
                    f'replace interface {interface} with {self.interface}.{self.cvlan}',
                    f'no interface {interface}',
                    'commit',
                    'exit'
                ]
                await executer.conn.send_configs(configs)
                break
        else:
            raise Exception(f'Could not find configured interface with vlan {self.cvlan}')
