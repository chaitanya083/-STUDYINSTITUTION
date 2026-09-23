from typing import List
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt as dev_jwt
from app.config import get_settings
from app.keycloak import token_validator, KeycloakError
from app.schemas import CurrentUser
settings=get_settings(); bearer_scheme=HTTPBearer(auto_error=False)

def _extract_roles(claims:dict)->List[str]:
    out=list(claims.get("realm_access",{}).get("roles",[]) or [])
    for v in (claims.get("resource_access",{}) or {}).values(): out.extend(v.get("roles",[]) or [])
    return sorted(set(out))
def _decode(token):
    if settings.auth_dev_mode:
        try: return dev_jwt.decode(token,settings.app_secret_key,algorithms=["HS256"])
        except Exception as e: raise HTTPException(401,f"Invalid dev token: {e}")
    try: return token_validator.decode(token)
    except KeycloakError as e: raise HTTPException(401,str(e),headers={"WWW-Authenticate":"Bearer"})
def get_current_user(c:HTTPAuthorizationCredentials|None=Depends(bearer_scheme)):
    if c is None:
        raise HTTPException(401,"Not authenticated",headers={"WWW-Authenticate":"Bearer"})
    claims=_decode(c.credentials); sub=claims.get("sub")
    if not sub: raise HTTPException(401,"Token missing subject")
    return CurrentUser(sub=sub,username=claims.get("preferred_username",sub),email=claims.get("email"),roles=_extract_roles(claims),session_state=claims.get("session_state"))
def require_role(role):
    def dep(user=Depends(get_current_user)):
        if role not in user.roles: raise HTTPException(403,f"Requires '{role}' role")
        return user
    return dep
require_super_admin=require_role("super_admin")
require_admin=require_super_admin
require_institution_admin=require_role("institution_admin")
require_professor=require_role("professor")
require_student=require_role("student")
