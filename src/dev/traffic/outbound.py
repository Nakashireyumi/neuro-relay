import json
from ..nakurity.client import NakurityClient
from neuro_api.server import RegisterActionsData

class NakurityOutbound:
    """
    Parses Intermediary outbound traffic    
    """
    async def register_actions(client: NakurityClient, payload: dict[RegisterActionsData]):
        try:
            print(
                """
                [Nakurity Outbound Traffic Handler Module]
                """
            )
            actions = payload.get("actions", [])
            
            client.register_actions()
        except Exception as e:
            print("[Nakurity Client] failed to forward payload to the neuro backend!")
            print(e)
            return None