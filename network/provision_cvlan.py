from pydantic import BaseModel
from typing import List

from .command import CommandExecutor
# from ..netbox.get_site_equipment import SiteEquipmentRespone, SiteEquipmentRequest

class EquipmentPlatform(BaseModel):
    pass

class SiteEquipment(BaseModel):
    primary_ip4: str
    default_platform: EquipmentPlatform


class ProvisionCVLAN(object):

    async def __call__(self, driver, request):
        self.driver = driver
        macs = []
        site_equipment = await self.get_site_equipment(request.site_id)

        for router in [x for x in site_equipment if x.role.name == 'Router']:
            net_driver = self.driver.ssh_factory(router.default_platform.slug)
            async with net_driver as conn:
                executor = CommandExecutor(conn)
                arp = await executor.run('arp', ip=request.arp_ip)
                interface = await executor.run('interface', interface=arp['interface'])
                macs.append(interface['mac_address'])
                await executor.run('add_cvlan_to_interface', interfaces=[interface['name']])

        for switch in [x for x in site_equipment if x.role.name == 'Switch']:
            net_driver = self.driver.ssh_factory(switch.default_platform.slug)
            async with net_driver as conn:
                executor = CommandExecutor(conn)
                for mac in macs:
                    entry = await executor.run('mac_table', mac=mac)
                    await executor.run('add_cvlan_to_interface', interfaces=entry['destination_port'])

    async def get_site_equipment(self, site_id) -> List[SiteEquipment]:
        response = await self.driver.rpc_call(
            'rpc.netbox.site_equipment',
            {
                'site_id': site_id
            }
        )
        return [SiteEquipment(**x) for x in response]
