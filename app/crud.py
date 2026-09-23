"""Tenant-aware business rules for StudyInstitution.

Important workflow rule:
Plan is the catalog definition. The application NEVER asks an operator to
manually enter member/session/course limits when selecting a plan. On plan
selection, a bundle snapshot is generated automatically from the plan and
attached to the institution subscription.
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app import models

class DomainError(Exception):
    def __init__(self,message,status_code=400): super().__init__(message); self.message=message; self.status_code=status_code

def get_plan(db,id):
    x=db.query(models.Plan).filter(models.Plan.id==id,models.Plan.is_active.is_(True)).first()
    if not x: raise DomainError("Plan not found or inactive",404)
    return x

def create_plan(db,data):
    if db.query(models.Plan).filter(models.Plan.name==data.name).first(): raise DomainError("Plan already exists",409)
    x=models.Plan(**data.model_dump()); db.add(x); db.commit(); db.refresh(x); return x

def list_plans(db): return db.query(models.Plan).filter(models.Plan.is_active.is_(True)).order_by(models.Plan.price.asc()).all()

def calculate_custom_plan_price(request):
    """Calculate an annualized custom-plan quote from the requested limits."""
    monthly_price = (
        request.branches * 1000
        + request.admins * 500
        + request.professors * 100
        + request.students * 10
        + request.courses * 25
        + request.concurrent_sessions * 1000
    )
    feature_count = len([feature for feature in request.additional_features.split(";") if feature.strip()])
    monthly_price += feature_count * 2500
    return round(monthly_price * request.duration_days / 30, 2)

def update_plan(db,id,data):
    x=db.query(models.Plan).filter(models.Plan.id==id).first()
    if not x: raise DomainError("Plan not found",404)
    changes=data.model_dump(exclude_unset=True)
    if "name" in changes and db.query(models.Plan).filter(models.Plan.name==changes["name"],models.Plan.id!=id).first(): raise DomainError("Plan already exists",409)
    for k,v in changes.items(): setattr(x,k,v)
    try: db.commit()
    except IntegrityError as e: db.rollback(); raise DomainError("Plan could not be updated",409) from e
    db.refresh(x); return x

def delete_plan(db,id):
    x=db.query(models.Plan).filter_by(id=id).first()
    if not x: raise DomainError("Plan not found",404)
    if db.query(models.BundlePlanItem).filter_by(plan_id=id).first() or db.query(models.Subscription).filter_by(plan_id=id).first():
        raise DomainError("Plan is in use and cannot be deleted; deactivate it instead",409)
    db.delete(x); db.commit()

def create_institution(db,data):
    if db.query(models.Institution).filter((models.Institution.name==data.name)|(models.Institution.code==data.code)).first(): raise DomainError("Institution name or code already exists",409)
    x=models.Institution(**data.model_dump()); db.add(x); db.commit(); db.refresh(x); return x

def get_institution(db,id):
    x=db.query(models.Institution).filter(models.Institution.id==id).first()
    if not x: raise DomainError("Institution not found",404)
    return x

def membership(db,inst,user_id,roles=None):
    x=db.query(models.InstitutionMembership).filter_by(institution_id=inst,user_id=user_id,is_active=True).first()
    if not x:
        raise DomainError("Target user is not an active member of this institution",403)
    if roles and x.role.value not in roles:
        raise DomainError(f"Target user must have one of these roles: {', '.join(roles)}",403)
    return x

def institution_for_user(db,user,allowed=("institution_admin","professor","student")):
    if user.has_role("super_admin"): return None
    rows=(db.query(models.InstitutionMembership).filter(models.InstitutionMembership.user_id==user.sub,models.InstitutionMembership.is_active.is_(True),models.InstitutionMembership.role.in_(list(allowed))).all())
    if not rows: raise DomainError("No active institution membership",403)
    if len({x.institution_id for x in rows})>1: raise DomainError("User belongs to multiple institutions; tenant context is required",409)
    return rows[0].institution_id

def add_membership(db,inst,user_id,role,display_name="",branch_id=None,department_id=None,section_id=None):
    get_institution(db,inst)
    try: r=models.MembershipRole(role)
    except ValueError: raise DomainError("Invalid membership role",422)
    old=db.query(models.InstitutionMembership).filter_by(institution_id=inst,user_id=user_id).first()
    if old:
        old.role=r; old.display_name=display_name; old.branch_id=branch_id; old.department_id=department_id; old.section_id=section_id; old.is_active=True; x=old
    else:
        x=models.InstitutionMembership(institution_id=inst,user_id=user_id,role=r,display_name=display_name,branch_id=branch_id,department_id=department_id,section_id=section_id); db.add(x)
    try: db.commit()
    except IntegrityError as e: db.rollback(); raise DomainError("Membership already exists",409) from e
    db.refresh(x); return x

def active_institution_subscription(db,inst):
    x=db.query(models.InstitutionSubscription).filter_by(institution_id=inst,is_active=True).order_by(models.InstitutionSubscription.start_date.desc()).first()
    if not x or not x.is_currently_valid(): return None
    return x

def latest_institution_subscription(db,inst):
    return (db.query(models.InstitutionSubscription)
            .filter_by(institution_id=inst)
            .order_by(models.InstitutionSubscription.start_date.desc())
            .first())

def require_institution_access(db,inst):
    i=get_institution(db,inst)
    if i.status != models.InstitutionStatus.active: raise DomainError("Institution is not active",403)
    s=active_institution_subscription(db,inst)
    if not s: raise DomainError("Institution subscription is missing or expired",402)
    return s

def _generated_bundle_name(db,institution,plan):
    base=f"{institution.code}-{plan.name}-bundle"
    name=base; n=2
    while db.query(models.PlanBundle).filter_by(name=name).first():
        name=f"{base}-{n}"; n+=1
    return name

def generate_bundle_from_plan(db,institution,plan):
    """Create the bundle automatically from the selected plan's limits."""
    bundle=models.PlanBundle(
        name=_generated_bundle_name(db,institution,plan),
        price=plan.price,
        duration_days=plan.duration_days,
        max_branches=plan.max_branches,
        max_admins=plan.max_admins,
        max_students=plan.max_students,
        max_professors=plan.max_professors,
        course_quota=plan.course_quota,
        concurrent_sessions=plan.concurrent_sessions,
        description=plan.features,
        is_active=True,
    )
    db.add(bundle); db.flush()
    db.add(models.BundlePlanItem(bundle_id=bundle.id,plan_id=plan.id,quantity=1))
    return bundle

