import json
import pynetbox
from aio_pika import IncomingMessage
from .index import Netbox


# Send a POST to Netbox, creating the tenant.
async def create_tenant(self, message: IncomingMessage):

    # Get and verify message contains account_ID
    if not message.body:
        print("Errors with Create Tenant Message: {message}")
        return None

    #print("Getting Account ID")
    acc_id = json.loads(message.body).get("account_id")

    if not acc_id:
        print("Create Tenant POST missing Account ID")
        return None

    #print("Getting URL")
    nb_url = json.loads(message.body).get("nb_url")

    if not nb_url:
        print("Create Tenant POST missing Netbox URL - Config File Issue?")
        return None

    #print("Getting Netbox Token")
    nb_token = json.loads(message.body).get("nb_token")

    if not nb_token:
        print("Create Tenant POST missing Netbox Token - Config File Issue?")
        return None

    # CURL POST
    netbox_api = pynetbox.api(nb_url, token=nb_token)
    res = "None"
    try:
        res = netbox_api.tenancy.tenants.create(
            name=f"ubb-{acc_id}",
            slug=f"{acc_id}",
        )
    except pynetbox.RequestError as e:
        raise Exception(f"Create Tenant Returned an error, {e.error}")

    print(f"Tenant Created Successfully: {res}")
    return f"ubb-{acc_id}"

Netbox.create_tenant = create_tenant
