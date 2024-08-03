import json
from aio_pika import IncomingMessage
from gql import gql
from .index import Netbox


# Checks if Netbox contains the tenant using the account id
# Returns the tenant ID if true, returns NULL if false
async def does_tenant_exist(self, message: IncomingMessage) -> dict:
    # Get and verify message contains account_ID
    if not bytes.decode(message.body):
        print("Errors with Does Tennant Exist Message: {message}")
        return None
    #print("Getting Account ID")
    acc_id = json.loads(bytes.decode(message.body)).get('account_id')
    if not acc_id:
        print("Tenant Exists Query missing Account ID")
        return None
    query = gql('''
            query verifyTenant($exact: String!, $starts_with: String!)
            {
                tenant_list(filters: {
                    name: {exact: $exact},
                    OR: {
                        name: {starts_with: $starts_with}
                    }
                })
                {
                    id
                    name
                }
            }
        ''')
    print(f"{query}")
    print(f"{acc_id}")
    try:
        exact = f"ubb-{acc_id}"
        starts_with = f"ubb-{acc_id}-"
        res = await self.execute(
            query,
            variable_values={
                'exact': exact,
                'starts_with': starts_with
            }
        )
    except Exception as e:
        print(f"EXCEPTION {e}")
    print(f"Does Tenant Exist Callback Received: {res}")

    if not len(res.get("tenant_list")):
        print("Does Tenant Exist request returned empty response")
        return None

    return res

Netbox.does_tenant_exist = does_tenant_exist
