import json
import pynetbox
from aio_pika import IncomingMessage
from gql import gql
from .index import Netbox


# Get Management Prefix and VLAN based on the Reg VLAN name
async def get_vlan_and_prefix(self, message: IncomingMessage):

    # Get and verify message contains Reg VLAN
    if not message.body:
        print("Errors with Get VLAN and Prefix Message: {message}")
        return None

    #print("Getting Reg VLAN")
    reg_vlan_id = json.loads(message.body).get("account_id")

    if not reg_vlan_id:
        print("Get VLAN and Prefix missing Reg VLAN")
        return None


    query = gql('''
            query getVLANandPrefix($name: [String])
            {
                tenant_list(name: $name)
                {
                    id
                    name
                }
            }
        ''')


    res = await self.execute(
        query,
        variable_values={
            'name': f"ubb-{acc_id}"
        }
    )

    print(f"Get VLAN and Prefix Callback Received: {res}")

    if not len(res.get("tenant_list")):
        print("Get VLAN and PRefix request returned empty response")
        return None

    return res


Netbox.get_vlan_and_prefix = get_vlan_and_prefix
