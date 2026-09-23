"""StudyInstitution multi-tenant educational platform API."""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from app import crud, schemas, models
from app.database import get_db, init_db
from app.keycloak import keycloak_admin, KeycloakError
from app.security import get_current_user, require_super_admin, require_admin, require_institution_admin, require_professor, require_student
from app.config import get_settings
settings=get_settings()

OPENAPI_TAGS=[
    {"name":"1. Authentication","description":"Start here: register a user, obtain a token, then inspect the signed-in identity."},
    {"name":"2. Plan Selection","description":"Review the plan catalog and submit or manage a custom-plan request."},
    {"name":"3. Super Admin Institution Setup","description":"Super Admin workflow: create an institution, select its plan, and inspect platform institutions."},
    {"name":"4. Institution Access & Subscription","description":"Institution workflow: inspect the tenant, subscription, payment, and activation state."},
    {"name":"5. User Management","description":"After activation: create the institution admin, organization hierarchy, professors, and students."},
    {"name":"6. Courses","description":"Create institution courses, list them, and assign them to learners."},
    {"name":"7. Learning","description":"Student workflow: view assigned courses, start learning, end watch sessions, and view history."},
    {"name":"8. Sessions","description":"Student session workflow: sign in, list active Keycloak sessions, and terminate a session."},
    {"name":"9. Subscriptions","description":"Compatibility endpoints for individual user subscriptions."},
    {"name":"10. Super Admin Monitoring","description":"Super Admin tools for platform users and user session inspection."},
    {"name":"System","description":"Service health and API identity endpoints."},
]

@asynccontextmanager
async def lifespan(app):
    init_db()
    try:
        sync_keycloak_hierarchy()
    except KeycloakError:
        # Keycloak may still be starting when the API container boots.
        pass
    yield
API_DESCRIPTION="""Multi-tenant education API with Keycloak authentication and SQLite business data.

## Recommended workflow

1. Authenticate with `POST /auth/token` and authorize Swagger with the returned Bearer token.
2. Review plans with `GET /plans`.
3. As Super Admin, create an institution with `POST /admin/institutions`.
4. Select a plan with `POST /admin/institutions/{institution_id}/select-plan`.
5. For paid plans, create a payment and activate the subscription.
6. Create the institution admin with `POST /admin/institutions/{institution_id}/admins`.
7. Sign in as the institution admin, create the organization hierarchy, users, and courses.
8. Assign courses to students, then use the Learning and Sessions sections.

The numbered Swagger sections follow this workflow. Every application endpoint is assigned to one section.

"""
app=FastAPI(title="StudyInstitution API",version="3.0.0",description=API_DESCRIPTION,lifespan=lifespan,openapi_tags=OPENAPI_TAGS,swagger_ui_parameters={"deepLinking":True,"defaultModelsExpandDepth":-1})
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

@app.get("/dashboard",tags=["System"],summary="Open the role-based dashboard",description="Returns the StudyInstitution web dashboard. Sign in on the page to load live data for the current user's role.")
def dashboard():
    return FileResponse(Path(__file__).parent.parent / "dashboard" / "index.html",media_type="text/html")

@app.get("/dashboard/summary",response_model=schemas.DashboardSummary,tags=["System"],summary="Get dashboard KPIs",description="Returns the KPI values displayed by the role-based dashboard for the authenticated user.")
def dashboard_summary(db:Session=Depends(get_db),u=Depends(get_current_user)):
    if u.has_role("super_admin"):
        institutions=db.query(models.Institution).all()
        memberships=db.query(models.InstitutionMembership).filter_by(is_active=True).all()
        courses=db.query(models.Course).count()
        active_institutions=sum(1 for institution in institutions if institution.status==models.InstitutionStatus.active)
    else:
        iid=inst_for(db,u)
        institutions=[crud.get_institution(db,iid)]
        memberships=db.query(models.InstitutionMembership).filter_by(institution_id=iid,is_active=True).all()
        courses=db.query(models.Course).filter_by(institution_id=iid).count()
        active_institutions=sum(1 for institution in institutions if institution.status==models.InstitutionStatus.active)
    user_ids={membership.user_id for membership in memberships}
    if u.has_role("super_admin"):
        try: user_ids.update(user.get("id") for user in keycloak_admin.list_users() if user.get("id"))
        except KeycloakError: pass
    active_sessions=0
    for user_id in user_ids:
        try: active_sessions += len(keycloak_admin.list_user_sessions(user_id))
        except KeycloakError: continue
    subscription_status="Active" if active_institutions else "Pending"
    progress=round(active_institutions / len(institutions) * 100) if institutions else 0
    if not u.has_role("super_admin"):
        subscription=crud.latest_institution_subscription(db,institutions[0].id)
        subscription_status=subscription.status.value.title() if subscription else "Not selected"
        progress=100 if subscription and subscription.is_currently_valid() else 0
    return schemas.DashboardSummary(
        institutions=len(institutions),
        active_students=sum(1 for membership in memberships if membership.role==models.MembershipRole.student),
        professors=sum(1 for membership in memberships if membership.role==models.MembershipRole.professor),
        active_courses=courses,
        active_sessions=active_sessions,
        subscription_status=subscription_status,
        subscription_progress=progress,
    )

app.mount("/dashboard",StaticFiles(directory=Path(__file__).parent.parent / "dashboard"),name="dashboard-assets")

