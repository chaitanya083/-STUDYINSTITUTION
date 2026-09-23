"""Business models for StudyInstitution. Keycloak owns identity/IAM sessions; SQLite owns business data."""
import enum, uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Enum, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base

def gen_uuid(): return str(uuid.uuid4())
def utcnow(): return datetime.now(timezone.utc).replace(tzinfo=None)

class InstitutionStatus(str, enum.Enum): pending="pending"; active="active"; suspended="suspended"
class MembershipRole(str, enum.Enum): institution_admin="institution_admin"; professor="professor"; student="student"
class PlanType(str, enum.Enum): free="free"; basic="basic"; premium="premium"; custom="custom"
class SubscriptionStatus(str, enum.Enum): pending="pending"; active="active"; expired="expired"; suspended="suspended"; cancelled="cancelled"
class PaymentStatus(str, enum.Enum): pending="pending"; success="success"; failed="failed"; refunded="refunded"; cancelled="cancelled"
class QuotationStatus(str, enum.Enum): requested="requested"; under_review="under_review"; quoted="quoted"; revision_requested="revision_requested"; accepted="accepted"; rejected="rejected"; paid="paid"; expired="expired"

class Plan(Base):
    __tablename__="plans"
    id=Column(String,primary_key=True,default=gen_uuid); name=Column(String,unique=True,nullable=False,index=True)
    plan_type=Column(Enum(PlanType),nullable=False,default=PlanType.basic,index=True); price=Column(Float,nullable=False,default=0)
    max_branches=Column(Integer,nullable=False,default=1); max_admins=Column(Integer,nullable=False,default=1)
    max_students=Column(Integer,nullable=False,default=10); max_professors=Column(Integer,nullable=False,default=2)
    course_quota=Column(Integer,nullable=False,default=10); concurrent_sessions=Column(Integer,nullable=False,default=2)
    duration_days=Column(Integer,nullable=False,default=30); features=Column(String,nullable=False,default=""); is_active=Column(Boolean,nullable=False,default=True)
    subscriptions=relationship("Subscription",back_populates="plan"); bundle_items=relationship("BundlePlanItem",back_populates="plan")

class PlanBundle(Base):
    __tablename__="plan_bundles"
    id=Column(String,primary_key=True,default=gen_uuid); name=Column(String,unique=True,nullable=False); price=Column(Float,nullable=False,default=0)
    duration_days=Column(Integer,nullable=False); max_branches=Column(Integer,nullable=False,default=1); max_admins=Column(Integer,nullable=False,default=1)
    max_students=Column(Integer,nullable=False,default=100); max_professors=Column(Integer,nullable=False,default=10); course_quota=Column(Integer,nullable=False,default=10)
    concurrent_sessions=Column(Integer,nullable=False,default=2); description=Column(String,nullable=False,default=""); is_active=Column(Boolean,default=True)
    items=relationship("BundlePlanItem",back_populates="bundle",cascade="all, delete-orphan"); subscriptions=relationship("InstitutionSubscription",back_populates="bundle")

class BundlePlanItem(Base):
    __tablename__="bundle_plan_items"
    id=Column(String,primary_key=True,default=gen_uuid); bundle_id=Column(String,ForeignKey("plan_bundles.id"),nullable=False); plan_id=Column(String,ForeignKey("plans.id"),nullable=False); quantity=Column(Integer,nullable=False,default=1)
    __table_args__=(UniqueConstraint("bundle_id","plan_id",name="uq_bundle_plan"),)
    bundle=relationship("PlanBundle",back_populates="items"); plan=relationship("Plan",back_populates="bundle_items")

class Institution(Base):
    __tablename__="institutions"
    id=Column(String,primary_key=True,default=gen_uuid); name=Column(String,unique=True,nullable=False,index=True); code=Column(String,unique=True,nullable=False,index=True)
    status=Column(Enum(InstitutionStatus),default=InstitutionStatus.pending,nullable=False); created_at=Column(DateTime,default=utcnow)
    memberships=relationship("InstitutionMembership",back_populates="institution",cascade="all, delete-orphan"); subscriptions=relationship("InstitutionSubscription",back_populates="institution",cascade="all, delete-orphan")
    courses=relationship("Course",back_populates="institution",cascade="all, delete-orphan"); branches=relationship("Branch",back_populates="institution",cascade="all, delete-orphan")

