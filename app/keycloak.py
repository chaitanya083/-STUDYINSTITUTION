from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

import requests
from jose import jwt
from jose.exceptions import JWTError

from app.config import get_settings

settings = get_settings()


class KeycloakError(Exception):
    """Raised for any Keycloak communication or validation failure."""


class KeycloakTokenValidator:
    """Validates access tokens against the realm's JWKS, with simple
    in-memory caching of the key set so we don't hit Keycloak on every
    single API request."""

    def __init__(self):
        self._jwks: Optional[Dict[str, Any]] = None
        self._jwks_fetched_at: float = 0.0
        self._jwks_ttl_seconds: int = 300

    def _get_jwks(self) -> Dict[str, Any]:
        now = time.time()
        if self._jwks is None or (now - self._jwks_fetched_at) > self._jwks_ttl_seconds:
            try:
                resp = requests.get(settings.jwks_url, timeout=5)
                resp.raise_for_status()
                self._jwks = resp.json()
                self._jwks_fetched_at = now
            except requests.RequestException as exc:
                if self._jwks is not None:
                    return self._jwks
                raise KeycloakError(f"Unable to fetch JWKS from Keycloak: {exc}") from exc
        return self._jwks

    def decode(self, token: str) -> Dict[str, Any]:
       
        jwks = self._get_jwks()
        try:
            unverified_header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise KeycloakError(f"Malformed token header: {exc}") from exc

        key = next((k for k in jwks.get("keys", []) if k.get("kid") == unverified_header.get("kid")), None)
        if key is None:
            raise KeycloakError("Signing key not found for token (kid mismatch)")

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[key.get("alg", "RS256")],
                audience=None,
                options={"verify_aud": False},
                issuer=settings.issuer,
            )
        except JWTError as exc:
            raise KeycloakError(f"Invalid token: {exc}") from exc
        return claims


