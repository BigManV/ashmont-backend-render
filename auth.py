from __future__ import annotations

import hmac
import json
from uuid import NAMESPACE_URL, uuid5

from fastapi import Depends, Header, HTTPException, Query, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, APIKeyHeader
from jose import JWTError, jwt

from config import get_settings
from utils import utc_now


bearer_scheme = HTTPBearer(auto_error=False)
intake_key_header = APIKeyHeader(name="x-intake-api-key", auto_error=False)


def _dashboard_token_secret() -> str:
    settings = get_settings()
    return settings.dashboard_token_secret or settings.supabase_jwt_secret


def _configured_dashboard_users() -> list[dict]:
    settings = get_settings()
    raw = (settings.dashboard_users_json or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=503, detail="DASHBOARD_USERS_JSON is invalid JSON.") from exc
    if not isinstance(parsed, list):
        raise HTTPException(status_code=503, detail="DASHBOARD_USERS_JSON must be a JSON array.")
    users = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        email = str(item.get("email") or "").strip().lower()
        password = str(item.get("password") or "")
        if not email or not password:
            continue
        users.append(
            {
                "id": str(item.get("id") or uuid5(NAMESPACE_URL, f"ashmont-dashboard:{email}")),
                "email": email,
                "password": password,
                "display_name": item.get("display_name") or email.split("@")[0],
                "access_level": item.get("access_level") or "full",
            }
        )
    return users


def dashboard_auth_configured() -> bool:
    return bool(_configured_dashboard_users() and _dashboard_token_secret())


def authenticate_dashboard_user(email: str, password: str) -> dict | None:
    normalized = str(email or "").strip().lower()
    attempted_password = str(password or "")
    for user in _configured_dashboard_users():
        if user["email"] == normalized and hmac.compare_digest(user["password"], attempted_password):
            return {key: value for key, value in user.items() if key != "password"}
    return None


def create_dashboard_token(user: dict) -> str:
    settings = get_settings()
    secret = _dashboard_token_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="Dashboard token secret is not configured.")
    now = utc_now()
    exp = now.timestamp() + (settings.dashboard_token_ttl_minutes * 60)
    return jwt.encode(
        {
            "sub": user["id"],
            "email": user["email"],
            "role": "authenticated",
            "iat": int(now.timestamp()),
            "exp": int(exp),
        },
        secret,
        algorithm="HS256",
    )


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
        if not profile:
            profile = db.fetch_one(
                """
                select *
                from dashboard_users
                where id = %s
                   or lower(email) = lower(%s)
                limit 1
                """,
                (claims.get("sub"), claims.get("email") or ""),
            )
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
