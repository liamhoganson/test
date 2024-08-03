import json
from gql import gql
from .index import Netbox

async def get_reg_vlan(self, ip: str) -> dict:
    query = gql('''
        query GetVLAN($ip: String!){
            # children 0 filters for the most relevant
            prefix_list(filters: {contains: $ip, children: "0"}){
                id
                prefix
                site {
                    id
                    name
                }
                vlan {
                    id
                    name
                    vid
                }
            }
        }
    ''')
    res = await self.execute(
        query,
        variable_values={
            'ip': ip
        }
    )

    #print(f"Site and VLAN Callback Received: {res}")

    if not len(res.get("prefix_list")):
        print("Site/VLAN request returned empty response")
        return None
    elif res.get("prefix_list")[0].get("vlan") is None:
        print(f"VLAN is Missing from site: {res}")
        return None

    return res

async def get_mgmt_id_by_reg(self, mgmt_vlan: str) -> int:
    print(mgmt_vlan)
    query = gql(f'''
       query {{
          vlan_list(filters: {{name: {{exact:"{mgmt_vlan.replace('-reg', '-mgmt')}"}}}}){{
            id
            vid
            name
          }}
        }}
   ''')
    res = await self.execute(query)
    return res['vlan_list'][0]['vid']


Netbox.get_reg_vlan = get_reg_vlan
Netbox.get_mgmt_id_by_reg = get_mgmt_id_by_reg 
