import asyncio
import logging
import json
import re
from pydantic import BaseModel, Extra
from aio_pika import Exchange, IncomingMessage
from httpx import AsyncClient
from busboy import BaseRpcServer, BaseRpcClient

log = logging.getLogger()

# The provisioner acts as the locus of driver communication.
# When interacting with other drivers the provision model consumes their RabbitMQ calls,
# then publishes RabbitMQ calls to complete the requested operation
# (Example: DHCP receives a request. This is forwarded to Provisioner who contacts sonar and netbox to complete it.)
class ProvisionerModel(BaseModel):
    class Config:
        extra = 'allow'

class Provisioner(BaseRpcServer, BaseRpcClient):
    name = "provisioner"
    binding_keys = ["dhcp.lease.reg"]
    model = ProvisionerModel

    async def consume(self, message: IncomingMessage) -> None:
        log.debug("Entered Provisioner")
        if message.routing_key == "dhcp.lease.reg":
            log.info(f"Provision Processing Message: {message.body}")
            body = json.loads(message.body)
            await self.publish_provisioner_slackupdate(body, "Received provisioning request.")

            # Provision Authorized Check. Throws an exception (to a .nack) if the provision request is rejected
            await self.publish_provisioner_slackupdate(body, f"Checking for Scheduled Job on account {body["account_id"]}.")
            can_prov = json.loads(bytes.decode(await self.rpc_call("rpc.erp.can_provision", message.body)))
            await self.error_check("Can Provision", can_prov, body)

            log.info(f"Customer can Provision")
            await self.publish_provisioner_slackupdate(body, f"Account {body["account_id"]} is valid and has a scheduled job today")

            # Get the access point name, as well as additional site info
            try:
                reg_vlan_res = json.loads(await self.rpc_call("rpc.dcim.get_reg_vlan", message.body))
                reg_vlan = reg_vlan_res['res']
                reg_vlan_name = reg_vlan.get("prefix_list")[0].get("vlan").get("name")
            except Exception as e:
                raise Exception(e)
            await self.error_check("Get Service Mod", reg_vlan_res, body)

            log.info(f"Access Point Data Acquired: {reg_vlan}")
            await self.publish_provisioner_slackupdate(body, "AP data acquired.")

            # Get the mgmt vlan ID using the reg vlan name
            try:
                res = json.loads(await self.rpc_call("rpc.dcim.get_mgmt_id_by_reg", { 'name': reg_vlan_name }))
            except Exception as e:
                raise Exception(e)

            await self.error_check("Get mgmt VLAN ID by reg name", res, body)
            mgmt_id = res['res']

            log.info(f"Got mgmt VLAN ID: {res}")
            await self.publish_provisioner_slackupdate(body, "Got mgmt VLAN ID")

            # Get the MAC Address list from the SM
            await self.publish_provisioner_slackupdate(body, f"Acquiring MAC Addresses from {body['ip']}.")
            mac_res = await self.rpc_call("rpc.network.get_wave_macs", body['ip'])
            mac_addresses_result = json.loads(mac_res)
            mac_addresses_data = mac_addresses_result.get("res")
            await self.error_check("Get MACs", mac_addresses_result, body)

            log.info(f"MAC Addresses Acquired: {mac_addresses_data}")
            await self.publish_provisioner_slackupdate(body, f"Acquired MAC Addresses from {body['ip']}.")

            # Validate that one of the MAC addresses is an inventory item (Throw to .nack otherwise)
            # Assign if so
            mac_message_dict = self.get_sonar_assigninventory_load(message, mac_addresses_data)
            await self.publish_provisioner_slackupdate(body, "Validating and Assigning MAC Addresses.")
            sonar_inventory_result = json.loads(await self.rpc_call("rpc.erp.assign_inventory", mac_message_dict))
            sonar_inventory_data = sonar_inventory_result["res"]
            await self.error_check("Assign Inventory", sonar_inventory_result, body)

            log.info(f"MAC Address Assigned {sonar_inventory_data}")
            await self.publish_provisioner_slackupdate(body, f"Assigned SM inventory item {sonar_inventory_data} to account.")

            # Get Tenant
            tenant_message_dict = self.get_tenant_config_load(message)
            await self.publish_provisioner_slackupdate(body, "Verifying/Creating Tenant.")
            verify_tenant_result = json.loads(await self.rpc_call("rpc.dcim.tenant_verification", tenant_message_dict))
            verify_tenant_id = verify_tenant_result["res"]
            await self.error_check("Tenant Verification", verify_tenant_result, body)

            log.info(f"Verify Tenant Successful: {verify_tenant_id}")
            await self.publish_provisioner_slackupdate(body, f"Netbox tenant verified: {verify_tenant_id.get("tenant_list")[0].get("id")}")

            # Get VLAN
            vlan_message_dict = self.get_vlan_config_load(message, verify_tenant_id, reg_vlan)
            await self.publish_provisioner_slackupdate(body, "Assigning tenant VLAN")
            get_vlan_result = json.loads(await self.rpc_call("rpc.dcim.assign_tenant_vlan", vlan_message_dict))
            get_vlan_data = get_vlan_result["res"]
            await self.error_check("VLAN Verification", get_vlan_result, body)

            log.info(f"Verify VLAN Successful: {get_vlan_data}")
            await self.publish_provisioner_slackupdate(body, f"Assigned VLAN {get_vlan_data["id"]} to account {body["account_id"]}.")

            #Get Router
            router_ip_dict = self.get_router_ip_load(message, get_vlan_data["id"], reg_vlan)
            await self.publish_provisioner_slackupdate(body, "Acquiring router IP address")
            get_router_ip_result = json.loads(bytes.decode(await self.rpc_call("rpc.dcim.get_router_ip", router_ip_dict)))
            get_router_ip_data = get_router_ip_result["res"]
            await self.error_check("Get Router IP", get_router_ip_result, body)

            log.info(f"Get Router IP Successful: {get_router_ip_data}")
            await self.publish_provisioner_slackupdate(body, f"Router IP Acquired: {get_router_ip_data["router_ip"]}")

            # Configure Router and Switches
            config_router_dict = self.get_config_router_load(message, get_router_ip_data["router_ip"], get_router_ip_data["access_point_ip"], get_vlan_data["vid"])
            log.info(f"Entering Router Config: {config_router_dict}")
            await self.publish_provisioner_slackupdate(body, f"Adding cVLAN ({get_vlan_data["vid"]}) to Interface and Configuring Router/Switches")
            config_router_result = json.loads(await self.rpc_call("rpc.network.router.add_cvlan_to_interface_by_arp", config_router_dict))
            await self.error_check("Config Interfaces", config_router_result, body)

            log.info(f"Config Router Successful: {config_router_result['res']}")
            await self.publish_provisioner_slackupdate(body, "cVLAN Assigned. Router and Switches Configured.")

            wave_config = {
                "ip_address": body['ip'],
                "cust_id": body['account_id'],
                "cust_vlan": get_vlan_data['vid'],
                "mgmt_vlan": mgmt_id,
                # Regex cuts off the suffix of the ap name
                "ap_name": re.sub("-reg$|-mgmt$", "", reg_vlan['prefix_list'][0]['vlan']['name']) 
            }
            log.info(f"Entering Service Module Config: {wave_config}")
            await self.publish_provisioner_slackupdate(body, f"Sending Config File to Service Module")
            config_sm_result = json.loads(await self.rpc_call("rpc.network.send_wave_sm_config", wave_config))
            await self.error_check("Account ID", config_sm_result, body)

            log.info(f"Config Service Module Successful: {config_sm_result['res']}")
            await self.publish_provisioner_slackupdate(body, "Service Module Successfully Configured.")
            await self.publish_provisioner_slackupdate(body, f"Provisioner process completed successfully for account {body["account_id"]}")

    def get_manufacturer_name(self, ap_info) -> str:
        if re.match("^ap-biq", ap_info.get("prefix_list")[0].get("vlan").get("name")):
            return "Ubiquiti Wave AP"
        elif (re.match("^ap-ltu", ap_info.get("prefix_list")[0].get("vlan").get("name")) or
                re.match("^ap-edg", ap_info.get("prefix_list")[0].get("vlan").get("name")) or
                re.match("^ap-ac", ap_info.get("prefix_list")[0].get("vlan").get("name"))
        ):
            return "Ubiquiti"
        elif re.match("^ap-cn3", ap_info.get("prefix_list")[0].get("vlan").get("name")):
            return "Cambium"
        elif re.match("^tik60", ap_info.get("prefix_list")[0].get("vlan").get("name")):
            return "Mikrotik"

        raise Exception(f"Could Not Determine Manufacturer for {ap_info.get("prefix_list")[0].get("vlan").get("name")}")

    #loads the message parameters for an Ubiquity v8 Wave MAC Address' Request Send
    def get_sonar_assigninventory_load(self, original_message, mac_addresses) -> dict:
        mac_message_dict = json.loads(original_message.body)
        mac_message_dict["routing_key"] = "rpc.erp.assign_inventory"
        mac_message_dict["mac_addresses"] = mac_addresses

        log.info(f"Sonar Message (Assign Valid MAC From List) Configured: {mac_message_dict}")

        return mac_message_dict

    #loads the message parameters for an Ubiquity v8 Wave Config Send
    def get_ubiquity_wave_config_load(self, message, ap_info) -> dict:
        sm_message_dict = json.loads(message.body)
        sm_message_dict["routing_key"] = "rpc.erp.config"

        sm_message_dict["customer_vlan"] = ap_info.get("prefix_list")[0].get("vlan").get("id")
        sm_message_dict["mgmt_vlan"] = re.sub("^.+?(?=/).", "", ap_info.get("prefix_list")[0].get("prefix")) #gets just the VLAN
        sm_message_dict["ap_name"] = re.sub("-reg$|-mgmt$", "", ap_info.get("prefix_list")[0].get("vlan").get("name")) #regex cuts off the suffix of the ap name
        sm_message_dict["snmp_location"] = re.sub("^.+?(?=\.).", "", sm_message_dict["ap_name"]) #Cutoff the tag before the .
        sm_message_dict["ip_address"] = json.loads(message.body).get("ip")
        sm_message_dict["ssh_user"] = self.config.ssh_user
        sm_message_dict["ssh_pass"] = self.config.ssh_pass
        sm_message_dict["ssh_wave_pass"] = self.config.ssh_wave_pass

        # Example of what to pass in, for future reference - as of 5/23/2024
        #sm_message_dict["customer_vlan"] = "1061"
        #sm_message_dict["mgmt_vlan"] = "15"
        #sm_message_dict["snmp_location"] = "ubbt2"
        #sm_message_dict["ap_name"] = "ap-biq60.ubbt2"
        #sm_message_dict["ip_address"] = "172.20.123.198"

        log.info(f"Network_Device Message (Ubiquity Wave Config) Configured: {sm_message_dict}")

        return sm_message_dict

    def get_tenant_config_load(self, message) -> dict:
        nb_message_dict = json.loads(message.body)
        nb_message_dict["routing_key"] = "rpc.dcim.tenant_verification"

        nb_message_dict["account_id"] = json.loads(message.body).get("account_id")
        nb_message_dict["nb_url"] = re.sub("graphql\/", "", self.config.netbox_api_url) # cuts off the graphql extension
        nb_message_dict["nb_token"] = self.config.netbox_api_key

        log.info(f"Verify Tenant Configured: {nb_message_dict}")

        return nb_message_dict

    def get_vlan_config_load(self, message, verify_tenant_data, reg_vlan) -> dict:
        nb_message_dict = json.loads(message.body)
        nb_message_dict["routing_key"] = "rpc.dcim.assign_tenant_vlan"
        nb_message_dict["tenant_id"] = verify_tenant_data.get("tenant_list")[0].get("id")
        nb_message_dict["site_id"] = reg_vlan.get("prefix_list")[0].get("site").get("id")

        log.info(f"Verify VLAN Configured: {nb_message_dict}")

        return nb_message_dict

    def get_router_ip_load(self, message, vlan_id, ap_info) -> dict:
        nb_message_dict = json.loads(message.body)
        nb_message_dict["routing_key"] = "rpc.dcim.get_router_ip"
        nb_message_dict["vlan_id"] = vlan_id
        nb_message_dict["ap_name"] = re.sub("-reg$|-mgmt$", "", ap_info.get("prefix_list")[0].get("vlan").get("name")) #regex cuts off the suffix of the ap name

        log.info(f"Get Router IP Configured: {nb_message_dict}")

        return nb_message_dict

    def get_config_router_load(self, message, router_ip, ap_ip, vlan_vid) -> dict:
        nb_message_dict = json.loads(message.body)
        nb_message_dict["routing_key"] = "rpc.erp.config_interface"
        nb_message_dict["router_ip"] = router_ip
        nb_message_dict["ip"] = ap_ip
        nb_message_dict["customer_vlan"] =  int(vlan_vid)

        log.info(f"Router Config Configured: {nb_message_dict}")

        return nb_message_dict

    def get_routing_key(self, body: dict) -> str:
        return body.get("routing_key")

    # Checks if RPC call replies w/ an error. If so, it posts error to Slack and raises exception to stop execution.
    async def error_check(self, call_name, result_dictionary, body):
        if result_dictionary["error"] is not None:
            await self.publish_provisioner_slackupdate(body=body, printout=("PROVISIONER FAILED: " + result_dictionary["error"]))
            raise Exception(f"Error Returned from {call_name} - {result_dictionary["error"]}")

    async def publish_provisioner_slackupdate(self, body, printout):
        msg = {
            "channel": self.config.slack_prov_chan,
            "text": f"*Acc: {body['account_id']}* - {printout}"
        }
        await self.publish("chat.message.post", msg)
