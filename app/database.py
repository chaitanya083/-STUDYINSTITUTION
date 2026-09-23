"""SQLite database configuration."""
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import get_settings
settings=get_settings()
if settings.database_url.startswith("sqlite:///"):
    db_path=settings.database_url.replace("sqlite:///", "", 1); db_dir=os.path.dirname(db_path)
    if db_dir: os.makedirs(db_dir,exist_ok=True)
connect_args={"check_same_thread":False} if settings.database_url.startswith("sqlite") else {}
engine=create_engine(settings.database_url,connect_args=connect_args)
SessionLocal=sessionmaker(autocommit=False,autoflush=False,bind=engine)
Base=declarative_base()
def get_db():
    db=SessionLocal()
    try: yield db
    finally: db.close()

def init_db():
    from app import models
    Base.metadata.create_all(bind=engine)
    if settings.database_url.startswith("sqlite"):
        # Lightweight migrations for existing v2 databases.
        migrations={
            "plans": {"max_students":"INTEGER NOT NULL DEFAULT 10","max_professors":"INTEGER NOT NULL DEFAULT 2","is_active":"BOOLEAN NOT NULL DEFAULT 1","plan_type":"VARCHAR(20) NOT NULL DEFAULT 'basic'","max_branches":"INTEGER NOT NULL DEFAULT 1","max_admins":"INTEGER NOT NULL DEFAULT 1"},
            "plan_bundles": {"max_branches":"INTEGER NOT NULL DEFAULT 1","max_admins":"INTEGER NOT NULL DEFAULT 1"},
            "institution_memberships": {"branch_id":"VARCHAR(36)","department_id":"VARCHAR(36)","section_id":"VARCHAR(36)"},
            "institution_subscriptions": {"status":"VARCHAR(20) NOT NULL DEFAULT 'active'"},
            "watch_sessions": {"keycloak_session_id":"VARCHAR(255)"},
        }
        with engine.begin() as conn:
            for table,columns in migrations.items():
                existing={row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
                for name,definition in columns.items():
                    if name not in existing: conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
            # Upgrade legacy watch_sessions that required a SQLite device-session row.
            cols={row[1] for row in conn.execute(text("PRAGMA table_info(watch_sessions)"))}
            if "device_session_id" in cols and "keycloak_session_id" in cols:
                conn.execute(text("CREATE TABLE IF NOT EXISTS watch_sessions_new (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL, institution_id VARCHAR(36), course_id VARCHAR(36) NOT NULL, keycloak_session_id VARCHAR(255) NOT NULL, started_at DATETIME, ended_at DATETIME)"))
                conn.execute(text("INSERT INTO watch_sessions_new (id,user_id,institution_id,course_id,keycloak_session_id,started_at,ended_at) SELECT id,user_id,institution_id,course_id,COALESCE(keycloak_session_id,device_session_id),started_at,ended_at FROM watch_sessions WHERE NOT EXISTS (SELECT 1 FROM watch_sessions_new n WHERE n.id=watch_sessions.id)"))
                conn.execute(text("DROP TABLE watch_sessions"))
                conn.execute(text("ALTER TABLE watch_sessions_new RENAME TO watch_sessions"))
            if "device_sessions" in {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}:
                conn.execute(text("DROP TABLE device_sessions"))
    seed_default_plans()

def seed_default_plans():
    from app import models
    defaults=[
        dict(name="Free",plan_type=models.PlanType.free.value,price=0,max_branches=1,max_admins=1,max_students=100,max_professors=5,course_quota=10,concurrent_sessions=1,duration_days=30,features="1 branch; 1 admin; 5 professors; 100 students; 10 courses; 1 concurrent session"),
        dict(name="Basic",plan_type=models.PlanType.basic.value,price=29.99,max_branches=2,max_admins=2,max_students=500,max_professors=20,course_quota=50,concurrent_sessions=2,duration_days=30,features="2 branches; 2 admins; 20 professors; 500 students; 50 courses; 2 concurrent sessions"),
        dict(name="Premium",plan_type=models.PlanType.premium.value,price=99.99,max_branches=10,max_admins=10,max_students=5000,max_professors=100,course_quota=500,concurrent_sessions=5,duration_days=365,features="10 branches; 10 admins; 100 professors; 5000 students; 500 courses; 5 concurrent sessions; premium support"),
        dict(name="Custom",plan_type=models.PlanType.custom.value,price=0,max_branches=1,max_admins=1,max_students=1,max_professors=1,course_quota=1,concurrent_sessions=1,duration_days=365,features="Requirement-based quotation and configurable limits"),
    ]
    db=SessionLocal()
    try:
        existing={p.name:p for p in db.query(models.Plan).all()}
        for data in defaults:
            if data["name"] not in existing: db.add(models.Plan(**data))
            else:
                row=existing[data["name"]]
                for k,v in data.items(): setattr(row,k,v)
                row.is_active=True
        db.commit()
    finally: db.close()

def seed_default_bundles():
    """Create catalog bundles for fixed plans only. Custom is quotation-based."""
    from app import models
    db=SessionLocal()
    try:
        plans={p.name:p for p in db.query(models.Plan).all()}
        defaults=[("Free Bundle","Free"),("Basic Bundle","Basic"),("Premium Bundle","Premium")]
        for bundle_name,plan_name in defaults:
            plan=plans.get(plan_name)
            if not plan or db.query(models.PlanBundle).filter_by(name=bundle_name).first(): continue
            bundle=models.PlanBundle(name=bundle_name,price=plan.price,duration_days=plan.duration_days,max_branches=plan.max_branches,max_admins=plan.max_admins,max_students=plan.max_students,max_professors=plan.max_professors,course_quota=plan.course_quota,concurrent_sessions=plan.concurrent_sessions,description=plan.features,is_active=True)
            db.add(bundle); db.flush(); db.add(models.BundlePlanItem(bundle_id=bundle.id,plan_id=plan.id,quantity=1))
        db.commit()
    finally: db.close()
