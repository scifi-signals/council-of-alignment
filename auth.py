"""GitHub OAuth authentication + user management."""

import uuid
import hmac
import hashlib
import secrets

import httpx
from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException

from config import GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, BASE_URL, ENCRYPTION_KEY, ENCRYPTION_KEY_OLD, FREE_CONVENE_LIMIT, ADMIN_GITHUB_IDS
from database import get_db

# ─── BYOK key encryption helpers ────────────────────────────

# MultiFernet: encrypts with the first (current) key, decrypts with any.
# To rotate: generate new key, move current ENCRYPTION_KEY to ENCRYPTION_KEY_OLD,
# set new key as ENCRYPTION_KEY. Old data still decrypts; new data uses new key.
_fernet_keys = [Fernet(ENCRYPTION_KEY.encode())]
if ENCRYPTION_KEY_OLD:
    _fernet_keys.append(Fernet(ENCRYPTION_KEY_OLD.encode()))
_fernet = MultiFernet(_fernet_keys)


async def log_key_access(user_id: str, action: str, session_id: str = None) -> None:
    """Record an audit log entry for API key access."""
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO key_access_log (user_id, session_id, action) VALUES (?, ?, ?)",
            (user_id, session_id, action),
        )
        await db.commit()
    finally:
        await db.close()


def encrypt_key(plaintext: str) -> str:
    """Encrypt an API key for storage."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_key(ciphertext: str) -> str:
    """Decrypt a stored API key."""
    return _fernet.decrypt(ciphertext.encode()).decode()


async def get_user_api_key(user_id: str) -> str | None:
    """Fetch and decrypt the user's OpenRouter API key. Returns None if not set."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT openrouter_key_encrypted FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if not row or not row["openrouter_key_encrypted"]:
            return None
        try:
            return decrypt_key(row["openrouter_key_encrypted"])
        except InvalidToken:
            return None
    finally:
        await db.close()


async def set_user_api_key(user_id: str, api_key: str) -> None:
    """Encrypt and store the user's OpenRouter API key."""
    encrypted = encrypt_key(api_key)
    db = await get_db()
    try:
        await db.execute(
            "UPDATE users SET openrouter_key_encrypted = ? WHERE id = ?",
            (encrypted, user_id),
        )
        await db.commit()
    finally:
        await db.close()