def handle(e): raise HTTPException(e.status_code,e.message)
def inst_for(db,user,roles=("institution_admin","professor","student")):
    try: return crud.institution_for_user(db,user,roles)
    except crud.DomainError as e: handle(e)

def bundle_out(x):
    return schemas.BundleOut(id=x.id,name=x.name,price=x.price,duration_days=x.duration_days,
        max_branches=x.max_branches,max_admins=x.max_admins,max_students=x.max_students,max_professors=x.max_professors,course_quota=x.course_quota,
        concurrent_sessions=x.concurrent_sessions,description=x.description,is_active=x.is_active,
        plan_ids=[i.plan_id for i in x.items])

def plan_selection_out(result):
    institution,plan,bundle,subscription=result
    return schemas.PlanSelectionOut(institution=institution,plan=plan,bundle=bundle_out(bundle),subscription=subscription)

def sync_keycloak_hierarchy():
    """Reconcile SQLite organization records into durable Keycloak groups."""
    from app.database import SessionLocal
    db=SessionLocal()
    try:
        root=keycloak_admin.create_group("Institutions")
        for institution in db.query(models.Institution).all():
            institution_group=keycloak_admin.create_group(institution.code,root)
            branch_groups={}
            for branch in db.query(models.Branch).filter_by(institution_id=institution.id).all():
                branch_group=keycloak_admin.create_group(branch.code,institution_group)
                branch_groups[branch.id]=branch_group
                for department in db.query(models.Department).filter_by(branch_id=branch.id).all():
                    department_group=keycloak_admin.create_group(department.code,branch_group)
                    for section in db.query(models.Section).filter_by(department_id=department.id).all():
                        keycloak_admin.create_group(section.name,department_group)
            for membership in db.query(models.InstitutionMembership).filter_by(institution_id=institution.id,is_active=True).all():
                user_group=institution_group
                if membership.branch_id: user_group=branch_groups.get(membership.branch_id,user_group)
                if membership.department_id:
                    department=db.query(models.Department).filter_by(id=membership.department_id).first()
                    if department and department.branch_id in branch_groups:
                        user_group=keycloak_admin.create_group(department.code,branch_groups[department.branch_id])
                if membership.section_id:
                    section=db.query(models.Section).filter_by(id=membership.section_id).first()
                    department=db.query(models.Department).filter_by(id=section.department_id).first() if section else None
                    if section and department and department.branch_id in branch_groups:
                        department_group=keycloak_admin.create_group(department.code,branch_groups[department.branch_id])
                        user_group=keycloak_admin.create_group(section.name,department_group)
                try:
                    keycloak_admin.add_user_to_group(membership.user_id,user_group)
                except KeycloakError:
                    # A legacy membership can reference an identity deleted in Keycloak.
                    continue
    finally:
        db.close()

@app.get("/me",response_model=schemas.CurrentUser,tags=["1. Authentication"])
def me(u=Depends(get_current_user)): return u

# ---------- Keycloak-backed registration and login ----------
@app.post("/auth/register",response_model=schemas.UserRegistrationOut,status_code=201,tags=["1. Authentication"])
def register(d:schemas.RegistrationRequest):
    try:
        user_id=keycloak_admin.register_user(d.username,d.email,d.password,"student",d.first_name,d.last_name)
        return {"user_id":user_id,"username":d.username,"role":"student"}
    except KeycloakError as e: raise HTTPException(409,str(e))

@app.post("/auth/token",response_model=schemas.TokenOut,tags=["1. Authentication"])
def token(d:schemas.TokenRequest):
    try:
        payload=keycloak_admin.password_token(d.username,d.password)
        return {"access_token":payload["access_token"],"token_type":payload.get("token_type","Bearer"),"expires_in":payload.get("expires_in",0),"refresh_expires_in":payload.get("refresh_expires_in",0),"refresh_token":payload.get("refresh_token"),"session_state":payload.get("session_state")}
    except KeycloakError as e: raise HTTPException(401,str(e),headers={"WWW-Authenticate":"Bearer"})

# ---------- Plan catalog ----------
# Plans are visible after login. The plan itself contains every limit used by the bundle.
@app.get("/plans",response_model=list[schemas.PlanOut],tags=["2. Plan Selection"])
def plans(db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(get_current_user)):
    return crud.list_plans(db)

@app.get("/plans/{plan_id}",response_model=schemas.PlanOut,tags=["2. Plan Selection"])
def plan_details(plan_id:str,db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(get_current_user)):
    return crud.get_plan(db,plan_id)

# ---------- Super Admin platform management ----------
@app.post("/admin/plans",response_model=schemas.PlanOut,status_code=201,tags=["2. Plan Selection"])
def create_plan(d:schemas.PlanCreate,db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(require_super_admin)):
    try:return crud.create_plan(db,d)
    except crud.DomainError as e:handle(e)

@app.put("/admin/plans/{id}",response_model=schemas.PlanOut,tags=["2. Plan Selection"])
def update_plan(id:str,d:schemas.PlanUpdate,db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(require_super_admin)):
    try:return crud.update_plan(db,id,d)
    except crud.DomainError as e:handle(e)

@app.delete("/admin/plans/{id}",response_model=schemas.Message,tags=["2. Plan Selection"])
def delete_plan(id:str,db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(require_super_admin)):
    try:crud.delete_plan(db,id);return {"detail":"Plan deleted"}
    except crud.DomainError as e:handle(e)

