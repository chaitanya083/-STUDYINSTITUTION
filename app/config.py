"""
Central application configuration.

All values are read from environment variables (or a local .env file).
See .env.example for the full list of supported settings.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- Application ----
    app_name: str = "StudyInstitution"
    app_env: str = "development"
    app_debug: bool = True
    app_secret_key: str = "dev-secret-key"

    # ---- Database ----
    database_url: str = "sqlite:///./data/studyinstitution.db"

    # ---- Keycloak ----
    # Default to localhost for local development. Docker services override this via
    # environment variables, so the same app can run either outside or inside Compose.
    keycloak_url: str = "http://localhost:8080"
    keycloak_realm: str = "study-institution"
    keycloak_admin: str = "admin"
    keycloak_admin_password: str = "admin"
    keycloak_admin_realm: str = "master"
    keycloak_admin_client_id: str = "admin-cli"

    keycloak_backend_client_id: str = "study-backend"
    keycloak_backend_client_secret: str = "change-this-client-secret"
    keycloak_frontend_client_id: str = "study-frontend"

    keycloak_student_role: str = "student"
    keycloak_admin_role: str = "super_admin"
    keycloak_institution_admin_role: str = "institution_admin"
    keycloak_professor_role: str = "professor"

    default_admin_username: str = "admin@studyinstitution.com"
    default_admin_password: str = "Admin@12345"
    default_student_username: str = "student@studyinstitution.com"
    default_student_password: str = "Student@12345"

    # When true, accepts locally-signed HS256 "dev" tokens instead of
    # verifying against the live Keycloak JWKS endpoint. Used for local
    # development and automated tests where a real Keycloak server may
    # not be running. Must be false in any real deployment.
    auth_dev_mode: bool = False

    @property
    def issuer(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/certs"

    @property
    def token_url(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/token"


@lru_cache
def get_settings() -> Settings:
    return Settings()
