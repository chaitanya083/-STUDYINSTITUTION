import uuid
from datetime import timedelta

import pytest
import requests

from app import models
from app.database import SessionLocal
from app.keycloak import KeycloakError, keycloak_admin
from app.models import utcnow
from tests.conftest import auth_header

def setup_tenant(client, super_h, name, plan_name="Basic"):
    plan = next(p for p in client.get("/plans", headers=super_h).json() if p["name"] == plan_name)
    inst = client.post("/admin/institutions", headers=super_h, json={"name": name, "code": name.upper()}).json()
    selected = client.post(f"/admin/institutions/{inst['id']}/select-plan", headers=super_h, json={"plan_id": plan["id"]})
    assert selected.status_code == 201
    sub = selected.json()["subscription"]
    if plan_name != "Free":
        assert sub["status"] == "pending"
        assert client.post(f"/admin/subscriptions/{sub['id']}/payment", headers=super_h).status_code == 201
        assert client.post(f"/admin/subscriptions/{sub['id']}/activate", headers=super_h).status_code == 200
    return inst

def admin(client, super_h, inst):
    uid = "ia-" + str(uuid.uuid4())
    r = client.post(f"/admin/institutions/{inst['id']}/admins", headers=super_h, json={"user_id": uid, "role": "institution_admin", "password": "Admin@12345"})
    assert r.status_code == 201
    return uid, auth_header(uid, ["institution_admin"])

def test_keycloak_admin_surfaces_unavailable_keycloak_as_keycloak_error(monkeypatch):
    def fake_post(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Keycloak is down")

    monkeypatch.setattr("app.keycloak.requests.post", fake_post)

    with pytest.raises(KeycloakError):
        keycloak_admin._get_admin_token()


def test_fixed_plan_catalog_has_four_types(client, super_headers):
    plans = client.get("/plans", headers=super_headers)
    assert plans.status_code == 200
    names = {x["name"] for x in plans.json()}
    assert names >= {"Free", "Basic", "Premium", "Custom"}

def test_full_workflow_with_keycloak_sessions(client, super_headers):
    inst = setup_tenant(client, super_headers, "Alpha")
    aid, ah = admin(client, super_headers, inst)
    pid, _ = ("p-" + str(uuid.uuid4()), None)
    sid = "s-" + str(uuid.uuid4())
    assert client.post("/institutions/me/professors", headers=ah, json={"user_id": pid, "role": "professor", "password": "Professor@123"}).status_code == 201
    assert client.post("/institutions/me/students", headers=ah, json={"user_id": sid, "role": "student", "password": "Student@123"}).status_code == 201
    ph = auth_header(pid, ["professor"])
    sh = auth_header(sid, ["student"], "kc-session-1")
    c = client.post("/institutions/me/courses", headers=ah, json={"title": "Python"}).json()
    assert client.post(f"/professor/courses/{c['id']}/assign", headers=ph, json={"user_id": sid}).status_code == 200
    assert client.get("/student/courses", headers=sh).json()[0]["id"] == c["id"]
    session = client.post("/sessions/login", headers=sh, json={}).json()
    assert session["id"] == "kc-session-1"
    watch = client.post(f"/courses/{c['id']}/access", headers=sh, json={"keycloak_session_id": session["id"]})
    assert watch.status_code == 201
    assert client.get("/sessions", headers=sh).json()[0]["id"] == session["id"]
    assert client.delete(f"/sessions/{session['id']}", headers=sh).status_code == 200
    assert client.post(f"/watch-sessions/{watch.json()['id']}/end", headers=sh).status_code == 200

def test_concurrent_session_limit_is_checked_in_keycloak(client, super_headers):
    inst = setup_tenant(client, super_headers, "Seats", "Basic")
    _, ah = admin(client, super_headers, inst)
    sid = "student-" + str(uuid.uuid4())
    assert client.post("/institutions/me/students", headers=ah, json={"user_id": sid, "role": "student", "password": "Student@123"}).status_code == 201
    h1 = auth_header(sid, ["student"], "kc-1")
    h2 = auth_header(sid, ["student"], "kc-2")
    assert client.post("/sessions/login", headers=h1, json={}).status_code == 200
    assert client.post("/sessions/login", headers=h2, json={}).status_code == 200
    h3 = auth_header(sid, ["student"], "kc-3")
    assert client.post("/sessions/login", headers=h3, json={}).status_code == 409

def test_session_ownership(client, super_headers):
    inst = setup_tenant(client, super_headers, "Ownership")
    _, ah = admin(client, super_headers, inst)
    sid = "student-" + str(uuid.uuid4())
    client.post("/institutions/me/students", headers=ah, json={"user_id": sid, "role": "student", "password": "Student@123"})
    sh = auth_header(sid, ["student"], "kc-own")
    client.post("/sessions/login", headers=sh, json={})
    other = auth_header("other", ["student"], "kc-other")
    assert client.delete("/sessions/kc-own", headers=other).status_code in (403, 404)

def test_custom_plan_request_and_quote(client, super_headers):
    inst = setup_tenant(client, super_headers, "CustomInst", "Free")
    _, ah = admin(client, super_headers, inst)
    req = client.post("/institutions/me/custom-plan-requests", headers=ah, json={"branches": 5, "admins": 5, "professors": 50, "students": 5000, "courses": 500, "concurrent_sessions": 10, "duration_days": 365, "additional_features": "SSO; advanced analytics"})
    assert req.status_code == 201
    assert req.json()["quoted_price"] == 1095000.0
    assert req.json()["status"] == "quoted"
    rid = req.json()["id"]
    q = client.post(f"/admin/custom-plan-requests/{rid}/quote", headers=super_headers)
    assert q.status_code == 200
    assert q.json()["status"] == "quoted"
    assert q.json()["quoted_price"] == 1095000.0
    assert client.post(f"/institutions/me/custom-plan-requests/{rid}/accept", headers=ah).status_code == 200

def test_cross_tenant_isolation(client, super_headers):
    a = setup_tenant(client, super_headers, "TenantA")
    b = setup_tenant(client, super_headers, "TenantB")
    _, ah = admin(client, super_headers, a)
    assert client.get(f"/admin/institutions/{b['id']}/members", headers=ah).status_code == 403

def test_subscription_expiry_blocks_access(client, super_headers):
    inst = setup_tenant(client, super_headers, "Expiry")
    _, ah = admin(client, super_headers, inst)
    sid = "student-" + str(uuid.uuid4())
    client.post("/institutions/me/students", headers=ah, json={"user_id": sid, "role": "student", "password": "Student@123"})
    db = SessionLocal(); sub = db.query(models.InstitutionSubscription).filter_by(institution_id=inst["id"]).first(); sub.end_date = utcnow() - timedelta(days=1); db.commit(); db.close()
    assert client.post("/sessions/login", headers=auth_header(sid, ["student"], "expired-session"), json={}).status_code == 402