# Manual bundle creation is intentionally removed from the business workflow.
@app.get("/bundles",response_model=list[schemas.BundleOut],tags=["2. Plan Selection"])
def bundles(db:Session=Depends(get_db),u=Depends(get_current_user)):
    if u.has_role("super_admin"):
        xs=db.query(models.PlanBundle).filter(models.PlanBundle.is_active.is_(True)).all()
    else:
        iid=inst_for(db,u); xs=crud.list_generated_bundles_for_institution(db,iid)
    return [bundle_out(x) for x in xs]

def institution_id_for_request(db:Session,u,institution_id:str|None):
    if u.has_role("super_admin"):
        if not institution_id:
            raise HTTPException(400,"Super admins must provide institution_id for institution-scoped endpoints")
        crud.get_institution(db,institution_id)
        return institution_id
    return inst_for(db,u)

@app.get("/institutions/me/bundle",response_model=schemas.BundleOut,tags=["4. Institution Access & Subscription"])
def my_bundle(institution_id:str|None=Query(None),db:Session=Depends(get_db),u=Depends(get_current_user)):
    iid=institution_id_for_request(db,u,institution_id); sub=crud.latest_institution_subscription(db,iid)
    if not sub: raise HTTPException(402,"Select a plan before using institution features")
    return bundle_out(sub.bundle)

@app.post("/admin/institutions",response_model=schemas.InstitutionOut,status_code=201,tags=["3. Super Admin Institution Setup"])
def create_institution(d:schemas.InstitutionCreate,db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(require_super_admin)):
    try:
        obj=crud.create_institution(db,d)
        try:
            root_gid=keycloak_admin.create_group("Institutions"); keycloak_admin.create_group(obj.code,root_gid)
        except KeycloakError as e:
            raise HTTPException(502,str(e))
        return obj
    except crud.DomainError as e:handle(e)

@app.get("/admin/institutions",response_model=list[schemas.InstitutionOut],tags=["3. Super Admin Institution Setup"])
def institutions(db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(require_super_admin)):return db.query(models.Institution).order_by(models.Institution.name.asc()).all()

@app.get("/institutions/me",response_model=schemas.InstitutionOut,tags=["4. Institution Access & Subscription"])
def my_institution(institution_id:str|None=Query(None),db:Session=Depends(get_db),u=Depends(get_current_user)):
    iid=institution_id_for_request(db,u,institution_id); return crud.get_institution(db,iid)

@app.post("/admin/institutions/{institution_id}/select-plan",response_model=schemas.PlanSelectionOut,status_code=201,tags=["3. Super Admin Institution Setup"])
def super_select_plan(institution_id:str,d:schemas.PlanSelectionRequest,db:Session=Depends(get_db),u=Depends(require_super_admin)):
    try:return plan_selection_out(crud.select_plan_for_institution(db,institution_id,d.plan_id,u.sub))
    except crud.DomainError as e:handle(e)

@app.post("/institutions/me/select-plan",response_model=schemas.PlanSelectionOut,status_code=201,tags=["4. Institution Access & Subscription"])
def institution_select_plan(d:schemas.PlanSelectionRequest,institution_id:str|None=Query(None),db:Session=Depends(get_db),u=Depends(get_current_user)):
    if u.has_role("super_admin"):
        if not institution_id:
            raise HTTPException(400,"Super admins must provide institution_id for institution-scoped endpoints")
        target_institution=institution_id
    else:
        if not u.has_role("institution_admin"):
            raise HTTPException(403,"Requires 'institution_admin' role")
        target_institution=inst_for(db,u,("institution_admin",))
    try:return plan_selection_out(crud.select_plan_for_institution(db,target_institution,d.plan_id,u.sub))
    except crud.DomainError as e:handle(e)

@app.get("/institutions/me/subscription",response_model=schemas.InstitutionSubscriptionOut,tags=["4. Institution Access & Subscription"])
def institution_subscription(institution_id:str|None=Query(None),db:Session=Depends(get_db),u=Depends(get_current_user)):
    iid=institution_id_for_request(db,u,institution_id); s=crud.latest_institution_subscription(db,iid)
    if not s: raise HTTPException(402,"Institution subscription missing or expired")
    return s

# ---------- Payments / activation ----------
@app.post("/institutions/me/subscriptions/{subscription_id}/payment",response_model=dict,status_code=201,tags=["4. Institution Access & Subscription"])
def create_payment(subscription_id:str,db:Session=Depends(get_db),u=Depends(get_current_user)):
    iid=inst_for(db,u); sub=db.query(models.InstitutionSubscription).filter_by(id=subscription_id,institution_id=iid).first()
    if not sub: raise HTTPException(404,"Subscription not found")
    if sub.status==models.SubscriptionStatus.active: raise HTTPException(409,"Subscription is already active")
    payment=models.Payment(institution_id=iid,subscription_id=sub.id,amount=sub.bundle.price,currency="INR",status=models.PaymentStatus.pending); db.add(payment); db.commit(); db.refresh(payment)
    return {"payment_id":payment.id,"amount":payment.amount,"currency":payment.currency,"status":payment.status.value,"message":"Payment intent created. Connect your payment gateway here."}

