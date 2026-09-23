"""Idempotent Keycloak provisioning for StudyInstitution."""
import sys,time,requests
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.config import get_settings
s=get_settings()
ACCESS_TOKEN_LIFESPAN_SECONDS = 900
SSO_SESSION_IDLE_SECONDS = 8 * 60 * 60
SSO_SESSION_MAX_SECONDS = 24 * 60 * 60

def wait():
    deadline=time.time()+120
    while time.time()<deadline:
        try:
            if requests.get(f"{s.keycloak_url}/realms/master",timeout=3).status_code==200:return
        except requests.RequestException: pass
        time.sleep(2)
    raise RuntimeError("Keycloak did not start")
def token():
    u=f"{s.keycloak_url}/realms/{s.keycloak_admin_realm}/protocol/openid-connect/token"
    r=requests.post(u,data={"grant_type":"password","client_id":s.keycloak_admin_client_id,"username":s.keycloak_admin,"password":s.keycloak_admin_password},timeout=10);r.raise_for_status();return r.json()["access_token"]
def h(t):return {"Authorization":f"Bearer {t}","Content-Type":"application/json"}
def realm(t):
    u=f"{s.keycloak_url}/admin/realms/{s.keycloak_realm}"
    r=requests.get(u,headers=h(t),timeout=10)
    realm_payload={
        "realm": s.keycloak_realm,
        "enabled": True,
        "registrationAllowed": True,
        "accessTokenLifespan": ACCESS_TOKEN_LIFESPAN_SECONDS,
        "accessTokenLifespanForImplicitFlow": ACCESS_TOKEN_LIFESPAN_SECONDS,
        "ssoSessionIdleTimeout": SSO_SESSION_IDLE_SECONDS,
        "ssoSessionMaxLifespan": SSO_SESSION_MAX_SECONDS,
        "eventsEnabled": True,
        "eventsListeners": ["jboss-logging"],
        "adminEventsEnabled": True,
        "adminEventsDetailsEnabled": True,
        "refreshTokenMaxReuse": 0,
        "revokeRefreshToken": False,
    }
    if r.status_code==404:
        r=requests.post(f"{s.keycloak_url}/admin/realms",headers=h(t),json=realm_payload,timeout=10);r.raise_for_status()
    else:
        r.raise_for_status()
        current = r.json()
        current.update(realm_payload)
        requests.put(u,headers=h(t),json=current,timeout=10).raise_for_status()
def role(t,name):
    u=f"{s.keycloak_url}/admin/realms/{s.keycloak_realm}/roles/{name}"
    if requests.get(u,headers=h(t),timeout=10).status_code==404:
        r=requests.post(f"{s.keycloak_url}/admin/realms/{s.keycloak_realm}/roles",headers=h(t),json={"name":name},timeout=10);r.raise_for_status()
def client(t,client_id,secret=None,public=False):
    root=f"{s.keycloak_url}/admin/realms/{s.keycloak_realm}"
    r=requests.get(f"{root}/clients",headers=h(t),params={"clientId":client_id},timeout=10);r.raise_for_status()
    payload={"clientId":client_id,"name":client_id,"enabled":True,"publicClient":public,"protocol":"openid-connect","standardFlowEnabled":public,"directAccessGrantsEnabled":True,"serviceAccountsEnabled":not public,"redirectUris":["*"],"webOrigins":["*"],"defaultClientScopes":["basic","profile","email","roles"],"optionalClientScopes":["address","phone","offline_access"]}
    if secret: payload["secret"]=secret
    if r.json():
        client_id_internal=r.json()[0]["id"]
        requests.put(f"{root}/clients/{client_id_internal}",headers=h(t),json=payload,timeout=10).raise_for_status()
    else:
        response=requests.post(f"{root}/clients",headers=h(t),json=payload,timeout=10); response.raise_for_status()
        client_id_internal=response.headers["Location"].rstrip("/").split("/")[-1]
    scopes=requests.get(f"{root}/client-scopes",headers=h(t),timeout=10); scopes.raise_for_status()
    basic=next((scope for scope in scopes.json() if scope.get("name")=="basic"),None)
    if basic:
        attached=requests.get(f"{root}/clients/{client_id_internal}/default-client-scopes",headers=h(t),timeout=10); attached.raise_for_status()
        if not any(scope.get("id")==basic["id"] for scope in attached.json()):
            response=requests.put(f"{root}/clients/{client_id_internal}/default-client-scopes/{basic['id']}",headers=h(t),timeout=10)
            if response.status_code not in (204,409): response.raise_for_status()
def user(t,username,password,role_name):
    root=f"{s.keycloak_url}/admin/realms/{s.keycloak_realm}"
    r=requests.get(f"{root}/users",headers=h(t),params={"username":username},timeout=10);r.raise_for_status()
    is_admin=role_name==s.keycloak_admin_role
    payload={"username":username,"email":username,"firstName":"Platform" if is_admin else "Demo","lastName":"Administrator" if is_admin else "Student","enabled":True,"emailVerified":True,"requiredActions":[]}
    users = r.json()
    if users:
        uid=users[0]["id"]
        requests.put(f"{root}/users/{uid}",headers=h(t),json=payload,timeout=10).raise_for_status()
        requests.put(f"{root}/users/{uid}/reset-password",headers=h(t),json={"type":"password","value":password,"temporary":False},timeout=10).raise_for_status()
    else:
        payload["credentials"]=[{"type":"password","value":password,"temporary":False}]
        r=requests.post(f"{root}/users",headers=h(t),json=payload,timeout=10);r.raise_for_status();uid=r.headers["Location"].rstrip("/").split("/")[-1]
    rr=requests.get(f"{root}/roles/{role_name}",headers=h(t),timeout=10);rr.raise_for_status()
    requests.post(f"{root}/users/{uid}/role-mappings/realm",headers=h(t),json=[rr.json()],timeout=10).raise_for_status()
def main():
    wait();t=token();realm(t)
    client(t,s.keycloak_backend_client_id,s.keycloak_backend_client_secret,False)
    client(t,s.keycloak_frontend_client_id,public=True)
    for r in ["super_admin","institution_admin","professor","student"]:role(t,r)
    user(t,s.default_admin_username,s.default_admin_password,"super_admin")
    user(t,s.default_student_username,s.default_student_password,"student")
    print("StudyInstitution Keycloak provisioning complete.")
if __name__=="__main__":main()