def select_plan_for_institution(db,inst,plan_id,buyer):
    """Select a catalog plan and automatically create its institution bundle/subscription."""
    institution=get_institution(db,inst); plan=get_plan(db,plan_id)
    if plan.plan_type == models.PlanType.custom: raise DomainError("Custom plans require a requirements request and quotation before activation",400)
    current=latest_institution_subscription(db,inst)
    if current and current.status in (models.SubscriptionStatus.pending, models.SubscriptionStatus.active):
        if current.status == models.SubscriptionStatus.pending:
            raise DomainError("Institution already has a plan awaiting payment or activation.",409)
        raise DomainError("Institution already has an active plan. Wait for expiry before selecting another plan.",409)
    bundle=generate_bundle_from_plan(db,institution,plan)
    subscription=models.InstitutionSubscription.new(inst,bundle,buyer,activate=(plan.plan_type == models.PlanType.free))
    if plan.plan_type != models.PlanType.free:
        subscription.status=models.SubscriptionStatus.pending; subscription.is_active=False
    db.add(subscription)
    institution.status=models.InstitutionStatus.active if plan.plan_type == models.PlanType.free else models.InstitutionStatus.pending
    try: db.commit()
    except IntegrityError as e: db.rollback(); raise DomainError("Plan selection could not be completed",409) from e
    db.refresh(subscription); db.refresh(bundle)
    return institution,plan,bundle,subscription

def activate_institution_subscription(db,subscription_id,provider_reference=None):
    sub=db.query(models.InstitutionSubscription).filter_by(id=subscription_id).first()
    if not sub: raise DomainError("Institution subscription not found",404)
    sub.status=models.SubscriptionStatus.active; sub.is_active=True
    institution=db.query(models.Institution).filter_by(id=sub.institution_id).first(); institution.status=models.InstitutionStatus.active
    payment=db.query(models.Payment).filter_by(subscription_id=sub.id).order_by(models.Payment.created_at.desc()).first()
    if payment:
        payment.status=models.PaymentStatus.success; payment.provider_reference=provider_reference or payment.provider_reference
    db.commit(); db.refresh(sub); return sub

def bundle_plan_id(bundle):
    item=bundle.items[0] if bundle.items else None
    return item.plan_id if item else None

def list_generated_bundles_for_institution(db,inst):
    return (db.query(models.PlanBundle).join(models.InstitutionSubscription,models.InstitutionSubscription.bundle_id==models.PlanBundle.id)
            .filter(models.InstitutionSubscription.institution_id==inst).order_by(models.InstitutionSubscription.start_date.desc()).all())

def get_bundle(db,id):
    x=db.query(models.PlanBundle).filter(models.PlanBundle.id==id,models.PlanBundle.is_active.is_(True)).first()
    if not x: raise DomainError("Bundle not found",404)
    return x

def validate_member_scope(db, creator_user_id, target_branch=None, target_department=None, target_section=None):
    creator=db.query(models.InstitutionMembership).filter_by(user_id=creator_user_id,is_active=True).first()
    if not creator: raise DomainError("Creator has no active institution membership",403)
    if creator.role != models.MembershipRole.professor: return
    if target_branch and creator.branch_id != target_branch: raise DomainError("Professor can only manage students in their assigned branch",403)
    if target_department and creator.department_id != target_department: raise DomainError("Professor can only manage students in their assigned department",403)
    if target_section and creator.section_id != target_section: raise DomainError("Professor can only manage students in their assigned section",403)

