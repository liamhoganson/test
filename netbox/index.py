import asyncio
import logging
import json
import re
import pynetbox
from pydantic import BaseModel, Extra
from aio_pika import IncomingMessage
from aiolimiter import AsyncLimiter
from gql import Client
from gql.transport.httpx import HTTPXAsyncTransport
from busboy import BaseRpcServer, BaseEndpoint, BaseRpcClient

log = logging.getLogger()

class NetboxModel(BaseModel):
    model: str
    event: str

    class Config:
        extra = 'allow'

class Netbox(BaseRpcServer, BaseEndpoint, BaseRpcClient):
    name = "netbox"
    binding_keys = [
        "rpc.dcim.get_reg_vlan",
        "rpc.dcim.get_mgmt_id_by_reg",
        "rpc.dcim.tenant_verification",
        "rpc.dcim.vlan_verification",
        "rpc.dcim.get_router_ip",
        "rpc.dcim.assign_tenant_vlan"
    ]
    model = NetboxModel

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.config.netbox_api_url:
            raise Exception('Unable to get Netbox URL from config')
        url = self.config.netbox_api_url

        self.transport = HTTPXAsyncTransport(
            url=url + '/graphql/',
            headers={
                "Authorization": f"Token {self.config.netbox_api_key}",
                "Accept": "application/json"
            }
        )

        self.api = pynetbox.api(url, token=self.config.netbox_api_key)
        self.client = Client(transport=self.transport, fetch_schema_from_transport=False)
        self.limiter = AsyncLimiter(40.0, 1.0)  # TODO: Make these config values with sane defaults

    async def consume(self, message: IncomingMessage) -> None:
        log.debug("Entered Netbox consumer")
        body = json.loads(message.body)

        if message.routing_key == "do_rpc":
            log.debug("Got webhook. Making RPC call")
            log.debug(await self.rpc_call("rpc.dcim.customer.get.by_id", "420791"))

        # It should just take an IP address. Not a whole message
        if message.routing_key == "rpc.dcim.get_reg_vlan":
            ip = body.get("ip")
            result = await self.get_reg_vlan(ip)
            if not result:
                await self.reply({ "error": "Fetch VLAN failed: Likely Bad IP", "res": None }, message)
                raise Exception("Fetch VLAN failed: Likely Bad IP")
            else:
                log.info(f"VLAN and Site Info Received: {result}")
                # Returns the VLAN and Site data dict as a string, to be converted back on the other side
                await self.reply({ "error": None, "res": result }, message)

        if message.routing_key == "rpc.dcim.get_mgmt_id_by_reg":
            result = await self.get_mgmt_id_by_reg(body.get('name'))
            if not result:
                await self.reply({ "error": "Failed to fetch mgmt VLAN ID", "res": None }, message)
                raise Exception("Fetch VLAN failed: Likely Bad IP")
            else:
                log.info(f"Found mgmt VLAN ID: {result}")
                await self.reply({ "error": None, "res": result }, message)

        if message.routing_key == "rpc.dcim.tenant_verification":
            log.debug("Netbox got Verify Tenant Request")
            #Does the Tenant Already Exist?
            tenant_id = ""
            try:
                tenant_id = await self.does_tenant_exist(message)
            except Exception as e:
                await self.reply({ "error": f"{e}", "res": None }, message)
                raise Exception(f"Error Looking for Existing Tennant - {e}")

            #if not, make it
            if not tenant_id:
                log.debug("Failed to find tenant. Creating new one.")
                try:
                    #Create the tenant, then verify that it actually worked.
                    creation_success = await self.create_tenant(message)
                    if creation_success:
                        tenant_id = await self.does_tenant_exist(message)
                except Exception as e:
                    await self.reply({ "error": f"{e}", "res": None }, message)
                    raise Exception(f"Error when Creating a new Tenant - {e}")

            if not tenant_id:
                await self.reply({ "error": "Netbox failed to find or generate tenant", "res": None }, message)
                raise Exception("Netbox Failed find or Generate Tenant")
            log.debug("Returning tenant")
            await self.reply({ "error": None, "res": tenant_id}, message) #Returns the VLAN and Site data dict as a string, to be converted back on the other side

        if message.routing_key == "rpc.dcim.vlan_verification":
            log.debug("Netbox got Verify VLAN Request")
            try:
                vlan_vid_set = await self.verify_tenant_vlan(message)
            except Exception as e:
                await self.reply({ "error": f"{e}", "res": None }, message)
                raise Exception(f"Error Verifying VLAN Request - {e}")

            if vlan_vid_set is None:
                await self.reply({ "error": "VLAN Verification failed to find and assign vlan", "res": None }, message)
                raise Exception("VLAN Verification failed to find and assign vlan")

            log.debug("vlan assigned and verified")
            await self.reply({ "error": None, "res": vlan_vid_set }, message) #Returns the VLAN and Site data dict as a string, to be converted back on the other side

        if message.routing_key == "rpc.dcim.assign_tenant_vlan":
            log.debug('Netbox got Assign Tenant VLAN Request')
            body = json.loads(message.body)
            vlan = None
            try:
                vlan = await self.assign_tenant_vlan(self, body['site_id'], body['tenant_id'])
            except Exception as e:
                await self.reply({ "error": f"{e}", "res": None }, message)
                raise Exception(f"Error Assigning Tenant VLAN - {e}")

            log.debug('vlan assigned')
            await self.reply({"error": None, "res": vlan.model_dump() }, message)

        if message.routing_key == "rpc.dcim.get_router_ip":
            log.debug("Netbox got Get Router IP Request")
            router_and_ap_ips = None
            try:
                router_and_ap_ips = await self.get_router_ip(message)
            except Exception as e:
                await self.reply({ "error": f"{e}", "res": None }, message)
                raise Exception(f"Error Retrieving Router IP - {e}")

            log.debug("router ip acquired")
            await self.reply({ "error": None, "res": router_and_ap_ips }, message) #Returns the VLAN and Site data dict as a string, to be converted back on the other side

    async def execute(self, *args, **kwargs):
        async with self.limiter, self.client as session:
            return await session.execute(*args, **kwargs)

    def get_routing_key(self, body: dict) -> str:
        data = self.model(**body)
        return f"{data.event}"
