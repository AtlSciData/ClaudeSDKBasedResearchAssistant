"""
Unit tests for backend/app/auth.py's JWT verification, using a locally
generated RSA key pair and a mocked JWKS response.

No network call, no real Cognito user pool: auth._get_jwks() is monkeypatched
to return a JWKS built from a key pair this test generates and signs tokens
with itself, so every case below (valid, expired, wrong audience, unknown
kid, forged signature) is fully under the test's control.
"""
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jose import jwk, jwt

from backend.app import auth

KID = "test-key-1"


def _generate_rsa_pem_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest.fixture(scope="module")
def keypair():
    private_pem, public_pem = _generate_rsa_pem_pair()

    jwk_dict = jwk.construct(public_pem, algorithm="RS256").to_dict()
    jwk_dict["kty"] = "RSA"
    jwk_dict["alg"] = "RS256"
    jwk_dict["use"] = "sig"
    jwk_dict["kid"] = KID

    return {"private_pem": private_pem, "jwks_keys": [jwk_dict]}


@pytest.fixture(autouse=True)
def mock_jwks(monkeypatch, keypair):
    """Every test in this file hits this fake JWKS instead of a real
    Cognito endpoint -- verify_token() never makes a network call."""
    monkeypatch.setattr(auth, "_get_jwks", lambda: keypair["jwks_keys"])


def make_token(keypair, overrides=None, kid=KID, signing_key=None):
    claims = {
        "sub": "test-user-id",
        "aud": auth.COGNITO_CLIENT_ID,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    if overrides:
        claims.update(overrides)
    key = signing_key or keypair["private_pem"]
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


class TestVerifyToken:
    def test_accepts_a_valid_token(self, keypair):
        token = make_token(keypair)
        claims = auth.verify_token(authorization=f"Bearer {token}")
        assert claims["sub"] == "test-user-id"

    def test_rejects_a_missing_bearer_prefix(self):
        with pytest.raises(HTTPException) as exc_info:
            auth.verify_token(authorization="not-a-bearer-token")
        assert exc_info.value.status_code == 401
        assert "bearer" in exc_info.value.detail.lower()

    def test_rejects_an_expired_token(self, keypair):
        token = make_token(keypair, overrides={"exp": int(time.time()) - 60})
        with pytest.raises(HTTPException) as exc_info:
            auth.verify_token(authorization=f"Bearer {token}")
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_rejects_a_token_issued_for_a_different_client(self, keypair):
        token = make_token(keypair, overrides={"aud": "some-other-client-id"})
        with pytest.raises(HTTPException) as exc_info:
            auth.verify_token(authorization=f"Bearer {token}")
        assert exc_info.value.status_code == 401
        assert "not issued for this app" in exc_info.value.detail.lower()

    def test_rejects_an_unknown_signing_key(self, keypair):
        token = make_token(keypair, kid="a-kid-not-in-the-jwks")
        with pytest.raises(HTTPException) as exc_info:
            auth.verify_token(authorization=f"Bearer {token}")
        assert exc_info.value.status_code == 401
        assert "unknown signing key" in exc_info.value.detail.lower()

    def test_rejects_a_token_forged_with_a_different_key(self, keypair):
        """A token that claims the real kid but was actually signed by a
        different private key -- the signature check must catch this even
        though the kid lookup alone would succeed."""
        rogue_private_pem, _ = _generate_rsa_pem_pair()
        token = make_token(keypair, signing_key=rogue_private_pem)
        with pytest.raises(HTTPException) as exc_info:
            auth.verify_token(authorization=f"Bearer {token}")
        assert exc_info.value.status_code == 401
        assert "invalid token signature" in exc_info.value.detail.lower()

    def test_rejects_a_malformed_token(self):
        with pytest.raises(HTTPException) as exc_info:
            auth.verify_token(authorization="Bearer not.a.validtoken")
        assert exc_info.value.status_code == 401
