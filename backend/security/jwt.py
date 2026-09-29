import os
from datetime import datetime, timedelta, timezone
import jwt
from jwt import InvalidTokenError
from .roles import Role

ISSUER = "auratwin-ai"
AUDIENCE = "auratwin-api"


def token_settings() -> tuple[str, str, int]:
    secret = os.getenv("AURATWIN_JWT_SECRET", "")
    algorithm = os.getenv("AURATWIN_JWT_ALGORITHM", "HS256")
    try:
        expires = int(os.getenv("AURATWIN_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    except ValueError as exc:
        raise RuntimeError("AURATWIN_ACCESS_TOKEN_EXPIRE_MINUTES must be an integer") from exc
    if algorithm != "HS256":
        raise RuntimeError("Only HS256 is supported by this single-service implementation")
    if len(secret) < 32 or secret.lower() in {"secret", "changeme", "jwt-secret"}:
        raise RuntimeError("Configure a strong AURATWIN_JWT_SECRET (at least 32 characters)")
    if not 1 <= expires <= 1440:
        raise RuntimeError("AURATWIN_ACCESS_TOKEN_EXPIRE_MINUTES must be between 1 and 1440")
    return secret, algorithm, expires


def create_access_token(user_id: str, role: Role) -> tuple[str, int]:
    secret, algorithm, minutes = token_settings()
    now = datetime.now(timezone.utc)
    claims = {"sub": user_id, "role": role.value, "iat": now,
              "exp": now + timedelta(minutes=minutes), "iss": ISSUER,
              "aud": AUDIENCE, "type": "access"}
    return jwt.encode(claims, secret, algorithm=algorithm), minutes * 60


def decode_access_token(token: str) -> dict:
    secret, algorithm, _ = token_settings()
    claims = jwt.decode(token, secret, algorithms=[algorithm], issuer=ISSUER,
                        audience=AUDIENCE, options={"require": ["sub", "role", "iat", "exp", "iss", "aud"]})
    if claims.get("type") != "access" or not isinstance(claims.get("sub"), str):
        raise InvalidTokenError("Invalid access token")
    claims["role"] = Role(claims["role"]).value
    return claims