class KeycloakAdmin:
    """Wrapper around the Keycloak Admin REST API."""

    def __init__(self):
        self._dev_sessions: Dict[str, Dict[str, Any]] = {}
        self._dev_users: Dict[str, Dict[str, Any]] = {}
        self._dev_groups: Dict[str, Dict[str, Any]] = {}
        self._admin_token: Optional[str] = None
        self._admin_token_expiry: float = 0.0

    # ---- Auth against the admin (master) realm ----
    def _request(self, method: str, url: str, **kwargs):
        method_name = method.upper()
        try:
            if method_name == "GET":
                return requests.get(url, **kwargs)
            if method_name == "POST":
                return requests.post(url, **kwargs)
            if method_name == "PUT":
                return requests.put(url, **kwargs)
            if method_name == "PATCH":
                return requests.patch(url, **kwargs)
            if method_name == "DELETE":
                return requests.delete(url, **kwargs)
            return requests.request(method_name, url, **kwargs)
        except requests.RequestException as exc:
            raise KeycloakError(f"Keycloak request failed for {method_name} {url}: {exc}") from exc

    def _get_admin_token(self) -> str:
        now = time.time()
        if self._admin_token and now < self._admin_token_expiry - 5:
            return self._admin_token

        url = f"{settings.keycloak_url}/realms/{settings.keycloak_admin_realm}/protocol/openid-connect/token"
        data = {
            "grant_type": "password",
            "client_id": settings.keycloak_admin_client_id,
            "username": settings.keycloak_admin,
            "password": settings.keycloak_admin_password,
        }
        try:
            resp = self._request("POST", url, data=data, timeout=10)
        except KeycloakError:
            raise
        if resp.status_code != 200:
            raise KeycloakError(f"Failed to obtain Keycloak admin token: {resp.status_code} {resp.text}")
        payload = resp.json()
        self._admin_token = payload["access_token"]
        self._admin_token_expiry = now + payload.get("expires_in", 60)
        return self._admin_token

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._get_admin_token()}", "Content-Type": "application/json"}

    def _realm_url(self, suffix: str) -> str:
        return f"{settings.keycloak_url}/admin/realms/{settings.keycloak_realm}{suffix}"

    # ---- Users ----
    def list_users(self, search: Optional[str] = None) -> List[Dict[str, Any]]:
        if settings.auth_dev_mode:
            users=list(self._dev_users.values())
            return [u for u in users if not search or search.lower() in u["username"].lower()]
        params = {"search": search} if search else {}
        try:
            resp = self._request("GET", self._realm_url("/users"), headers=self._headers(), params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise KeycloakError(f"Unable to list Keycloak users: {exc}") from exc

    def get_user(self, user_id: str) -> Dict[str, Any]:
        if settings.auth_dev_mode:
            user=self._dev_users.get(user_id)
            if not user: raise KeycloakError("User not found")
            return user
        try:
            resp = self._request("GET", self._realm_url(f"/users/{user_id}"), headers=self._headers(), timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise KeycloakError(f"Unable to fetch Keycloak user {user_id}: {exc}") from exc

    def create_user(self, username: str, email: str, password: str, first_name: str = "",
                     last_name: str = "", enabled: bool = True) -> str:
        if settings.auth_dev_mode:
            if self.find_user(username): raise KeycloakError("Username is already registered")
            user_id=str(uuid.uuid4())
            self._dev_users[user_id]={"id":user_id,"username":username,"email":email,"firstName":first_name,"lastName":last_name,"enabled":enabled,"roles":[],"password":password}
            return user_id
        first_name = first_name or username.split("@", 1)[0]
        last_name = last_name or "User"
        payload = {
            "username": username,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "enabled": enabled,
            "emailVerified": True,
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        }
        try:
            resp = self._request("POST", self._realm_url("/users"), headers=self._headers(), json=payload, timeout=10)
        except KeycloakError:
            raise
        if resp.status_code not in (201, 409):
            raise KeycloakError(f"Failed to create user {username}: {resp.status_code} {resp.text}")
        location = resp.headers.get("Location", "")
        if location:
            return location.rstrip("/").split("/")[-1]
        found=self.list_users(username)
        if found: return found[0]["id"]
        raise KeycloakError("User created but Keycloak did not return its id")

    def delete_user(self, user_id: str) -> None:
        if settings.auth_dev_mode:
            self._dev_users.pop(user_id, None); self._dev_sessions.pop(user_id, None); return
        try:
            resp = self._request("DELETE", self._realm_url(f"/users/{user_id}"), headers=self._headers(), timeout=10)
        except KeycloakError:
            raise
        if resp.status_code not in (204, 404):
            raise KeycloakError(f"Failed to delete user {user_id}: {resp.status_code} {resp.text}")

    # ---- Roles ----
    def get_realm_role(self, role_name: str) -> Dict[str, Any]:
        if settings.auth_dev_mode: return {"name":role_name}
        try:
            resp = self._request("GET", self._realm_url(f"/roles/{role_name}"), headers=self._headers(), timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise KeycloakError(f"Unable to fetch Keycloak role {role_name}: {exc}") from exc

    def assign_realm_role(self, user_id: str, role_name: str) -> None:
        if settings.auth_dev_mode:
            user=self.get_user(user_id)
            if role_name not in user.setdefault("roles",[]): user["roles"].append(role_name)
            return
        role = self.get_realm_role(role_name)
        try:
            resp = self._request(
                "POST",
                self._realm_url(f"/users/{user_id}/role-mappings/realm"),
                headers=self._headers(),
                json=[role],
                timeout=10,
            )
        except KeycloakError:
            raise
        if resp.status_code != 204:
            raise KeycloakError(f"Failed to assign role {role_name} to {user_id}: {resp.status_code} {resp.text}")

    def find_user(self, username: str):
        users=self.list_users(username)
        return users[0] if users else None

    def create_user_with_role(self, username, email, password, role_name, first_name="", last_name=""):
        uid=self.find_user(username)
        if uid: user_id=uid["id"]
        else: user_id=self.create_user(username,email,password,first_name,last_name)
        self.assign_realm_role(user_id,role_name)
        return user_id

    def register_user(self, username: str, email: str, password: str, role_name: str = "student",
                      first_name: str = "", last_name: str = "") -> str:
        if self.find_user(username):
            raise KeycloakError("Username is already registered")
        return self.create_user_with_role(username, email, password, role_name, first_name, last_name)


    # ---- Groups / organizational hierarchy ----
    def list_groups(self, parent_id: str | None = None):
        if settings.auth_dev_mode:
            return [g for g in self._dev_groups.values() if g.get("parent_id") == parent_id]
        path = "/groups" if parent_id is None else f"/groups/{parent_id}/children"
        try:
            resp = self._request("GET", self._realm_url(path), headers=self._headers(), timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise KeycloakError(f"Unable to list Keycloak groups: {exc}") from exc

    def find_group(self, name: str, parent_id: str | None = None):
        return next((g for g in self.list_groups(parent_id) if g.get("name") == name), None)

    def create_group(self, name: str, parent_id: str | None = None) -> str:
        if settings.auth_dev_mode:
            existing=self.find_group(name,parent_id)
            if existing: return existing["id"]
            group_id=str(uuid.uuid4()); self._dev_groups[group_id]={"id":group_id,"name":name,"parent_id":parent_id}; return group_id
        existing=self.find_group(name,parent_id)
        if existing: return existing["id"]
        path="/groups" if parent_id is None else f"/groups/{parent_id}/children"
        try:
            resp=self._request("POST", self._realm_url(path),headers=self._headers(),json={"name":name},timeout=10)
        except KeycloakError:
            raise
        if resp.status_code not in (201,409): raise KeycloakError(f"Failed to create group {name}: {resp.status_code} {resp.text}")
        loc=resp.headers.get("Location","")
        if loc: return loc.rstrip("/").split("/")[-1]
        found=self.find_group(name,parent_id)
        if found: return found["id"]
        raise KeycloakError("Group created but Keycloak did not return its id")

    def add_user_to_group(self, user_id: str, group_id: str) -> None:
        if settings.auth_dev_mode:
            self.get_user(user_id).setdefault("groups",[]).append(group_id); return
        try:
            resp=self._request("PUT", self._realm_url(f"/users/{user_id}/groups/{group_id}"),headers=self._headers(),timeout=10)
        except KeycloakError:
            raise
        if resp.status_code != 204: raise KeycloakError(f"Failed to add user to group: {resp.status_code} {resp.text}")

    # ---- Keycloak authentication sessions ----
    def list_user_sessions(self, user_id: str):
        if settings.auth_dev_mode:
            return list(self._dev_sessions.get(user_id, {}).values())
        try:
            resp=self._request("GET", self._realm_url(f"/users/{user_id}/sessions"),headers=self._headers(),timeout=10)
            resp.raise_for_status(); return resp.json()
        except requests.RequestException as exc:
            raise KeycloakError(f"Unable to list Keycloak sessions: {exc}") from exc

    def register_dev_session(self, user_id: str, session_id: str, client_id: str = "study-frontend") -> Dict[str, Any]:
        now=int(time.time()*1000); item={"id":session_id,"userId":user_id,"clientId":client_id,"ipAddress":"127.0.0.1","start":now,"lastAccess":now,"state":"active"}
        self._dev_sessions.setdefault(user_id,{})[session_id]=item; return item

    def get_user_by_username(self, username: str):
        return self.find_user(username)

    def count_user_sessions(self, user_id: str) -> int:
        return len(self.list_user_sessions(user_id))

    def logout_user_session(self, user_id: str, session_id: str) -> None:
        if settings.auth_dev_mode:
            self._dev_sessions.get(user_id, {}).pop(session_id, None); return
        try:
            resp=self._request("DELETE", self._realm_url(f"/sessions/{session_id}"),headers=self._headers(),timeout=10)
        except KeycloakError:
            raise
        if resp.status_code not in (204,404): raise KeycloakError(f"Failed to terminate Keycloak session: {resp.status_code} {resp.text}")

    def password_token(self, username: str, password: str) -> Dict[str, Any]:
        url = f"{settings.token_url}"
        data = {
            "grant_type": "password",
            "client_id": settings.keycloak_frontend_client_id,
            "username": username,
            "password": password,
            "scope": "openid profile email",
        }
        try:
            resp = self._request("POST", url, data=data, timeout=10)
        except KeycloakError:
            raise
        if resp.status_code != 200:
            raise KeycloakError("Invalid username or password")
        return resp.json()


token_validator = KeycloakTokenValidator()
keycloak_admin = KeycloakAdmin()