@app.post("/admin/subscriptions/{subscription_id}/payment",response_model=dict,status_code=201,tags=["4. Institution Access & Subscription"])
def admin_create_payment(subscription_id:str,db:Session=Depends(get_db),u=Depends(require_super_admin)):
    sub=db.query(models.InstitutionSubscription).filter_by(id=subscription_id).first()
    if not sub: raise HTTPException(404,"Subscription not found")
    payment=models.Payment(institution_id=sub.institution_id,subscription_id=sub.id,amount=sub.bundle.price,currency="INR",status=models.PaymentStatus.pending); db.add(payment); db.commit(); db.refresh(payment)
    return {"payment_id":payment.id,"amount":payment.amount,"currency":payment.currency,"status":payment.status.value,"message":"Payment intent created; confirm payment through the configured gateway before activation."}

@app.post("/admin/subscriptions/{subscription_id}/activate",response_model=schemas.InstitutionSubscriptionOut,tags=["4. Institution Access & Subscription"])
def activate_subscription(subscription_id:str,db:Session=Depends(get_db),u=Depends(require_super_admin)):
    try:
        sub=crud.activate_institution_subscription(db,subscription_id,"manual-admin-confirmation")
        return sub
    except crud.DomainError as e:handle(e)

# ---------- User provisioning ----------
def provision_user(db,institution_id,d,creator,allowed):
    if d.role not in allowed: raise HTTPException(403,"You cannot create this role")
    try:
        # Validate the tenant and seat before creating an external identity.
        sub=crud.require_institution_access(db,institution_id)
        if creator.has_role("professor"):
            crud.validate_member_scope(db,creator.sub,d.branch_id,d.department_id,d.section_id)
            creator_membership=db.query(models.InstitutionMembership).filter_by(user_id=creator.sub,institution_id=institution_id,is_active=True).first()
            if creator_membership:
                d.branch_id=d.branch_id or creator_membership.branch_id; d.department_id=d.department_id or creator_membership.department_id; d.section_id=d.section_id or creator_membership.section_id
        if d.role=="institution_admin" and crud.count_role(db,institution_id,"institution_admin")>=sub.bundle.max_admins:
            raise HTTPException(409,"Institution already has an Institution Admin")
        if d.role=="student" and crud.count_role(db,institution_id,"student")>=sub.bundle.max_students: raise HTTPException(403,"Student seat limit reached")
        if d.role=="professor" and crud.count_role(db,institution_id,"professor")>=sub.bundle.max_professors: raise HTTPException(403,"Professor seat limit reached")
        # In production Keycloak is the password/identity store.
        if settings.auth_dev_mode:
            if not d.user_id:
                raise HTTPException(422,"user_id is required in development authentication mode")
            uid=d.user_id
        else:
            if not d.username: raise HTTPException(422,"username is required in production")
            uid=keycloak_admin.create_user_with_role(d.username,d.username,d.password,d.role)
            # Mirror the institution hierarchy into Keycloak Groups/Subgroups.
            root_gid=keycloak_admin.create_group("Institutions")
            inst_obj=crud.get_institution(db,institution_id); inst_gid=keycloak_admin.create_group(inst_obj.code,root_gid)
            parent_gid=inst_gid
            if d.branch_id:
                branch=db.query(models.Branch).filter_by(id=d.branch_id,institution_id=institution_id).first()
                if not branch: raise HTTPException(404,"Branch not found in this institution")
                parent_gid=keycloak_admin.create_group(branch.code,parent_gid)
            if d.department_id:
                dept=db.query(models.Department).filter_by(id=d.department_id).first()
                if not dept or db.query(models.Branch).filter_by(id=dept.branch_id,institution_id=institution_id).first() is None: raise HTTPException(404,"Department not found in this institution")
                parent_gid=keycloak_admin.create_group(dept.code,parent_gid)
            if d.section_id:
                sec=db.query(models.Section).filter_by(id=d.section_id).first()
                if not sec: raise HTTPException(404,"Section not found")
                parent_gid=keycloak_admin.create_group(sec.name,parent_gid)
            keycloak_admin.add_user_to_group(uid,parent_gid)
        return crud.add_membership(db,institution_id,uid,d.role,d.display_name,d.branch_id,d.department_id,d.section_id)
    except crud.DomainError as e:handle(e)
    except KeycloakError as e:raise HTTPException(502,str(e))


@app.post("/admin/institutions/{institution_id}/admins",response_model=schemas.MembershipOut,status_code=201,tags=["5. User Management"],summary="Create Institution Admin",description="Create the first institution administrator after the institution plan is active. The new admin can then create branches, departments, sections, professors, students, and courses through the institution-scoped endpoints. Branch and department IDs are intentionally not part of this request.")
def create_admin(institution_id:str,d:schemas.InstitutionAdminCreate,db:Session=Depends(get_db),u:schemas.CurrentUser=Depends(require_super_admin)):
    user=schemas.UserCreate(user_id=d.user_id,username=d.username,display_name=d.display_name,role="institution_admin",password=d.password)
    try:return provision_user(db,institution_id,user,u,["institution_admin"])
    except crud.DomainError as e:handle(e)

