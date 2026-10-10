# ChaosLab frontend
React + TypeScript + Vite + TanStack Query + React Hook Form + Zod + Recharts.
## Run
    npm install
    cp .env.example .env     # set VITE_API_BASE_URL (browser-reachable URL of the FastAPI backend)
    npm run dev              # http://localhost:5173
    npm run typecheck && npm run build
## Docker
    docker build --build-arg VITE_API_BASE_URL=http://localhost:8000 -t chaoslab-ui .
    docker run -p 8080:80 chaoslab-ui
The backend must allow this origin via CORS. `localhost` in the browser is the user's machine, not a container.
## Expected endpoints (under /api/v1)
engines, environments, experiments (+/validate, /runs), runs (+/cancel, /events, /probes, /retry-cleanup), faults/active.
Response field names are defined in `src/api/types.ts`; adjust them to match your backend schema.
## Authentication
Enter an API key in the application; the client sends it as `X-API-Key` from browser local storage. Never pass API keys as Vite build arguments because Vite embeds build arguments in public static assets.

## Known gaps
No SSO or key rotation UI, no edit/duplicate/delete UI, no mock mode, plain CSS instead of Tailwind/shadcn.