class Branch(Base):
    __tablename__="branches"
    id=Column(String,primary_key=True,default=gen_uuid); institution_id=Column(String,ForeignKey("institutions.id"),nullable=False,index=True); name=Column(String,nullable=False); code=Column(String,nullable=False)
    __table_args__=(UniqueConstraint("institution_id","code",name="uq_branch_code"),); institution=relationship("Institution",back_populates="branches"); departments=relationship("Department",back_populates="branch",cascade="all, delete-orphan")
class Department(Base):
    __tablename__="departments"
    id=Column(String,primary_key=True,default=gen_uuid); branch_id=Column(String,ForeignKey("branches.id"),nullable=False,index=True); name=Column(String,nullable=False); code=Column(String,nullable=False)
    __table_args__=(UniqueConstraint("branch_id","code",name="uq_department_code"),); branch=relationship("Branch",back_populates="departments"); sections=relationship("Section",back_populates="department",cascade="all, delete-orphan")
class Section(Base):
    __tablename__="sections"
    id=Column(String,primary_key=True,default=gen_uuid); department_id=Column(String,ForeignKey("departments.id"),nullable=False,index=True); name=Column(String,nullable=False); academic_year=Column(String,nullable=True)
    __table_args__=(UniqueConstraint("department_id","name",name="uq_section_name"),); department=relationship("Department",back_populates="sections")

class InstitutionMembership(Base):
    __tablename__="institution_memberships"
    id=Column(String,primary_key=True,default=gen_uuid); institution_id=Column(String,ForeignKey("institutions.id"),nullable=False,index=True); user_id=Column(String,nullable=False,index=True)
    role=Column(Enum(MembershipRole),nullable=False); display_name=Column(String,nullable=False,default=""); branch_id=Column(String,ForeignKey("branches.id"),nullable=True,index=True); department_id=Column(String,ForeignKey("departments.id"),nullable=True,index=True); section_id=Column(String,ForeignKey("sections.id"),nullable=True,index=True); is_active=Column(Boolean,default=True,nullable=False)
    __table_args__=(UniqueConstraint("institution_id","user_id",name="uq_institution_user"),); institution=relationship("Institution",back_populates="memberships")

class InstitutionSubscription(Base):
    __tablename__="institution_subscriptions"
    id=Column(String,primary_key=True,default=gen_uuid); institution_id=Column(String,ForeignKey("institutions.id"),nullable=False,index=True); bundle_id=Column(String,ForeignKey("plan_bundles.id"),nullable=False); purchased_by=Column(String,nullable=False)
    start_date=Column(DateTime,default=utcnow); end_date=Column(DateTime,nullable=False); status=Column(Enum(SubscriptionStatus),default=SubscriptionStatus.pending,nullable=False); is_active=Column(Boolean,default=True)
    institution=relationship("Institution",back_populates="subscriptions"); bundle=relationship("PlanBundle",back_populates="subscriptions")
    def is_currently_valid(self): return self.status==SubscriptionStatus.active and bool(self.is_active) and utcnow() <= self.end_date
    @staticmethod
    def new(institution_id,bundle,purchased_by,activate=True):
        s=utcnow(); return InstitutionSubscription(institution_id=institution_id,bundle_id=bundle.id,purchased_by=purchased_by,start_date=s,end_date=s+timedelta(days=bundle.duration_days),status=SubscriptionStatus.active if activate else SubscriptionStatus.pending,is_active=activate)

class Subscription(Base):
    __tablename__="subscriptions"
    id=Column(String,primary_key=True,default=gen_uuid); user_id=Column(String,nullable=False,index=True); plan_id=Column(String,ForeignKey("plans.id"),nullable=False); start_date=Column(DateTime,default=utcnow); end_date=Column(DateTime,nullable=False); is_active=Column(Boolean,default=True)
    plan=relationship("Plan",back_populates="subscriptions")
    def is_currently_valid(self): return bool(self.is_active) and utcnow() <= self.end_date
    @staticmethod
    def new_for_plan(user_id,plan):
        s=utcnow(); return Subscription(user_id=user_id,plan_id=plan.id,start_date=s,end_date=s+timedelta(days=plan.duration_days),is_active=True)

