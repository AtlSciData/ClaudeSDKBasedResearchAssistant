"""
Cognito authentication: login (USER_PASSWORD_AUTH) and JWT verification.
"""
import os
import time
import boto3
import requests
from jose import jwk, jwt
from jose.utils import base64url_decode
from fastapi import HTTPException, Header
import os
import time
import boto3
import requests
from dotenv import load_dotenv          # <-- add this
from jose import jwk, jwt
from jose.utils import base64url_decode
from fastapi import HTTPException, Header

load_dotenv()                            # <-- and this

AWS_REGION = os.environ["AWS_REGION"]
COGNITO_POOL_ID = os.environ["COGNITO_POOL_ID"]
COGNITO_CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]

AWS_REGION = os.environ["AWS_REGION"]
COGNITO_POOL_ID = os.environ["COGNITO_POOL_ID"]
COGNITO_CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]

_cognito = boto3.client("cognito-idp", region_name=AWS_REGION)

_JWKS_URL = f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_POOL_ID}/.well-known/jwks.json"
_jwks_cache = {"keys": None, "fetched_at": 0}


def _get_jwks():
    if _jwks_cache["keys"] is None or time.time() - _jwks_cache["fetched_at"] > 3600:
        resp = requests.get(_JWKS_URL, timeout=5)
        resp.raise_for_status()
        _jwks_cache["keys"] = resp.json()["keys"]
        _jwks_cache["fetched_at"] = time.time()
    return _jwks_cache["keys"]


def login(username: str, password: str) -> str:
    """Authenticate against Cognito and return the ID token."""
    try:
        resp = _cognito.initiate_auth(
            ClientId=COGNITO_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
    except _cognito.exceptions.NotAuthorizedException:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return resp["AuthenticationResult"]["IdToken"]


def verify_token(authorization: str = Header(...)) -> dict:
    """FastAPI dependency: verifies the Bearer ID token, returns its claims."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ")

    try:
        headers = jwt.get_unverified_header(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed token")

    key_data = next((k for k in _get_jwks() if k["kid"] == headers["kid"]), None)
    if key_data is None:
        raise HTTPException(status_code=401, detail="Unknown signing key")

    public_key = jwk.construct(key_data)
    message, encoded_sig = token.rsplit(".", 1)
    sig = base64url_decode(encoded_sig.encode())
    if not public_key.verify(message.encode(), sig):
        raise HTTPException(status_code=401, detail="Invalid token signature")

    claims = jwt.get_unverified_claims(token)
    if time.time() > claims["exp"]:
        raise HTTPException(status_code=401, detail="Token expired")
    if claims.get("aud") != COGNITO_CLIENT_ID and claims.get("client_id") != COGNITO_CLIENT_ID:
        raise HTTPException(status_code=401, detail="Token not issued for this app")

    return claims