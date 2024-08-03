import json
from aio_pika import IncomingMessage
from gql import gql
import pynetbox
from typing import Optional, List, Tuple
from .index import Netbox

from pydantic import BaseModel


class Site(BaseModel):
    id: int


class Vlan(BaseModel):
    id: int
    vid: int
    name: str
    site: Site


class Tenant(BaseModel):
    id: int
    name: str
    slug: str
    vlans: List[Vlan] = []

class SiteTenant(BaseModel):
    id: int

class SiteVlan(BaseModel):
    id: int
    vid: int
    name: str
    site: Site
    tenant: Optional[SiteTenant]


# Checks if Netbox contains the tenant using the account id
# Returns the vlan id and vlan vid
async def verify_tenant_vlan(self, message: IncomingMessage) -> Optional[Tuple[int, int]]:
    ''' Checks if Netbox contains the tenant using
        Returns tuple of Nebox vlan id and vlan vid
    '''

    # Get and verify message contains tenant_id and site_id
    if not message.body:
        print("Errors with Tenant VLAN Message: {message}")
        return None

    #print("Getting Tenant ID")
    tenant_id = int(json.loads(message.body).get("tenant_id"))  # Unkown type, cast to int

    if not tenant_id:
        print("tenant VLAN Verification missing tenant ID")
        return None

    #print("Getting Site ID")
    site_id = int(json.loads(message.body).get("site_id"))  # Unkown type, cast to int

    if not site_id:
        print("tenant VLAN Verification missing Site ID")
        return None

    query = gql('''
        query returnTenantVLAN($id: Int!){
            tenant(id: $id){
                id
                name
                slug
                vlans {
                    id
                    vid
                    name
                    site {
                        id
                    }
                }
            }
        }
    ''')
    res = await self.execute(
        query,
        variable_values={
            'id': tenant_id
        }
    )

    print(f"Does Tenant have VLAN Callback Received: {res}")


    if res.get("tenant") is not None:
        tenant = Tenant(**res['tenant'])
    else:
        raise Exception("Verify tenant VLAN failed to find tenant")

    # Does the tenant already have a VLAN
    if tenant.vlans:
        if len(tenant.vlans) > 1:
            # This is business logic fault, bail
            # TODO: Handle this gracefully
            raise Exception('Tenant has more than one vlan')
        print('Tenant has VLAN')
        vlan = tenant.vlans[0]
        if site_id == vlan.site.id:
            return { "vlan_id": vlan.id, "vlan_site_id": vlan.vid }

        # vlan does not belong to site, release it
        try:
            nb_vlan = self.api.ipam.vlans.get(vlan.id)
            if not nb_vlan:
                raise Exception(f'Could not release VLAN, no VLAN with Netbox ID {vlan.id} found')
            nb_vlan.tenant = None
            nb_vlan.save() #update change on server

        except pynetbox.RequestError as e:
            raise Exception(f"VLAN Release Returned an error, {e.error}")

        print(f"VLAN Released: {res}")


    # Assign VLAN
    print("Assigning VLAN")
    try:
        #Get VLAN object
        print("Searching for Available VLAN")
        # We need to find a free Customer VLAN
        # TODO: Range is business logic, maybe check for role instead of range
        query = gql('''
            query getAvailableVLANsFromSite($id: [String!]){
                vlan_list(filters: {site_id:$id, vid: {gte: 1024, lt: 3072}}){
                    id
                    vid
                    name
                    site {
                        id
                    }
                    tenant {
                        id
                    }
                }
            }
        ''')
        res = await self.execute(
            query,
            variable_values={
                'id': str(site_id)
            }
        )

        print("Received Site VLAN Callback")

        if not res.get('vlan_list'):
            raise Exception(f'No available vlans found for Netbox Site ID {site_id}')

        next_vlan = None
        for vlan in res['vlan_list']:
            vlan = SiteVlan(**vlan)
            if vlan.tenant is None:
                next_vlan = vlan
                break

        nb_vlan = self.api.ipam.vlans.get(next_vlan.id)
        if nb_vlan is None:
            raise Exception(f'Could not assign VLAN, unable to retrieve Netbox VLAN ID {next_vlan.id}')

        print(f"Retrieved Available Customer VLAN: {nb_vlan.name}")

        #Modify Object
        nb_vlan.tenant = tenant.id
        nb_vlan.save() #update change on server

        nb_vlan_modified = self.api.ipam.vlans.get(next_vlan.id)
        if not nb_vlan_modified or nb_vlan_modified.tenant.id != tenant.id:
            raise Exception(f"Failed to update VLAN tenant.")

        print(f"VLAN {next_vlan.name} Added to tenant {tenant.slug}")

        return { "vlan_id": next_vlan.id, "vlan_site_id": next_vlan.vid }

    except pynetbox.RequestError as e:
        raise Exception(f"VLAN Assignment Returned an error, {e.error}")

Netbox.verify_tenant_vlan = verify_tenant_vlan