@app.post("/institutions/me/admins",response_model=schemas.MembershipOut,status_code=201,tags=["5. User Management"])
def create_institution_admin(d:schemas.UserCreate,db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    return provision_user(db,inst_for(db,u,("institution_admin",)),d,u,["institution_admin"])

@app.post("/institutions/me/professors",response_model=schemas.MembershipOut,status_code=201,tags=["5. User Management"])
def create_professor(d:schemas.UserCreate,db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    return provision_user(db,inst_for(db,u,("institution_admin",)),d,u,["professor"])

@app.post("/institutions/me/students",response_model=schemas.MembershipOut,status_code=201,tags=["5. User Management"])
def create_student(d:schemas.UserCreate,db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    return provision_user(db,inst_for(db,u,("institution_admin",)),d,u,["student"])

@app.get("/professor/students",response_model=list[schemas.MembershipOut],tags=["5. User Management"])
def professor_students(db:Session=Depends(get_db),u=Depends(require_professor)):
    iid=inst_for(db,u,("professor",))
    return db.query(models.InstitutionMembership).filter_by(institution_id=iid,role=models.MembershipRole.student,is_active=True).all()

@app.post("/professor/students",response_model=schemas.MembershipOut,status_code=201,tags=["5. User Management"])
def professor_create_student(d:schemas.UserCreate,db:Session=Depends(get_db),u=Depends(require_professor)):
    return provision_user(db,inst_for(db,u,("professor",)),d,u,["student"])

@app.get("/institutions/me/members",response_model=list[schemas.MembershipOut],tags=["5. User Management"])
def members(db:Session=Depends(get_db),u=Depends(get_current_user)):
    iid=inst_for(db,u); return db.query(models.InstitutionMembership).filter_by(institution_id=iid,is_active=True).order_by(models.InstitutionMembership.role.asc(),models.InstitutionMembership.display_name.asc()).all()

@app.get("/institutions/me/students",response_model=list[schemas.MembershipOut],tags=["5. User Management"],summary="List My Institution Students",description="List active students and their user_id values. Use one of these user_id values when assigning a course.")
def institution_students(db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    iid=inst_for(db,u,("institution_admin",))
    return db.query(models.InstitutionMembership).filter_by(institution_id=iid,role=models.MembershipRole.student,is_active=True).order_by(models.InstitutionMembership.display_name.asc()).all()

@app.get("/admin/institutions/{institution_id}/members",response_model=list[schemas.MembershipOut],tags=["5. User Management"])
def admin_members(institution_id:str,db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(require_super_admin)):
    crud.get_institution(db,institution_id)
    return db.query(models.InstitutionMembership).filter_by(institution_id=institution_id,is_active=True).all()

# ---------- Organization hierarchy ----------
@app.get("/institutions/me/branches",response_model=list[schemas.BranchOut],tags=["5. User Management"],summary="List My Institution Branches",description="List every branch in the signed-in institution. Use the returned branch IDs when creating departments.")
def list_my_branches(db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    iid=inst_for(db,u,("institution_admin",))
    return db.query(models.Branch).filter_by(institution_id=iid).order_by(models.Branch.name.asc()).all()

@app.get("/institutions/me/departments",response_model=list[schemas.DepartmentOut],tags=["5. User Management"],summary="List My Institution Departments",description="List departments in the signed-in institution. Pass branch_id to filter departments for one branch.")
def list_my_departments(branch_id:str|None=Query(None),db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    iid=inst_for(db,u,("institution_admin",))
    query=db.query(models.Department).join(models.Branch).filter(models.Branch.institution_id==iid)
    if branch_id: query=query.filter(models.Department.branch_id==branch_id)
    return query.order_by(models.Department.name.asc()).all()

@app.get("/institutions/me/sections",response_model=list[schemas.SectionOut],tags=["5. User Management"],summary="List My Institution Sections",description="List sections in the signed-in institution. Pass department_id to filter sections for one department.")
def list_my_sections(department_id:str|None=Query(None),db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    iid=inst_for(db,u,("institution_admin",))
    query=db.query(models.Section).join(models.Department).join(models.Branch).filter(models.Branch.institution_id==iid)
    if department_id: query=query.filter(models.Section.department_id==department_id)
    return query.order_by(models.Section.name.asc()).all()

@app.get("/admin/institutions/{institution_id}/branches",response_model=list[schemas.BranchOut],tags=["5. User Management"],summary="List Institution Branches")
def list_admin_branches(institution_id:str,db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(require_super_admin)):
    crud.get_institution(db,institution_id)
    return db.query(models.Branch).filter_by(institution_id=institution_id).order_by(models.Branch.name.asc()).all()

@app.get("/admin/institutions/{institution_id}/departments",response_model=list[schemas.DepartmentOut],tags=["5. User Management"],summary="List Institution Departments")
def list_admin_departments(institution_id:str,branch_id:str|None=Query(None),db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(require_super_admin)):
    crud.get_institution(db,institution_id)
    query=db.query(models.Department).join(models.Branch).filter(models.Branch.institution_id==institution_id)
    if branch_id: query=query.filter(models.Department.branch_id==branch_id)
    return query.order_by(models.Department.name.asc()).all()

@app.get("/admin/institutions/{institution_id}/sections",response_model=list[schemas.SectionOut],tags=["5. User Management"],summary="List Institution Sections")
def list_admin_sections(institution_id:str,department_id:str|None=Query(None),db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(require_super_admin)):
    crud.get_institution(db,institution_id)
    query=db.query(models.Section).join(models.Department).join(models.Branch).filter(models.Branch.institution_id==institution_id)
    if department_id: query=query.filter(models.Section.department_id==department_id)
    return query.order_by(models.Section.name.asc()).all()

@app.post("/institutions/me/branches",response_model=dict,status_code=201,tags=["5. User Management"])
def create_branch(d:schemas.BranchCreate,db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    try:
        iid=inst_for(db,u,("institution_admin",)); sub=crud.require_institution_access(db,iid)
        if db.query(models.Branch).filter_by(institution_id=iid).count()>=sub.bundle.max_branches: raise HTTPException(403,"Branch limit reached")
        b=crud.create_branch(db,iid,d.name,d.code); inst_obj=crud.get_institution(db,iid); inst_gid=keycloak_admin.create_group(inst_obj.code,keycloak_admin.create_group("Institutions")); keycloak_admin.create_group(d.code,inst_gid); return {"id":b.id,"institution_id":iid,"name":b.name,"code":b.code}
    except crud.DomainError as e:handle(e)
@app.post("/institutions/me/departments/{branch_id}",response_model=dict,status_code=201,tags=["5. User Management"])
def create_department(branch_id:str,d:schemas.DepartmentCreate,db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    try:
        iid=inst_for(db,u,("institution_admin",)); b=db.query(models.Branch).filter_by(id=branch_id,institution_id=iid).first()
        if not b: raise HTTPException(404,"Branch not found")
        dep=crud.create_department(db,branch_id,d.name,d.code); inst_obj=crud.get_institution(db,iid); root=keycloak_admin.create_group("Institutions"); inst_gid=keycloak_admin.create_group(inst_obj.code,root); branch_gid=keycloak_admin.create_group(b.code,inst_gid); keycloak_admin.create_group(d.code,branch_gid); return {"id":dep.id,"branch_id":branch_id,"name":dep.name,"code":dep.code}
    except crud.DomainError as e:handle(e)
@app.post("/institutions/me/sections/{department_id}",response_model=dict,status_code=201,tags=["5. User Management"])
def create_section(department_id:str,d:schemas.SectionCreate,db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    try:
        iid=inst_for(db,u,("institution_admin",)); dep=db.query(models.Department).filter_by(id=department_id).first(); branch=db.query(models.Branch).filter_by(id=dep.branch_id,institution_id=iid).first() if dep else None
        if not branch: raise HTTPException(404,"Department not found")
        sec=crud.create_section(db,department_id,d.name,d.academic_year); b=db.query(models.Branch).filter_by(id=dep.branch_id).first(); root=keycloak_admin.create_group("Institutions"); inst_obj=crud.get_institution(db,iid); inst_gid=keycloak_admin.create_group(inst_obj.code,root); branch_gid=keycloak_admin.create_group(b.code,inst_gid); dept_gid=keycloak_admin.create_group(dep.code,branch_gid); keycloak_admin.create_group(d.name,dept_gid); return {"id":sec.id,"department_id":department_id,"name":sec.name,"academic_year":sec.academic_year}
    except crud.DomainError as e:handle(e)

@app.post("/institutions/me/custom-plan-requests",response_model=dict,status_code=201,tags=["2. Plan Selection"])
def custom_plan_request(d:schemas.CustomPlanRequestCreate,db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    iid=inst_for(db,u,("institution_admin",)); x=models.CustomPlanRequest(institution_id=iid,requested_by=u.sub,**d.model_dump()); x.quoted_price=crud.calculate_custom_plan_price(x); x.status=models.QuotationStatus.quoted; db.add(x); db.commit(); db.refresh(x)
    return {"id":x.id,"quoted_price":x.quoted_price,"currency":x.currency,"status":x.status.value,"message":"Custom plan price calculated from the requested limits"}

@app.post("/admin/custom-plan-requests/{request_id}/quote",response_model=schemas.CustomPlanQuoteOut,tags=["2. Plan Selection"],summary="Calculate a custom-plan quote",description="Automatically calculates the fixed custom-plan price from the saved requirements. The price is never accepted from the request body.")
def quote_custom_plan(request_id:str,db:Session=Depends(get_db),u=Depends(require_super_admin)):
    x=db.query(models.CustomPlanRequest).filter_by(id=request_id).first()
    if not x: raise HTTPException(404,"Custom plan request not found")
    x.quoted_price=crud.calculate_custom_plan_price(x); x.status=models.QuotationStatus.quoted; x.updated_at=models.utcnow(); db.commit(); return {"id":x.id,"quoted_price":x.quoted_price,"currency":x.currency,"status":x.status.value}

@app.post("/institutions/me/custom-plan-requests/{request_id}/accept",response_model=dict,tags=["2. Plan Selection"])
def accept_custom_quote(request_id:str,db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    iid=inst_for(db,u,("institution_admin",)); x=db.query(models.CustomPlanRequest).filter_by(id=request_id,institution_id=iid).first()
    if not x: raise HTTPException(404,"Custom plan request not found")
    if x.status != models.QuotationStatus.quoted or x.quoted_price is None: raise HTTPException(409,"A quoted custom plan is required before acceptance")
    x.status=models.QuotationStatus.accepted; x.updated_at=models.utcnow(); db.commit(); return {"id":x.id,"status":x.status.value,"amount":x.quoted_price,"message":"Quotation accepted. Payment is required before activation."}

@app.post("/admin/custom-plan-requests/{request_id}/activate",response_model=schemas.InstitutionSubscriptionOut,tags=["4. Institution Access & Subscription"])
def activate_custom_plan(request_id:str,db:Session=Depends(get_db),u=Depends(require_super_admin)):
    x=db.query(models.CustomPlanRequest).filter_by(id=request_id).first()
    if not x: raise HTTPException(404,"Custom plan request not found")
    if x.status != models.QuotationStatus.accepted or x.quoted_price is None: raise HTTPException(409,"Custom quotation must be accepted before activation")
    institution=crud.get_institution(db,x.institution_id)
    bundle=models.PlanBundle(name=f"{institution.code}-CUSTOM-{x.id[:8]}",price=x.quoted_price,duration_days=x.duration_days,max_branches=x.branches,max_admins=x.admins,max_students=x.students,max_professors=x.professors,course_quota=x.courses,concurrent_sessions=x.concurrent_sessions,description=x.additional_features,is_active=True)
    db.add(bundle); db.flush()
    custom_plan=db.query(models.Plan).filter_by(name="Custom").first()
    if custom_plan: db.add(models.BundlePlanItem(bundle_id=bundle.id,plan_id=custom_plan.id,quantity=1))
    sub=models.InstitutionSubscription.new(x.institution_id,bundle,x.requested_by,activate=True); db.add(sub)
    x.status=models.QuotationStatus.paid; x.updated_at=models.utcnow(); institution.status=models.InstitutionStatus.active; db.commit(); db.refresh(sub)
    return sub

# Super Admin can operate on a tenant without bypassing tenant identity checks.
@app.post("/admin/institutions/{institution_id}/courses",response_model=schemas.CourseOut,status_code=201,tags=["6. Courses"])
def super_create_course(institution_id:str,d:schemas.CourseCreate,db:Session=Depends(get_db),u=Depends(require_super_admin)):
    try:return crud.create_course(db,institution_id,d,u.sub)
    except crud.DomainError as e:handle(e)

# ---------- Courses and assignment ----------
@app.post("/institutions/me/courses",response_model=schemas.CourseOut,status_code=201,tags=["6. Courses"])
def create_course(d:schemas.CourseCreate,db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    try:
        iid=inst_for(db,u,("institution_admin",)); return crud.create_course(db,iid,d,u.sub)
    except crud.DomainError as e:handle(e)
@app.get("/courses",response_model=list[schemas.CourseOut],tags=["6. Courses"])
def get_courses(db:Session=Depends(get_db),u=Depends(get_current_user)):
    iid=inst_for(db,u); return crud.list_courses(db,iid)
@app.post("/courses/{course_id}/assign",response_model=schemas.Message,tags=["6. Courses"],summary="Assign Course to Institution Member",description="Assign a course to an active student or professor in the same institution. First call GET /institutions/me/members and copy the target member's user_id; do not use a deleted or Keycloak-only user ID.")
def assign_course(course_id:str,d:schemas.CourseAssignmentRequest,db:Session=Depends(get_db),u=Depends(require_institution_admin)):
    try:return crud.assign_course(db,inst_for(db,u,("institution_admin",)),course_id,d.user_id,u.sub,"institution_admin")
    except crud.DomainError as e:handle(e)
@app.post("/professor/courses/{course_id}/assign",response_model=schemas.Message,tags=["6. Courses"],summary="Assign Course Within Professor Scope",description="Assign a course to an active student or professor in the same institution and within the professor's assigned scope. Get valid IDs from GET /institutions/me/members.")
def professor_assign(course_id:str,d:schemas.CourseAssignmentRequest,db:Session=Depends(get_db),u=Depends(require_professor)):
    try:return crud.assign_course(db,inst_for(db,u,("professor",)),course_id,d.user_id,u.sub,"professor")
    except crud.DomainError as e:handle(e)
@app.get("/professor/courses",response_model=list[schemas.CourseOut],tags=["6. Courses"])
def professor_courses(db:Session=Depends(get_db),u=Depends(require_professor)):
    iid=inst_for(db,u,("professor",))
    return crud.list_courses(db,iid)

# ---------- Student access ----------
@app.post("/courses/{course_id}/access",response_model=schemas.WatchSessionOut,status_code=201,tags=["7. Learning"])
def access(course_id:str,d:schemas.CourseAccessRequest,db:Session=Depends(get_db),u=Depends(require_student)):
    try:
        iid=inst_for(db,u,("student",))
        if not any(x.get("id")==d.keycloak_session_id for x in keycloak_admin.list_user_sessions(u.sub)):
            raise HTTPException(403,"Keycloak session is not active for this user")
        return crud.start_course_access(db,u.sub,iid,course_id,d.keycloak_session_id)
    except crud.DomainError as e:handle(e)
@app.post("/watch-sessions/{id}/end",response_model=schemas.WatchSessionOut,tags=["7. Learning"])
def end_watch(id:str,db:Session=Depends(get_db),u=Depends(require_student)):
    try:return crud.end_course_access(db,u.sub,inst_for(db,u,("student",)),id)
    except crud.DomainError as e:handle(e)
@app.get("/history",response_model=list[schemas.WatchHistoryEntry],tags=["7. Learning"])
def history(db:Session=Depends(get_db),u=Depends(require_student)):
    out=[]
    for x in crud.get_watch_history(db,u.sub,inst_for(db,u,("student",))): out.append(schemas.WatchHistoryEntry(id=x.id,course_id=x.course_id,course_title=x.course.title,started_at=x.started_at,ended_at=x.ended_at))
    return out
@app.get("/student/courses",response_model=list[schemas.CourseOut],tags=["7. Learning"])
def student_courses(db:Session=Depends(get_db),u=Depends(require_student)):
    iid=inst_for(db,u,("student",))
    ids=[x.course_id for x in db.query(models.CourseAssignment).filter_by(institution_id=iid,user_id=u.sub).all()]
    if not ids:return []
    return db.query(models.Course).filter(models.Course.institution_id==iid,models.Course.id.in_(ids)).all()

# ---------- Keycloak authentication session management ----------
def keycloak_session_out(session,user_id):
    def dt(ms):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ms/1000,timezone.utc) if ms else None
    return schemas.SessionOut(id=session.get("id"),user_id=user_id,client_id=session.get("clientId"),ip_address=session.get("ipAddress"),started=dt(session.get("start")),last_access=dt(session.get("lastAccess")),state=session.get("state","active"))

@app.post("/sessions/login",response_model=schemas.SessionOut,status_code=200,tags=["8. Sessions"])
def session_login(d:schemas.SessionLoginRequest,db:Session=Depends(get_db),u=Depends(require_student)):
    try:
        iid=inst_for(db,u,("student",)); sub=crud.require_institution_access(db,iid); _,limit=crud.get_plan_limits(sub)
        sid=getattr(u,"session_state",None) or f"dev-{u.sub}"
        sessions=keycloak_admin.list_user_sessions(u.sub)
        if sid not in {x.get("id") for x in sessions} and len(sessions)>=limit:
            raise HTTPException(409,f"Concurrent session limit reached ({limit}). Sign out another Keycloak session first.")
        if settings.auth_dev_mode and sid not in {x.get("id") for x in sessions}: keycloak_admin.register_dev_session(u.sub,sid)
        sessions=keycloak_admin.list_user_sessions(u.sub); current=next((x for x in sessions if x.get("id")==sid),None)
        if not current: raise HTTPException(401,"Current Keycloak session was not found")
        return keycloak_session_out(current,u.sub)
    except crud.DomainError as e:handle(e)
    except KeycloakError as e:raise HTTPException(502,str(e))

@app.get("/sessions",response_model=list[schemas.SessionOut],tags=["8. Sessions"])
def sessions(db:Session=Depends(get_db),u=Depends(require_student)):
    try:return [keycloak_session_out(x,u.sub) for x in keycloak_admin.list_user_sessions(u.sub)]
    except KeycloakError as e:raise HTTPException(502,str(e))

@app.delete("/sessions/{id}",response_model=schemas.Message,tags=["8. Sessions"])
def remove_session(id:str,db:Session=Depends(get_db),u=Depends(require_student)):
    try:
        if not any(x.get("id")==id for x in keycloak_admin.list_user_sessions(u.sub)): raise HTTPException(404,"Keycloak session not found")
        keycloak_admin.logout_user_session(u.sub,id); return {"detail":"Keycloak session terminated"}
    except KeycloakError as e:raise HTTPException(502,str(e))

# ---------- Compatibility individual subscriptions ----------
@app.post("/subscriptions/subscribe",response_model=schemas.SubscriptionOut,status_code=201,tags=["9. Subscriptions"])
def subscribe(d:schemas.SubscribeRequest,db:Session=Depends(get_db),u=Depends(get_current_user)):
    try:return crud.subscribe_user(db,u.sub,d.plan_id)
    except crud.DomainError as e:handle(e)
@app.get("/subscriptions/me",response_model=schemas.SubscriptionStatusOut,tags=["9. Subscriptions"])
def my_sub(db:Session=Depends(get_db),u=Depends(get_current_user)):
    s=crud.get_active_subscription(db,u.sub)
    if not s:return schemas.SubscriptionStatusOut(subscribed=False)
    return schemas.SubscriptionStatusOut(subscribed=True,subscription=s,is_expired=not s.is_currently_valid())

@app.get("/admin/subscriptions/{user_id}",response_model=schemas.SubscriptionStatusOut,tags=["9. Subscriptions"])
def admin_user_subscription(user_id:str,db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(require_super_admin)):
    s=crud.get_active_subscription(db,user_id)
    if not s:return schemas.SubscriptionStatusOut(subscribed=False)
    return schemas.SubscriptionStatusOut(subscribed=True,subscription=s,is_expired=not s.is_currently_valid())

@app.post("/admin/subscriptions/{user_id}",response_model=schemas.SubscriptionOut,status_code=201,tags=["9. Subscriptions"])
def admin_user_subscribe(user_id:str,d:schemas.SubscribeRequest,db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(require_super_admin)):
    try:return crud.admin_assign_subscription(db,user_id,d.plan_id)
    except crud.DomainError as e:handle(e)

# ---------- Super Admin monitoring ----------
@app.get("/admin/users",tags=["10. Super Admin Monitoring"])
def admin_users(search:str|None=Query(None),_:schemas.CurrentUser=Depends(require_super_admin)):
    try:return keycloak_admin.list_users(search)
    except KeycloakError as e:raise HTTPException(502,str(e))
@app.delete("/admin/users/{id}",response_model=schemas.Message,tags=["10. Super Admin Monitoring"])
def admin_delete_user(id:str,_:schemas.CurrentUser=Depends(require_super_admin)):
    try:keycloak_admin.delete_user(id);return {"detail":"User deleted"}
    except KeycloakError as e:raise HTTPException(502,str(e))
@app.get("/admin/sessions/{user_id}",response_model=list[schemas.SessionOut],tags=["10. Super Admin Monitoring"])
def admin_sessions(user_id:str,db:Session=Depends(get_db),_:schemas.CurrentUser=Depends(require_super_admin)):
    try:return [keycloak_session_out(x,user_id) for x in keycloak_admin.list_user_sessions(user_id)]
    except KeycloakError as e:raise HTTPException(502,str(e))

@app.get("/health",tags=["System"])
def health(): return {"status":"ok","service":"StudyInstitution","version":app.version}
