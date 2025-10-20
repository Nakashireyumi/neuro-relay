from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from ..server import nakurity_id, nakurity_link

def register_routes(server):
    app = server.app
    auth_tokens = server.auth_tokens

    @app.post("/auth")
    async def get_auth_key(request: Request):
        """
        Neuro Integration → requests an auth key.
        """
        data = await request.json()
        module_name = data.get("module_name")
        if not module_name:
            raise HTTPException(status_code=400, detail="Missing module_name")

        token = nakurity_id.generate_token(module_name)
        return JSONResponse({"auth_token": token})
    
    @app.post("/identify")
    async def identify_integration(request: Request):
        """
        Neuro Integration → sends identity + token.
        Validates and forwards to Nakurity Backend.
        """
        data = await request.json()
        module_name = data.get("module_name")
        token = data.get("auth_token")
        identity = data.get("identity")

        if not module_name or not token or not identity:
            raise HTTPException(status_code=400, detail="Missing required fields")

        await nakurity_id.register_identity(module_name, token, identity, source="integration")
        await nakurity_id.forward_identity_to_backend(nakurity_link, identity)
        return JSONResponse({"status": "ok", "message": "Integration identified"})

    @app.post("/nakurity/identify")
    async def identify_nakurity(request: Request):
        """
        Nakurity Backend → identifies itself to ID Server.
        """
        data = await request.json()
        await nakurity_id.identify_backend(data)
        return JSONResponse({"status": "ok", "message": "Nakurity registered"})
