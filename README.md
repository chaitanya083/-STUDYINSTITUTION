# StudyInstitution

StudyInstitution is a multi-tenant education platform built with FastAPI, Keycloak, and SQLite. It supports tenant-based institutions, plan subscriptions, user roles, organization hierarchy, course assignment, and student learning activity tracking.

## Overview

The application is designed around a tenant model:

- Super Admin manages the platform and plans.
- Institution Admin manages one institution.
- Professor manages teaching scope within the institution.
- Student accesses assigned courses and learning sessions.

Keycloak is the identity provider for authentication, roles, user sessions, and group membership. FastAPI handles business application logic and tenant workflows. SQLite stores institutional and subscription data.

## Technology stack

- Python 3.11+
- FastAPI
- SQLAlchemy
- SQLite
- Keycloak 25
- Docker Compose
- Pytest

## Project structure

```text
.
├── app/
│   ├── config.py
│   ├── crud.py
│   ├── database.py
│   ├── keycloak.py
│   ├── main.py
│   ├── models.py
│   ├── schemas.py
│   ├── security.py
│   └── __init__.py
├── keycloak/
│   └── keycloak_setup.py
├── tests/
│   ├── conftest.py
│   ├── test_app.py
│   └── __init__.py
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── pytest.ini
├── README.md
├── requirements.txt
├── run.py
└── data/
```

## Prerequisites

- Python 3.11+
- pip
- Docker + Docker Compose (recommended for Keycloak)
- A terminal with access to the project directory

## Configuration

Copy the example environment file and adjust values if needed:

```bash
copy .env.example .env
```

The main settings include:

- application name, debug mode, secret key
- database URL
- Keycloak URL, admin credentials, realm names, and client IDs
- default admin/user accounts used in local dev and setup
- auth_dev_mode flag for local test/dev token handling

Important defaults from the project:

- Keycloak URL: http://localhost:8080
- Realm: study-institution
- Admin username: admin
- Admin password: admin
- Default platform admin: admin@studyinstitution.com / Admin@12345
- Default student: student@studyinstitution.com / Student@12345

## Running with Docker Compose

This is the recommended way to run the project because it starts the backend and Keycloak together.

```bash
docker compose up -d --build
```

Then check:

- Backend API: http://localhost:8000
- Dashboard: http://localhost:8000/dashboard
- Swagger UI: http://localhost:8000/docs
- Keycloak Admin Console: http://localhost:8080

To stop the stack:

```bash
docker compose down
```

## Running locally without Docker

Install dependencies:

```bash
pip install -r requirements.txt
```

Then start the FastAPI app:

```bash
python run.py
```

Or directly:

```bash
uvicorn app.main:app --reload
```

The app is served at:

- http://localhost:8000
- Swagger docs: http://localhost:8000/docs

## Keycloak setup

The project includes a provisioning script at [keycloak/keycloak_setup.py](keycloak/keycloak_setup.py) that creates the realm, roles, clients, and default admin/student users.

This is usually triggered by the Docker Compose stack as a setup service before app usage.

The provisioning script ensures:

- a Keycloak realm named study-institution exists
- required roles exist: super_admin, institution_admin, professor, student
- backend and frontend clients are configured
- default admin and sample student accounts are created

## Default API workflow

The application expects the following workflow, which matches the implemented routes in the API:

1. View plans
   - GET /plans
2. Authenticate
   - POST /auth/token
3. Create institution as Super Admin
   - POST /admin/institutions
4. Select a plan for the institution
   - POST /admin/institutions/{institution_id}/select-plan
5. Create an institution admin
   - POST /admin/institutions/{institution_id}/admins
6. Create branches / departments / sections
   - POST /institutions/me/branches
   - POST /institutions/me/departments/{branch_id}
   - POST /institutions/me/sections/{department_id}
7. Create professors and students
   - POST /institutions/me/professors
   - POST /institutions/me/students
8. Create courses
   - POST /institutions/me/courses
9. Assign courses
   - POST /professor/courses/{course_id}/assign
10. Student learning flow
   - POST /courses/{course_id}/access
   - GET /student/courses
   - GET /history

## Core roles and permissions

### Super Admin

- manages plans
- creates institutions
- manages subscriptions and activations
- inspects platform-wide institution data

### Institution Admin

- creates members and hierarchy
- creates courses
- manages institution-level assignments
- manages institution subscription and custom plan requests

### Professor

- assigns courses to students within the professor scope
- views course and member information in the institution

### Student

- views assigned courses
- starts and ends watch sessions
- accesses learning history

## Authentication and sessions

The app uses Keycloak-backed identity validation and session checks. The backend also supports local dev mode via the auth_dev_mode setting when appropriate for testing and local work.

Swagger authorization is done with a bearer token returned by:

- POST /auth/token

Then add the token to the Swagger UI Authorization header as:

```text
Bearer <access_token>
```

## API documentation

FastAPI generates Swagger UI automatically.

Open:

```text
http://localhost:8000/docs
```

or the OpenAPI JSON at:

```text
http://localhost:8000/openapi.json
```

## Dashboard

Open `http://localhost:8000/dashboard` and sign in with a Keycloak account. The dashboard loads the view for the signed-in role and displays live institution, course, membership, learning, session, or platform data from the API.

## Testing

Run the project tests with:

```bash
pytest -q
```

The current suite validates planning, institution workflow, course assignment, session limits, subscription expiry, and Keycloak error handling.

## Notes

- The backend expects Keycloak to be available for full production-like operations.
- The project includes a built-in setup script for realm and default users.
- The app is designed to work both in Docker and locally, with environment settings allowing each mode to operate cleanly.
- For local development, environment variables should be stored in .env and should not be committed to source control.

## Quick start summary

```bash
copy .env.example .env
pip install -r requirements.txt
docker compose up -d --build
```

Then open Swagger at:

```text
http://localhost:8000/docs
```

This is the standard development workflow for the project.