class CustomPlanRequest(Base):
    __tablename__="custom_plan_requests"
    id=Column(String,primary_key=True,default=gen_uuid); institution_id=Column(String,ForeignKey("institutions.id"),nullable=False,index=True); requested_by=Column(String,nullable=False)
    branches=Column(Integer,nullable=False,default=1); admins=Column(Integer,nullable=False,default=1); professors=Column(Integer,nullable=False,default=1); students=Column(Integer,nullable=False,default=1); courses=Column(Integer,nullable=False,default=1); concurrent_sessions=Column(Integer,nullable=False,default=1)
    duration_days=Column(Integer,nullable=False,default=365); additional_features=Column(String,nullable=False,default=""); quoted_price=Column(Float,nullable=True); currency=Column(String,nullable=False,default="INR"); status=Column(Enum(QuotationStatus),default=QuotationStatus.requested,nullable=False); created_at=Column(DateTime,default=utcnow); updated_at=Column(DateTime,default=utcnow)
class Payment(Base):
    __tablename__="payments"
    id=Column(String,primary_key=True,default=gen_uuid); institution_id=Column(String,ForeignKey("institutions.id"),nullable=False,index=True); subscription_id=Column(String,ForeignKey("institution_subscriptions.id"),nullable=True); quotation_id=Column(String,ForeignKey("custom_plan_requests.id"),nullable=True); amount=Column(Float,nullable=False); currency=Column(String,nullable=False,default="INR"); status=Column(Enum(PaymentStatus),default=PaymentStatus.pending,nullable=False); provider_reference=Column(String,nullable=True); created_at=Column(DateTime,default=utcnow)
class AuditLog(Base):
    __tablename__="audit_logs"
    id=Column(String,primary_key=True,default=gen_uuid); user_id=Column(String,nullable=False,index=True); institution_id=Column(String,ForeignKey("institutions.id"),nullable=True,index=True); action=Column(String,nullable=False); resource=Column(String,nullable=False); resource_id=Column(String,nullable=True); details=Column(String,nullable=True); created_at=Column(DateTime,default=utcnow)

class Course(Base):
    __tablename__="courses"
    id=Column(String,primary_key=True,default=gen_uuid); institution_id=Column(String,ForeignKey("institutions.id"),nullable=True,index=True); title=Column(String,nullable=False,index=True); description=Column(String,nullable=True,default="")
    institution=relationship("Institution",back_populates="courses"); accesses=relationship("CourseAccess",back_populates="course")
class CourseAssignment(Base):
    __tablename__="course_assignments"
    id=Column(String,primary_key=True,default=gen_uuid); institution_id=Column(String,ForeignKey("institutions.id"),nullable=False,index=True); course_id=Column(String,ForeignKey("courses.id"),nullable=False,index=True); user_id=Column(String,nullable=False,index=True); assigned_by=Column(String,nullable=False); assigned_at=Column(DateTime,default=utcnow)
    __table_args__=(UniqueConstraint("course_id","user_id",name="uq_course_user"),)
class CourseAccess(Base):
    __tablename__="course_access"
    id=Column(String,primary_key=True,default=gen_uuid); user_id=Column(String,nullable=False,index=True); course_id=Column(String,ForeignKey("courses.id"),nullable=False); institution_id=Column(String,ForeignKey("institutions.id"),nullable=False,index=True); granted_at=Column(DateTime,default=utcnow)
    __table_args__=(UniqueConstraint("user_id","course_id",name="uq_user_course"),); course=relationship("Course",back_populates="accesses")
class WatchSession(Base):
    """Application activity only. Authentication session itself lives in Keycloak."""
    __tablename__="watch_sessions"
    id=Column(String,primary_key=True,default=gen_uuid); user_id=Column(String,nullable=False,index=True); institution_id=Column(String,ForeignKey("institutions.id"),nullable=True,index=True); course_id=Column(String,ForeignKey("courses.id"),nullable=False)
    keycloak_session_id=Column(String,nullable=False,index=True); started_at=Column(DateTime,default=utcnow); ended_at=Column(DateTime,nullable=True); course=relationship("Course")