async def delete_user_api_key(user_id: str) -> None:
    """Remove the user's stored API key."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE users SET openrouter_key_encrypted = NULL WHERE id = ?",
            (user_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def increment_free_convenes(user_id: str) -> None:
    """Increment the user's free convene counter."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE users SET free_convenes_used = free_convenes_used + 1 WHERE id = ?",
            (user_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def get_free_convenes_remaining(user_id: str) -> int:
    """Return how many free convenes the user has left."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT free_convenes_used FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return 0
        used = row["free_convenes_used"] or 0
        return max(0, FREE_CONVENE_LIMIT - used)
    finally:
        await db.close()


def _hash_api_key(key: str) -> str:
    """SHA-256 hash of an API key for storage."""
    return hashlib.sha256(key.encode()).hexdigest()


async def generate_user_api_key(user_id: str) -> str:
    """Generate a new personal API key for the user. Returns the plaintext key (shown once)."""
    key = f"coa-{secrets.token_urlsafe(32)}"
    key_hash = _hash_api_key(key)
    db = await get_db()
    try:
        await db.execute(
            "UPDATE users SET api_key_hash = ? WHERE id = ?",
            (key_hash, user_id),
        )
        await db.commit()
    finally:
        await db.close()
    return key


async def revoke_user_api_key(user_id: str) -> None:
    """Revoke the user's personal API key."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE users SET api_key_hash = NULL WHERE id = ?",
            (user_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def get_user_by_api_key(key: str) -> dict | None:
    """Look up a user by their personal API key. Returns user dict or None."""
    key_hash = _hash_api_key(key)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM users WHERE api_key_hash = ?", (key_hash,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "github_id": row["github_id"],
            "github_login": row["github_login"],
            "display_name": row["display_name"],
            "avatar_url": row["avatar_url"],
        }
    finally:
        await db.close()


def is_admin(user: dict | None) -> bool:
    """Check if a user has admin access."""
    if not user:
        return False
    return user.get("github_id") in ADMIN_GITHUB_IDS


def github_login_url(state: str) -> str:
    """Build the GitHub OAuth authorize URL."""
    return (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={BASE_URL}/auth/callback"
        f"&scope=read:user"
        f"&state={state}"
    )


async def exchange_code_for_token(code: str) -> str:
    """Exchange OAuth code for access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        data = resp.json()
        if "access_token" not in data:
            raise ValueError(data.get("error_description", "OAuth token exchange failed"))
        return data["access_token"]


async def fetch_github_user(access_token: str) -> dict:
    """Fetch the authenticated GitHub user's profile."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def get_or_create_user(github_user: dict) -> dict:
    """Upsert a user from GitHub profile data. Updates profile on each login."""
    github_id = github_user["id"]
    login = github_user["login"]
    display_name = github_user.get("name") or login
    avatar_url = github_user.get("avatar_url", "")

    db = await get_db()
    try:
        # Check if user exists
        cursor = await db.execute("SELECT * FROM users WHERE github_id = ?", (github_id,))
        row = await cursor.fetchone()

        if row:
            # Update profile on each login
            await db.execute(
                "UPDATE users SET github_login = ?, display_name = ?, avatar_url = ? WHERE github_id = ?",
                (login, display_name, avatar_url, github_id),
            )
            await db.commit()
            return {
                "id": row["id"],
                "github_id": github_id,
                "github_login": login,
                "display_name": display_name,
                "avatar_url": avatar_url,
            }
        else:
            user_id = str(uuid.uuid4())[:8]
            await db.execute(
                "INSERT INTO users (id, github_id, github_login, display_name, avatar_url) VALUES (?, ?, ?, ?, ?)",
                (user_id, github_id, login, display_name, avatar_url),
            )
            await db.commit()
            return {
                "id": user_id,
                "github_id": github_id,
                "github_login": login,
                "display_name": display_name,
                "avatar_url": avatar_url,
            }
    finally:
        await db.close()


async def get_user_by_id(user_id: str) -> dict | None:
    """Fetch a user by their internal ID."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        used = row["free_convenes_used"] or 0 if "free_convenes_used" in row.keys() else 0
        return {
            "id": row["id"],
            "github_id": row["github_id"],
            "github_login": row["github_login"],
            "display_name": row["display_name"],
            "avatar_url": row["avatar_url"],
            "free_convenes_used": used,
            "free_convenes_remaining": max(0, FREE_CONVENE_LIMIT - used),
            "has_api_key": bool(row["openrouter_key_encrypted"] if "openrouter_key_encrypted" in row.keys() else None),
            "has_personal_key": bool(row["api_key_hash"] if "api_key_hash" in row.keys() else None),
        }
    finally:
        await db.close()


async def get_current_user(request: Request) -> dict | None:
    """Read user_id from session cookie, return user or None."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return await get_user_by_id(user_id)


async def require_auth(request: Request) -> str:
    """Return user_id or redirect to login. For page routes."""
    user = await get_current_user(request)
    if not user:
        # Store where they were trying to go
        request.session["oauth_next"] = str(request.url)
        raise HTTPException(status_code=307, detail="Login required",
                            headers={"Location": "/auth/login"})
    return user["id"]


async def require_auth_api(request: Request) -> str:
    """Return user_id or raise 401. For API/HTMX routes."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user["id"]


def generate_state() -> str:
    """Generate a random state token for OAuth CSRF protection."""
    return secrets.token_urlsafe(32)
