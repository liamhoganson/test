import json
import re
import pynetbox
from aio_pika import IncomingMessage
from gql import gql
from .index import Netbox


# Get Management Prefix and VLAN based on the Reg VLAN name
async def get_router_ip(self, message: IncomingMessage):

    # Get and verify message contains Reg VLAN
    if not message.body:
        print("Errors with Get Router IP Message: {message}")
        return None

    vlan_id = json.loads(message.body).get("vlan_id")

    if not vlan_id:
        print("Get Router IP missing VLAN ID")
        return None


    query = gql('''
        query getRouterIP($id: Int!)
        {
            vlan(id:$id)
            {
                site
                {
                    devices
                    {
                         name
                        role
                        {
                            id
                            name
                        }
                        primary_ip4
                        {
                            address
                        }
                    }
                }
            }
        }
        ''')


    res = await self.execute(
        query,
        variable_values={
            'id': int(vlan_id)
        }
    )

    print(f"Get Router IP Callback Received: {res}")
    router_ip = None
    for device in res.get("vlan").get("site").get("devices"):
        if device.get("role").get("name") == "Router":
            router_ip = re.sub("\/\d*$", "", device.get("primary_ip4").get("address"))
            print(f"Found Router: {router_ip}")

    ap_name = json.loads(message.body).get("ap_name")
    access_point_ip = None
    for device in res.get("vlan").get("site").get("devices"):
        if device.get("role").get("name") == "AP" and device.get("name") == ap_name:
            access_point_ip = re.sub("\/\d*$", "", device.get("primary_ip4").get("address"))
            print(f"Found Access Point IP: {access_point_ip}")

    if router_ip is None or access_point_ip is None:
        raise Exception("Get Router IP Failed to find Router or Access Point")

    return { "router_ip": router_ip, "access_point_ip": access_point_ip }




Netbox.get_router_ip = get_router_ip