def create_branch(db,inst,name,code):
    require_institution_access(db,inst)
    x=models.Branch(institution_id=inst,name=name,code=code); db.add(x); db.commit(); db.refresh(x); return x
def create_department(db,branch_id,name,code):
    b=db.query(models.Branch).filter_by(id=branch_id).first()
    if not b: raise DomainError("Branch not found",404)
    require_institution_access(db,b.institution_id); x=models.Department(branch_id=branch_id,name=name,code=code); db.add(x); db.commit(); db.refresh(x); return x
def create_section(db,department_id,name,academic_year=None):
    d=db.query(models.Department).filter_by(id=department_id).first()
    if not d: raise DomainError("Department not found",404)
    b=db.query(models.Branch).filter_by(id=d.branch_id).first(); require_institution_access(db,b.institution_id); x=models.Section(department_id=department_id,name=name,academic_year=academic_year); db.add(x); db.commit(); db.refresh(x); return x

def count_role(db,inst,role): return db.query(models.InstitutionMembership).filter_by(institution_id=inst,role=role,is_active=True).count()

def create_course(db,inst,data,creator):
    require_institution_access(db,inst); x=models.Course(institution_id=inst,**data.model_dump()); db.add(x); db.commit(); db.refresh(x); return x
def list_courses(db,inst): return db.query(models.Course).filter(models.Course.institution_id==inst).all()
def get_course(db,id,inst=None):
    q=db.query(models.Course).filter(models.Course.id==id)
    if inst: q=q.filter(models.Course.institution_id==inst)
    x=q.first()
    if not x: raise DomainError("Course not found",404)
    return x

def assign_course(db,inst,course_id,user_id,assigned_by,actor_role=None):
    c=get_course(db,course_id,inst); target=membership(db,inst,user_id,["student","professor"])
    if actor_role=="professor": validate_member_scope(db,assigned_by,target.branch_id,target.department_id,target.section_id)
    if not db.query(models.CourseAssignment).filter_by(course_id=course_id,user_id=user_id).first():
        db.add(models.CourseAssignment(institution_id=inst,course_id=course_id,user_id=user_id,assigned_by=assigned_by)); db.commit()
    return {"detail":"Course assigned"}

def get_plan_limits(sub): return sub.bundle.course_quota,sub.bundle.concurrent_sessions
def count_courses_used(db,user,inst): return db.query(models.CourseAccess).filter_by(user_id=user,institution_id=inst).count()
def has_course_access(db,user,course,inst): return db.query(models.CourseAccess).filter_by(user_id=user,course_id=course,institution_id=inst).first() is not None
def count_active_sessions(keycloak_admin, user_id):
    try: return keycloak_admin.count_user_sessions(user_id)
    except Exception as exc: raise DomainError(f"Unable to read Keycloak sessions: {exc}",502)

def list_active_sessions(keycloak_admin, user_id):
    try: return keycloak_admin.list_user_sessions(user_id)
    except Exception as exc: raise DomainError(f"Unable to read Keycloak sessions: {exc}",502)

def start_course_access(db,user,inst,course_id,keycloak_session_id):
    sub=require_institution_access(db,inst); quota,_=get_plan_limits(sub); get_course(db,course_id,inst); membership(db,inst,user,["student"])
    assignment=db.query(models.CourseAssignment).filter_by(course_id=course_id,user_id=user,institution_id=inst).first()
    if not assignment: raise DomainError("Course has not been assigned to this student",403)
    if not has_course_access(db,user,course_id,inst):
        if count_courses_used(db,user,inst)>=quota: raise DomainError(f"Course quota exceeded (quota {quota})",403)
        db.add(models.CourseAccess(user_id=user,course_id=course_id,institution_id=inst))
    x=models.WatchSession(user_id=user,institution_id=inst,course_id=course_id,keycloak_session_id=keycloak_session_id); db.add(x); db.commit(); db.refresh(x); return x

def end_course_access(db,user,inst,wid):
    x=db.query(models.WatchSession).filter_by(id=wid,user_id=user,institution_id=inst).first()
    if not x: raise DomainError("Watch session not found",404)
    if x.ended_at: raise DomainError("Watch session already ended",409)
    x.ended_at=models.utcnow(); db.commit(); db.refresh(x); return x

def get_watch_history(db,user,inst): return db.query(models.WatchSession).filter_by(user_id=user,institution_id=inst).order_by(models.WatchSession.started_at.desc()).all()

# Backward-compatible individual APIs.
def get_active_subscription(db,user): return db.query(models.Subscription).filter_by(user_id=user,is_active=True).order_by(models.Subscription.start_date.desc()).first()
def subscribe_user(db,user,plan_id):
    p=get_plan(db,plan_id); cur=get_active_subscription(db,user)
    if cur and cur.is_currently_valid(): raise DomainError("Active subscription already exists",409)
    x=models.Subscription.new_for_plan(user,p); db.add(x); db.commit(); db.refresh(x); return x
def admin_assign_subscription(db,user,plan_id): return subscribe_user(db,user,plan_id)
