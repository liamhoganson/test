"""import requests
from os import environ as env


async def get_free_vlan(prefix: str) -> dict | None:
    free_vlan = None

    for vlan_id in range(1024, 2001):
        prefix_resp = await requests.get(env['NETBOX_REST_API_URL'] + f"/ipam/prefixes/{vlan_id}/",
                                   headers={"Authorization": f"Token {env['NETBOX_TOKEN']}"}).json()

        if "tenant" not in prefix_resp:
            continue

        if prefix_resp["tenant"] is None and prefix_resp["prefix"] == prefix:
            free_vlan = prefix_resp

    return free_vlan


async def assign_free_vlan(vlan_id: int, tenant_name: str) -> dict | None:
    resp = await requests.patch(env['NETBOX_REST_API_URL'] + f"/ipam/prefixes/{vlan_id}/",
                                 headers={"Authorization": f"Token {env['NETBOX_TOKEN']}"},
                                 json={"tenant": {"name": f"{tenant_name}"}})

    if resp.status_code == 200:
        resp = resp.json()

        return {
            "message": f"Tenant {tenant_name} was assigned to VLAN {resp['vlan']['name']}"
        }

    else:
        return None
"""
