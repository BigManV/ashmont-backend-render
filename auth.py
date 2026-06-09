from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Query, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, APIKeyHeader
from jose import JWTError, jwt

from config import get_settings


bearer_scheme = HTTPBearer(auto_error=False)
intake_key_header = APIKeyHeader(name="x-intake-api-key", auto_error=False)


def require_user(credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme)) -> dict:
    settings = get_settings()
    if settings.dev_auth_bypass:
        return {
            "id": "dev-bypass",
            "email": "dev-bypass@advogue.ai",
            "role": "authenticated",
            "display_name": "Dev bypass",
            "access_level": "full",
        }
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization token.")
    if not settings.supabase_jwt_secret:
        raise HTTPException(status_code=503, detail="SUPABASE_JWT_SECRET is not configured.")
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired authorization token.") from exc
    try:
        import db
        profile = db.fetch_one("select * from app_users where id = %s", (claims.get("sub"),))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not verify dashboard access.") from exc
    if not profile:
        raise HTTPException(status_code=403, detail="This Supabase user is not allowed to access the Ashmont dashboard.")
    return {
        "id": claims.get("sub"),
        "email": claims.get("email"),
        "role": claims.get("role"),
        "display_name": profile.get("display_name"),
        "access_level": profile.get("access_level"),
    }


def require_intake_key(api_key: str | None = Depends(intake_key_header)) -> None:
    settings = get_settings()
    if not settings.intake_api_key:
        raise HTTPException(status_code=503, detail="INTAKE_API_KEY is not configured.")
    if api_key != settings.intake_api_key:
        raise HTTPException(status_code=401, detail="Invalid intake API key.")


def require_tool_key(
    header_key: str | None = Header(default=None, alias="x-tool-api-key"),
    query_key: str | None = Query(default=None, alias="api_key"),
) -> None:
    settings = get_settings()
    if not settings.tool_api_key:
        raise HTTPException(status_code=503, detail="TOOL_API_KEY is not configured.")
    if (header_key or query_key) != settings.tool_api_key:
        raise HTTPException(status_code=401, detail="Invalid tool API key.")
