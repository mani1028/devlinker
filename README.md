# Dev Linker

Dev Linker runs frontend and backend dev servers, proxies both through a single local port (8000), and creates a single public URL via Cloudflare or ngrok.

## Features

- Launches frontend automatically (when frontend exists)
- Auto-detects backend runtime (Docker Compose, Dockerfile, Node, or Python)
- Auto-starts Python/Node backends; Docker is manual by default for reliability
- Detects common frontend/backend ports
- Detects Vite frontend across dynamic fallback ports (5173-5190, plus common alternatives)
- Supports Docker backend port auto-detection
- Works with dynamic container host ports
- No config needed for standard FastAPI or Flask plus Docker flows
- Serves both through one proxy at http://localhost:8000
- Creates a public tunnel for sharing (Cloudflare first, ngrok fallback)
- Terminal-first workflow
- Supports CLI version output with --version

## Project Structure

```text
devlinker/
├── devlinker/
│   ├── __init__.py
│   ├── main.py
│   ├── runner.py
│   ├── detector.py
│   ├── proxy.py
│   └── tunnel.py
├── setup.py
├── README.md
└── requirements.txt
```

## Install

For local development:

```bash
pip install .
```

After publishing to PyPI:

```bash
pip install devlinker
```

## Run

```bash
devlinker
```

Typical startup output:

```text
Dev Linker v1.2.2

[INFO] Mode: Auto (FastAPI async proxy + Docker detection)
[INFO] Booting local services...
[INFO] Detecting frontend/backend ports...
[OK] Frontend -> 5173
[OK] Backend  -> 5000

[OK] Proxy ready at http://localhost:8000

[OK] Tunnel provider: Cloudflare
[OK] Public URL:
    https://xxxx.trycloudflare.com
Tip: Press Ctrl+Click to open link

[INFO] Share this link with collaborators.

DevLinker Ready (in 2.4s)
Frontend: http://localhost:5173
Backend:  http://localhost:5000
Access Links:
Local:  http://localhost:8000
WLAN:   http://192.168.1.5:8000
Public: https://xxxx.trycloudflare.com
Tip: Press Ctrl+Click to open link
```

Version check:

```bash
devlinker --version
```

Optional overrides:

```bash
devlinker --frontend 5173 --backend 5000
```

Backend override alias:

```bash
devlinker --backend-port 3001
```

Enable Docker auto-start explicitly:

```bash
devlinker --docker
```

Run local-only mode without tunnel:

```bash
devlinker --no-tunnel
```

Disable WLAN URL output:

```bash
devlinker --no-lan
```

Interactive backend selection (when local and Docker are both detected):

```bash
devlinker --interactive-backend
```

Disable interactive backend selection (keeps local-first behavior):

```bash
devlinker --no-interactive-backend
```

If port 8000 is already in use:

```bash
devlinker --frontend 5173 --backend 5000 --proxy-port 18000
```

Default behavior also tries fallback ports automatically when 8000 is busy:

```text
[WARN] Port 8000 in use
[INFO] Using proxy port: 8001
```

- 8001
- 8002
- 18000

Frontend detection behavior:

- Scans Vite defaults and fallback ports (`5173` through `5190`)
- Also checks common alternatives (`3000`, `4173`, `8080`)
- Retries during startup to catch slow boot cases
- Performs readiness gating before proxy startup (waits until frontend looks like Vite and backend responds)

## Important Frontend Rule

Frontend requests must use relative API paths:

```js
fetch("/api/endpoint")
```

Do not hardcode backend host URLs in frontend code.

## Backend Auto-Detection

Backend port detection runs in this order:

1. Check localhost port 5000
2. If not found, query Docker via Docker SDK (`docker.from_env()`) for published host-to-container port mappings
3. Prioritize containers using labels when present (`devlinker.role=backend`, optional `devlinker.port=<container-port>`)
4. Otherwise rank containers by likely backend identity (name hints like backend/api plus project-name hints)
5. Use the best mapped host port automatically, even when internal port is not 5000
6. If nothing is found, print next-step guidance and exit

If Docker SDK is unavailable, Dev Linker falls back to Docker CLI parsing as a compatibility path.

