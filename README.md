# Dev Linker

Dev Linker runs frontend and backend dev servers, proxies both through a single local port (8000), and creates a single public URL via Cloudflare or ngrok.

## Features

- Launches frontend automatically (when frontend exists)
- Auto-detects backend runtime (Docker Compose, Dockerfile, Node, or Python)
- Auto-starts Python/Node backends; Docker is manual by default for reliability
- Detects common frontend/backend ports
- Serves both through one proxy at http://localhost:8000
- Creates a public tunnel for sharing (Cloudflare first, ngrok fallback)
- Terminal-first workflow
- Supports CLI version output with --version

## Project Structure

```text
onelink/
├── onelink/
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
pip install dev-linker
```

## Run

```bash
devlinker
```

Typical startup output:

```text
Dev Linker v1.2.0

[INFO] Booting local services...
[INFO] Detecting frontend/backend ports...
[OK] Frontend -> 5173
[OK] Backend  -> 5000

[OK] Proxy ready at http://localhost:8000

[OK] Tunnel provider: Cloudflare
[OK] Public URL:
    https://xxxx.trycloudflare.com

[INFO] Share this link with collaborators.

Dev Linker Ready
Frontend: http://localhost:5173
Backend:  http://localhost:5000
Proxy:    http://localhost:8000
Public:   https://xxxx.trycloudflare.com
```

Version check:

```bash
devlinker --version
```

Optional overrides:

```bash
devlinker --frontend 5173 --backend 5000
```

Enable Docker auto-start explicitly:

```bash
devlinker --docker
```

If port 8000 is already in use:

```bash
devlinker --frontend 5173 --backend 5000 --proxy-port 18000
```

Default behavior also tries fallback ports automatically when 8000 is busy:

- 8001
- 8002
- 18000

## Important Frontend Rule

Frontend requests must use relative API paths:

```js
fetch("/api/endpoint")
```

Do not hardcode backend host URLs in frontend code.

## Backend Auto-Detection

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

- runner.py expects frontend project in frontend and Flask app in backend/app.py.
- If those paths do not exist, Dev Linker skips launch and only tries to detect already-running services.
- Tunnel selection order is: cloudflared (TryCloudflare), then ngrok.
- If cloudflared is unavailable and ngrok is not configured, Dev Linker prints one-time setup guidance.
- You may need to set ngrok auth once on your machine using ngrok config add-authtoken <token>.
- Dev Linker prints a public URL with `ngrok-skip-browser-warning=true` only when ngrok is used.
- Startup output includes selected tunnel provider (`cloudflare` or `ngrok`).
- When Dev Linker launches a Vite frontend, it sets `ONELINK=1` to disable Vite HMR WebSockets for stable tunnel behavior.

## Real-Time Development Modes

### Option 1: Dev Linker sharing mode (recommended)

- Run `devlinker` to share one combined frontend/backend URL.
- Open local Vite URL yourself for instant HMR updates.
- Share Dev Linker/ngrok URL with others; they can use normal page refresh to see changes.

### Option 2: Full remote HMR mode (bypass Dev Linker)

- Start frontend and backend manually.
- Configure Vite `server.proxy` for `/api` to backend.
- Run `ngrok http <vite-port>` directly so Vite handles WebSocket HMR traffic.
