# ChaosLab

A unified Chaos Engineering platform that provides one interface for managing resilience experiments, integrating Chaos Toolkit, Pumba, Toxiproxy, and Chaos Mesh.

## Architecture

* **Frontend**: React, TypeScript, Vite, Tailwind CSS
* **Backend**: Python, FastAPI, SQLAlchemy, PostgreSQL, Redis, RQ

## Getting Started

1. **Prerequisites**: Docker and Docker Compose must be installed.
2. **Environment Setup**:
   * Generate backend `.env` configuration from the template if not present:
     ```bash
     cp backend/.env.example backend/.env
     ```
   * Set `POSTGRES_PASSWORD` and hashed `CHAOS_API_KEYS` in `backend/.env`. Generate a key with `python backend/scripts/make_api_key.py <user> <role>` and hash it using the script's output. Keep the raw key in browser local storage; it is never embedded in the frontend build.
3. **Start the Platform**:
   ```bash
   # Start core services (API, workers, Postgres, Redis, Frontend)
   docker compose --env-file backend/.env up --build -d

   ```
4. **Accessing the UI**:
   * Open your browser and navigate to `http://localhost:3000`

## Components

* `frontend/`: Vite + React frontend application.
* `backend/`: FastAPI application, RQ workers, and engine adapters.
* `docker-compose.yml`: Root compose file for frontend, backend, database, workers, and Toxiproxy.

Pipeline submissions pause in `PENDING_APPROVAL`. A separate approver must approve before the pipeline worker builds uploaded source and starts the experiment. Pipeline state is stored in PostgreSQL; source/build artifacts are in the shared artifacts volume. The pipeline worker mounts the Docker socket, which grants host-level Docker control, so use only with trusted projects on a disposable development host.

## Running an experiment

Add your disposable or staging target to `CHAOS_ENVIRONMENTS` in `backend/.env`, and add its probe hostname to `CHAOS_PROBE_ALLOWED_HOSTS`. For Toxiproxy experiments, configure a proxy for that service. The platform does not start a sample application or create a sample proxy automatically.