When both Local and Docker backends are available, Dev Linker prompts you to choose one (TTY mode) unless `--no-interactive-backend` is used.

If backend detection fails, Dev Linker prints a clear checklist showing what it checked and how to recover.

Detection messages include source labels, for example:

```text
[OK] Backend detected (Local) -> port 5000
```

Example Docker dynamic-port message:

```text
[WARN] Backend not found on port 5000
[INFO] Checking Docker containers...
[OK] Backend detected (Docker) -> port 32768
```

Dev Linker checks backend runtime in this order:

1. Docker Compose (`backend/docker-compose.yml`, `docker-compose.yaml`, `compose.yml`, or `compose.yaml`)
2. Docker (`backend/Dockerfile`)
3. Node (`backend/package.json`)
4. Python (`backend/requirements.txt` or `backend/app.py`)

Backend startup commands:

- Docker Compose (default): manual run `docker compose up --build` in `backend/`
- Dockerfile (default): manual run `docker build -t devlinker-backend .` then `docker run --rm -p 5000:5000 devlinker-backend`
- Docker Compose/Dockerfile with `--docker`: Dev Linker runs those Docker commands for you
- Node: `npm run dev` (or `npm start` when `dev` is missing)
- Python: `python app.py`

For containerized Flask backends, ensure:

- App binds to all interfaces: `app.run(host="0.0.0.0", port=5000)`
- Port mapping is present: `-p 5000:5000`

## Notes

- runner.py expects frontend project in frontend and Python app in backend/app.py.
- If those paths do not exist, Dev Linker skips launch and only tries to detect already-running services.
- Tunnel selection order is: cloudflared (TryCloudflare), then ngrok.
- If cloudflared is unavailable and ngrok is not configured, Dev Linker prints one-time setup guidance.
- You may need to set ngrok auth once on your machine using ngrok config add-authtoken <token>.
- Dev Linker prints a public URL with `ngrok-skip-browser-warning=true` only when ngrok is used.
- Startup output includes selected tunnel provider (`cloudflare` or `ngrok`).
- Proxy layer now supports WebSocket upgrades, including Vite HMR over shared links.
- Proxy listens on `0.0.0.0` and can print a WLAN URL for same-network sharing.
- If WLAN access fails on Windows, allow the proxy port in firewall and confirm devices are on the same network.

## Runtime Smoke Test

Run this test to validate proxy behavior end-to-end (frontend HTTP route, backend API forwarding, and WebSocket pass-through):

```bash
python -m unittest tests.test_proxy_runtime
```

The test spins up lightweight local frontend and backend apps, starts Dev Linker proxy, and verifies:

- `GET /` is routed to frontend
- `POST /api/login` is routed to backend
- `ws://.../hmr` round-trip works through proxy

## Troubleshooting Links

If local or shared links show blank pages, connection refused errors, or 404s, check these common causes:

1. Docker backend binding

- Symptom: `http://localhost:<backend-port>` refuses connection.
- Cause: backend process inside container is bound to `127.0.0.1` instead of `0.0.0.0`.
- Fix: run backend with host `0.0.0.0` (example FastAPI/Uvicorn: `uvicorn app.main:app --host 0.0.0.0 --port 8000`).

2. API prefix mismatch

- Symptom: frontend loads through Dev Linker but API calls return 404.
- Cause: frontend calls `/api/...`, but backend routes are mounted without `/api` prefix.
- Fix: expose backend routes under `/api` (or adjust frontend paths to match backend routes).

3. Vite host restrictions

- Symptom: direct Vite URL works, Dev Linker proxy URL is blank or blocked.
- Cause: Vite host protections reject proxied host/port.
- Fix: set Vite `server.host` and `server.allowedHosts` to allow proxy use.

Quick isolate sequence:

1. Open `http://localhost:<backend-port>/docs` (or `/health`) directly.
2. Open Dev Linker local proxy URL and verify UI loads.
3. Use browser network tab to check API status codes for `/api/*` requests.

## Real-Time Development

- Run `devlinker` to share one combined frontend/backend URL.
- Vite HMR and other WebSocket flows are proxied end-to-end through Dev Linker.
- Keep using relative frontend API paths (for example, `/api/endpoint`) so routing stays consistent locally and over tunnel.
