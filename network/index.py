# Imports
import json
from string import Template
import logging
import logging.config
from aio_pika import IncomingMessage
from gql import Client, gql
from busboy import BaseConsumer, BasePublisher, BaseRpcServer
from pydantic import BaseModel
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.aiohttp import log as gql_logger
from scrapli.driver.core import AsyncIOSXRDriver
from scrapli.driver.base.base_driver import BaseDriver

from .utils import CommandExecuter
from .provision_cvlan import ProvisionCVLAN
from .platforms import cisco_iosxr

# Defining variables
gql_logger.setLevel(logging.CRITICAL)
log = logging.getLogger('drivers/network')

async def get_site_router_type(API_TOKEN, NETBOX_API_URL, IP_ADDRESS):
    NETBOX_API_URL = "https://netbox.utbb.net/graphql/"
    QL_HEADERS = {"Authorization": f"Token {API_TOKEN}"}
    QL_TRANSPORT = AIOHTTPTransport(url=NETBOX_API_URL, headers=QL_HEADERS)
    client = Client(transport=QL_TRANSPORT, fetch_schema_from_transport=False)

    # Gets Router's Primary IPv4 IP ID
    router_ip_id_query = gql("""
    query DeviceIDFromIPAddress($ip: StrFilterLookup!) {
        ip_address_list(filters: {address: $ip}) {
            id
        }
    }
    """)

    router_info_query = gql("""
    query DeviceInfoFromID($id: [String!]){
        device_list(filters: {primary_ip4_id: $id}){
            device_type{
                manufacturer{
                    name
                }
                default_platform {
                    slug
                }
            }
            site
            {
                slug
            }
        }
    }
    """)

    router_ip_id_query_variables = {
        "ip": {
            "exact": IP_ADDRESS
        }
    }

    try:
        async with client as session:
            get_router_ip = await session.execute(router_ip_id_query, variable_values=router_ip_id_query_variables)
            primary_ip4_id = get_router_ip.get("ip_address_list")[0].get("id")
            log.info(f'Got primary_ip4_id f{primary_ip4_id}')
            router_info_query_variables = {
                "id": [str(primary_ip4_id)]
            }
            get_router_data = await session.execute(router_info_query, variable_values=router_info_query_variables)
            log.info(f'Received router data {get_router_data}')
            return [
                get_router_data.get("device_list")[0].get("device_type").get("default_platform").get("slug"),
                get_router_data.get("device_list")[0].get("site").get("slug")
            ]
    except:
        log.error("Error: Could not get router platform")
        raise


class RouterModel(BaseModel):
    routing_key: str
    router_ip: str
    ip: str
    customer_vlan: int

    class Config:
        extra = "allow"


class ProvisionCVLANRequest(BaseModel):
    site_id: int
    arp_ip: str
    cvlan: int

    class Config:
        extra = "allow"


class Network(BaseRpcServer, BaseConsumer, BasePublisher):
    name = "Network"
    binding_keys = ["rpc.network.router.add_cvlan_to_interface_by_arp"]
    model = RouterModel

    def __init__(self):

        provision_cvlan = ProvisionCVLAN(self)

    def ssh_factory(self, host: str, netbox_platform_slug: str) -> BaseDriver:
        device = {
            'host': host,
            'auth_username': self.driver.config.ssh_user,
            'auth_password': self.driver.config.rtr_ssh_pass,
            'transport': 'asyncssh'
        }
        match netbox_platform_slug:
            case 'ios-xr':
                device.update({
                    'textfsm_platform': 'cisco_xr'
                })
                return AsyncIOSXRDriver(**device)


    async def consume(self, message: IncomingMessage):
        log.info(f"Entered Router Consumer.")

        if not message.body:
            print(f"Errors with getting Router consumer message: {message}")
            return None


        # Getting variables from .conf
        NETBOX_API_TOKEN = self.config.netbox_api_key
        NETBOX_API_URL = self.config.netbox_api_url
        SSH_USER = self.config.ssh_user
        SSH_PASS = self.config.ssh_pass
        RTR_PASS = self.config.rtr_ssh_pass

        if message.routing_key == "rpc.network.provision_cvlan":
            try:
                request = ProvisionCVLANRequest.model_validate_json(message.body)
                result = await self.provision_cvlan(request)
                self.reply({'error': None, 'res': result})
            except Exception as e:
                msg = str(e)
                log.error(msg)
                self.reply({'error': msg, 'res': None})
                raise

        elif message.routing_key == "rpc.network.router.add_cvlan_to_interface_by_arp":
            try:
                # Extracting the message body and parsing it as JSON
                body = json.loads(message.body)

                # Parse the body into the RouterModel
                router_data = RouterModel(**body)
                site_data = await get_site_router_type(
                    NETBOX_API_TOKEN, NETBOX_API_URL, router_data.router_ip
                )
                log.info(site_data)
                nb_platform = str(site_data[0])
                site_slug = str(site_data[1])

                device = {
                    'host': router_data.router_ip,
                    'auth_username': self.config.ssh_user,
                    'auth_password': self.config.rtr_ssh_pass,
                    'auth_strict_key': False,
                    'transport': 'asyncssh'
                }

                match nb_platform:
                    case "ios-xr":
                        network_driver = AsyncIOSXRDriver
                        command = cisco_iosxr.AddCVLANToInterfaceByArp
                        device['textfsm_platform'] = 'cisco_xr'
                    # TODO: case "routeros":
                    # TODO: case "extreme":
                    case _:
                        log.error("Unknown router platform.")
                        await self.reply({ "error": "Unknown router platform", "res": None }, message)
                        raise Exception("Unknown router platform")


                async with network_driver(**device) as conn:
                    command = command(router_data.ip, router_data.customer_vlan)
                    executer = CommandExecuter(conn)
                    await executer.run(command)
                await self.reply({ "error": None, "res": "Router/Switch Config successfully completed" }, message)
            except Exception as e:
                await self.reply({ "error": f"{e}", "res": None }, message)
                raise Exception(f"Error in SM Config - {e}")


    async def slack_post(self, message):
        msg = {
                "channel": self.config.slack_prov_chan,
                "text": message
            }
        await self.publish(msg, "chat.message.post")


    async def execute(self, *args, **kwargs):
        async with self.limiter, self.client as session:
            return await session.execute(*args, **kwargs)
