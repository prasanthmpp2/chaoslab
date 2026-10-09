# ChaosLab

A unified Chaos Engineering platform that provides one interface for managing resilience experiments, integrating Chaos Toolkit, Pumba, Toxiproxy, and Chaos Mesh.

## Architecture

* **Frontend**: React, TypeScript, Vite, Tailwind CSS
* **Backend**: Python, FastAPI, SQLAlchemy, PostgreSQL, Redis, RQ

## Getting Started

1. **Prerequisites**: Docker and Docker Compose must be installed.
2. **Environment Setup**:
   * Generate backend `.env` configuration from templates if not present:
     ```bash
     cp chaos-platform-backend/.env.example chaos-platform-backend/.env
     ```
   * Set `CHAOS_API_KEYS` in `chaos-platform-backend/.env`. (e.g. using `python chaos-platform-backend/scripts/make_api_key.py admin admin`).
   * Add the generated API key to `docker-compose.yml` under `frontend` build arguments as `VITE_API_TOKEN`.
3. **Start the Platform**:
   ```bash
   # Start core services (API, Worker, Postgres, Redis, Frontend)
   docker compose up --build -d

   # Optional: Start testbed services (Toxiproxy, Dummy Payment Service)
   docker compose --profile testbed up -d
   ```
4. **Accessing the UI**:
   * Open your browser and navigate to `http://localhost:3000`

## Components

* `frontend/chaoslab`: Contains the Vite + React frontend application.
* `chaos-platform-backend`: Contains the FastAPI application and worker code.
* `docker-compose.yml`: Root compose file that links frontend, backend, testbed, and database services.

## E2E Testing

Create a test experiment using `toxiproxy` engine, approve it, and execute it to see faults being injected into the sample `payment-service` running in the testbed network.
