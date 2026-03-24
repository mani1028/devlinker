# OneLink

OneLink runs frontend and backend dev servers, proxies both through a single local port (8000), and creates a single public URL via Cloudflare or ngrok.

## Features

- Launches frontend and backend (when frontend and backend/app.py exist)
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
pip install onelink
```

## Run

```bash
onelink
```

Typical startup output:

```text
✨ OneLink v0.1.0

🚀 Starting services...
🔍 Detecting services...
	• Frontend -> 5173
	• Backend  -> 5000

🌐 Proxy ready at http://localhost:8000

⚡ Tunnel provider: Cloudflare
🌍 Public URL:
	https://xxxx.trycloudflare.com

👉 Share this link with anyone
```

Version check:

```bash
onelink --version
```

Optional overrides:

```bash
onelink --frontend 5173 --backend 5000
```

If port 8000 is already in use:

```bash
onelink --frontend 5173 --backend 5000 --proxy-port 18000
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

## Notes

- runner.py expects frontend project in frontend and Flask app in backend/app.py.
- If those paths do not exist, OneLink skips launch and only tries to detect already-running services.
- Tunnel selection order is: cloudflared (TryCloudflare), then ngrok.
- If cloudflared is unavailable and ngrok is not configured, OneLink prints one-time setup guidance.
- You may need to set ngrok auth once on your machine using ngrok config add-authtoken <token>.
- OneLink prints a public URL with `ngrok-skip-browser-warning=true` only when ngrok is used.
- Startup output includes selected tunnel provider (`cloudflare` or `ngrok`).
- When OneLink launches a Vite frontend, it sets `ONELINK=1` to disable Vite HMR WebSockets for stable tunnel behavior.

## Real-Time Development Modes

### Option 1: OneLink sharing mode (recommended)

- Run `onelink` to share one combined frontend/backend URL.
- Open local Vite URL yourself for instant HMR updates.
- Share OneLink/ngrok URL with others; they can use normal page refresh to see changes.

### Option 2: Full remote HMR mode (bypass OneLink)

- Start frontend and backend manually.
- Configure Vite `server.proxy` for `/api` to backend.
- Run `ngrok http <vite-port>` directly so Vite handles WebSocket HMR traffic.
