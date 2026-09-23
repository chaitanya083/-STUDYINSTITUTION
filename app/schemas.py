"""Pydantic API schemas."""
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator

class CurrentUser(BaseModel):
    sub:str; username:str; email:Optional[str]=None; roles:list[str]=Field(default_factory=list); session_state:Optional[str]=None
    def has_role(self,role): return role in self.roles

class DashboardSummary(BaseModel):
    institutions:int=0; active_students:int=0; professors:int=0; active_courses:int=0; active_sessions:int=0
    subscription_status:str="Unavailable"; subscription_progress:int=0

class RegistrationRequest(BaseModel):
    username:str=Field(min_length=3); email:str; password:str=Field(min_length=8); first_name:str=""; last_name:str=""
class TokenRequest(BaseModel):
    username:str; password:str=Field(min_length=1)
class TokenOut(BaseModel):
    access_token:str; token_type:str="Bearer"; expires_in:int=0; refresh_expires_in:int=0; refresh_token:Optional[str]=None; session_state:Optional[str]=None
class UserRegistrationOut(BaseModel): user_id:str; username:str; role:str

class PlanCreate(BaseModel):
    name:str; plan_type:Literal["free","basic","premium","custom"]="basic"; price:float=Field(ge=0); max_branches:int=Field(default=1,gt=0); max_admins:int=Field(default=1,gt=0); max_students:int=Field(gt=0); max_professors:int=Field(gt=0)
    course_quota:int=Field(gt=0); concurrent_sessions:int=Field(gt=0); duration_days:int=Field(gt=0); features:str=""
class PlanUpdate(BaseModel):
    name:Optional[str]=None; plan_type:Optional[Literal["free","basic","premium","custom"]]=None; price:Optional[float]=Field(default=None,ge=0); max_branches:Optional[int]=Field(default=None,gt=0); max_admins:Optional[int]=Field(default=None,gt=0); max_students:Optional[int]=Field(default=None,gt=0)
    max_professors:Optional[int]=Field(default=None,gt=0); course_quota:Optional[int]=Field(default=None,gt=0)
    concurrent_sessions:Optional[int]=Field(default=None,gt=0); duration_days:Optional[int]=Field(default=None,gt=0)
    features:Optional[str]=None; is_active:Optional[bool]=None
class PlanOut(PlanCreate):
    model_config=ConfigDict(from_attributes=True); id:str; is_active:bool=True
    @field_validator("plan_type", mode="before")
    @classmethod
    def normalize_plan_type(cls, value):
        return value.value if hasattr(value, "value") else value

class PlanSelectionRequest(BaseModel): plan_id:str
class BundleOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:str; name:str; price:float; duration_days:int; max_branches:int=1; max_admins:int=1; max_students:int; max_professors:int; course_quota:int
    concurrent_sessions:int; description:str; is_active:bool; plan_ids:list[str]=Field(default_factory=list)
class InstitutionCreate(BaseModel): name:str; code:str
class InstitutionOut(BaseModel):
    model_config=ConfigDict(from_attributes=True); id:str; name:str; code:str; status:str; created_at:datetime
class InstitutionSubscriptionOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:str; institution_id:str; bundle_id:str; purchased_by:str; start_date:datetime; end_date:datetime; status:str="active"; is_active:bool
class PlanSelectionOut(BaseModel):
    institution:InstitutionOut; plan:PlanOut; bundle:BundleOut; subscription:InstitutionSubscriptionOut

class MembershipCreate(BaseModel): user_id:str; username:Optional[str]=None; display_name:str=""; role:str; branch_id:Optional[str]=None; department_id:Optional[str]=None; section_id:Optional[str]=None
class UserCreate(BaseModel):
    user_id:Optional[str]=None; username:Optional[str]=None; display_name:str=""; role:str; password:str=Field(min_length=8); branch_id:Optional[str]=None; department_id:Optional[str]=None; section_id:Optional[str]=None
class InstitutionAdminCreate(BaseModel):
    user_id:Optional[str]=Field(default=None,description="Development mode only. Use username when Keycloak is enabled.")
    username:Optional[str]=Field(default=None,description="Keycloak username/email. Required when AUTH_DEV_MODE is false.",examples=["institution.admin@example.com"])
    display_name:str=Field(default="",description="Name shown in institution membership records.",examples=["Institution Administrator"])
    password:str=Field(min_length=8,description="Password for the new Keycloak user.",examples=["Admin@12345"])
    model_config=ConfigDict(json_schema_extra={"example":{"username":"institution.admin@example.com","display_name":"Institution Administrator","password":"Admin@12345"}})
class MembershipOut(BaseModel):
    model_config=ConfigDict(from_attributes=True); id:str; institution_id:str; user_id:str; role:str; display_name:str; branch_id:Optional[str]=None; department_id:Optional[str]=None; section_id:Optional[str]=None; is_active:bool

# Legacy individual APIs
class SubscribeRequest(BaseModel): plan_id:str
class SubscriptionOut(BaseModel):
    model_config=ConfigDict(from_attributes=True); id:str; user_id:str; plan_id:str; start_date:datetime; end_date:datetime; is_active:bool; plan:PlanOut
class SubscriptionStatusOut(BaseModel):
    subscribed:bool; subscription:Optional[SubscriptionOut]=None; is_expired:bool=False; courses_used:int=0; course_quota:Optional[int]=None; active_sessions:int=0; concurrent_session_limit:Optional[int]=None

class CourseCreate(BaseModel): title:str=Field(min_length=1); description:Optional[str]=""
class CourseOut(BaseModel):
    model_config=ConfigDict(from_attributes=True); id:str; title:str; description:Optional[str]=""; institution_id:Optional[str]=None
class CourseAssignmentRequest(BaseModel):
    user_id:str=Field(description="Active institution member ID. Get it from GET /institutions/me/members.",examples=["6a9df2a0-b583-4d18-9f1a-8825e793f760"])
class SessionLoginRequest(BaseModel): device:str="Keycloak managed"; browser:str="Keycloak managed"
class SessionOut(BaseModel):
    id:str; user_id:str; client_id:Optional[str]=None; ip_address:Optional[str]=None; started:Optional[datetime]=None; last_access:Optional[datetime]=None; state:str="active"
class CourseAccessRequest(BaseModel): keycloak_session_id:str
class WatchSessionOut(BaseModel):
    model_config=ConfigDict(from_attributes=True); id:str; user_id:str; course_id:str; keycloak_session_id:str; started_at:datetime; ended_at:Optional[datetime]=None
class WatchHistoryEntry(BaseModel): id:str; course_id:str; course_title:str; started_at:datetime; ended_at:Optional[datetime]=None
class Message(BaseModel): detail:str

class BranchCreate(BaseModel): name:str; code:str
class BranchOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:str; institution_id:str; name:str; code:str
class DepartmentCreate(BaseModel): name:str; code:str
class DepartmentOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:str; branch_id:str; name:str; code:str
class SectionCreate(BaseModel): name:str; academic_year:Optional[str]=None
class SectionOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:str; department_id:str; name:str; academic_year:Optional[str]=None
class CustomPlanRequestCreate(BaseModel):
    branches:int=Field(gt=0); admins:int=Field(gt=0); professors:int=Field(gt=0); students:int=Field(gt=0); courses:int=Field(gt=0); concurrent_sessions:int=Field(gt=0); duration_days:int=Field(gt=0); additional_features:str=""
class CustomPlanQuoteOut(BaseModel):
    id:str; quoted_price:float; currency:str="INR"; status:str
