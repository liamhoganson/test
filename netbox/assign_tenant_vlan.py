'''Assign a VLAN to an account in Netbox'''
from typing import Optional, List
from gql import gql
from gql.transport.exceptions import TransportQueryError
from pydantic import BaseModel

from .index import Netbox


class Site(BaseModel):
    '''Netbox site'''
    id: int


class Tenant(BaseModel):
    '''Netbox site'''
    id: int


class Vlan(BaseModel):
    '''Netbox Vlan'''
    id: int
    vid: int
    site: Site
    tenant: Optional[Tenant]


QUERY_VLANS_BY_TENANT = gql('''
    query VlansByTenant($id: [String!]){
        vlan_list(filters: {tenant_id: $id}){
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

QUERY_TENANT_VLANS_BY_SITE = gql('''
    query TenantVlansBySite($id: [String!]){
        vlan_list(
            filters: {
                AND: {
                    site_id: $id,
                    vid: {gte: 1024, lt: 3072}
                }
            }
        ){
            id
            vid
            site {
                id
            }
            tenant {
                id
            }
        }
    }
''')

QUERY_SITE = gql('''
    query SiteExists($id: Int!){
        site(id: $id) {
            id
        }
    }
''')

QUERY_TENANT = gql('''
    query TenantExists($id: Int!){
        tenant(id: $id) {
            id
        }
    }
''')

class NetboxAssignVLANException(Exception):
    '''General Exception for Assigining VLANs'''


class AssignTenantVlan(object):
    '''Assign VLAN to Tenant'''

    async def __call__(self, driver: Netbox, site_id: int, tenant_id: int) -> Vlan:
        '''Entry point for driver'''
        self.driver = driver
        return await self.assign_tenant_vlan(site_id, tenant_id)

    def _path_in_errors(self, errors: list, path: str) -> bool:
        '''Check if path is in error'''
        for error in errors:
            for p in error.get('path', []):
                if p == path:
                    return True
        return False

    async def assign_tenant_vlan(self, site_id: int, tenant_id: int) -> Vlan:
        '''Main logic'''
        vlan = await self.tenant_vlan(tenant_id)

        if vlan is not None:
            if vlan.site.id == site_id:
                return vlan
            self.release_vlan(vlan.id)

        return await self.assign_next_vlan(site_id, tenant_id)

    async def tenant_vlan(self, tenant_id: int) -> Optional[Vlan]:
        '''Return VLAN assigned to tenant'''
        # If tenant id does not exist, netbox returns all VLANs.
        # Check for tenant first
        await self.tenant_exists(tenant_id)

        try:
            res = await self.driver.execute(
                QUERY_VLANS_BY_TENANT,
                variable_values={
                    'id': [str(tenant_id)]
                }
            )

            vlans = [Vlan(**x) for x in res['vlan_list']]

            if vlans:
                if len(vlans) > 1:
                    # This is a business logic fault, bail
                    # TODO: Handle this gracefully
                    raise NetboxAssignVLANException('Tenant has more than one vlan')
                return vlans[0]

        except TransportQueryError as e:
            if self._path_in_errors(e.errors, 'tenant'):
                raise NetboxAssignVLANException(
                    f'Tenant {tenant_id} does not exist.'
                ) from e
            raise NetboxAssignVLANException from e

    async def site_exists(self, site_id: int) -> bool:
        '''Raise exception if site does not exist'''
        try:
            res = await self.driver.execute(
                QUERY_SITE,
                variable_values={
                    'id': int(site_id)
                }
            )

            # Double check in case API changes and returns a successful
            # result with null site.  Should not get here.
            if res.get('site') is None:
                raise NetboxAssignVLANException(f'Site {site_id} does not exist')
            return True

        except TransportQueryError as e:
            if self._path_in_errors(e.errors, 'site'):
                raise NetboxAssignVLANException(
                    f'Site {site_id} does not exist.'
                ) from e
            raise NetboxAssignVLANException from e

    async def tenant_exists(self, tenant_id: int) -> bool:
        '''Raise exception if tenant does not exist'''
        try:
            res = await self.driver.execute(
                QUERY_TENANT,
                variable_values={
                    'id': int(tenant_id)
                }
            )

            # Double check in case API changes and returns a successful
            # result with null site.  Should not get here.
            if res.get('tenant') is None:
                raise NetboxAssignVLANException(f'Tenant {tenant_id} does not exist')
            return True

        except TransportQueryError as e:
            if self._path_in_errors(e.errors, 'tenant'):
                raise NetboxAssignVLANException(
                    f'Tenant {tenant_id} does not exist.'
                ) from e
            raise NetboxAssignVLANException from e


    async def site_tenant_vlans(self, site_id: int) -> List[Vlan]:
        '''Return all VLANs that can be assigned to a tenant'''
        # GraphQL AND does not seem to work when site id doesn't exist,
        # and instead returns VLANs for all sites.
        await self.site_exists(site_id)

        # TODO: Range is business logic, maybe check for role instead of range
        # At time of writing Role is NAT Access, but VLAN doesn't define whether
        # prefix is NAT or Public, should change role to "Tenant" first

        try:
            res = await self.driver.execute(
                QUERY_TENANT_VLANS_BY_SITE,
                variable_values={
                    'id': str(site_id)
                }
            )
            if not res.get('vlan_list'):
                raise NetboxAssignVLANException(f'No assignable VLANs found for site {site_id}')
            return [Vlan(**x) for x in res['vlan_list']]

        except TransportQueryError as e:
            raise NetboxAssignVLANException from e

    async def next_free_tenant_vlan(self, site_id: int) -> Vlan:
        '''Return first VLAN on a site that can be assigned to a tenant'''
        for site_vlan in await self.site_tenant_vlans(site_id):
            if site_vlan.tenant is None:
                return site_vlan
        raise NetboxAssignVLANException(f'No free VLANs for site ID {site_id}')

    async def assign_next_vlan(self, site_id: int, tenant_id: int) -> Vlan:
        '''Assign next free VLAN to tenant'''
        site_vlan = await self.next_free_tenant_vlan(site_id)
        nb_vlan = self.driver.api.ipam.vlans.get(site_vlan.id)
        if not nb_vlan:
            raise NetboxAssignVLANException(f'Unable to retrieve VLAN {site_vlan.id}')
        nb_vlan.tenant = tenant_id
        nb_vlan.save()
        return await self.tenant_vlan(tenant_id)

    def release_vlan(self, vlan_id: int):
        '''Remove tenant from VLAN'''
        nb_vlan = self.driver.api.ipam.vlans.get(vlan_id)
        if not nb_vlan:
            raise NetboxAssignVLANException(f'Unable to retrieve VLAN {vlan_id}')
        nb_vlan.tenant = None
        nb_vlan.save()


Netbox.assign_tenant_vlan = AssignTenantVlan()
