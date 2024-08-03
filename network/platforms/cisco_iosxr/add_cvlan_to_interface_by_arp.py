from ...command import Command


class AddCVLANToInterfaceByArp(Command):
    '''
    Find interface having ARP entry for given IP.  Add CVLAN to that interface.
    '''
    def __init__(self, ip, cvlan):
        self.ip = ip
        self.cvlan = cvlan

    async def execute(self, executer):
        for arp in await executer.run('arp'):
            if arp['ip_address'] == self.ip:
                interface = arp['interface'].split('.')[0]
                break
        else:
            raise Exception(f'Could not find ARP for {self.ip}')
        return await executer.run('add_cvlan_to_interface', interface, self.cvlan)
