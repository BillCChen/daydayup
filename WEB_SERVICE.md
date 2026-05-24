# Web Service (Server Deployment)

Production is deployed on the remote server (`a55002`) and managed there.

Server URL (service on remote host):

```bash
http://127.0.0.1:8789 on a55002
```

Start on server:

- SSH to `a55002`, then run `uv run python web_console.py --host 127.0.0.1 --port 8789`
- The service auto-configures `www.147soft.cn` host alias fallback, so starting command itself is enough for normal environments. Set `DAYDAYUP_HOST_ALIASES` only when you need a custom mapping.
- Start scan worker in an isolated tmux process: `./scripts/daydayup_tmux.sh start`
  - This starts:
    - `daydayup-web`: `web_console.py`
    - `daydayup-scan`: `scan_worker.py`
- Control helper:
  - `./scripts/daydayup_tmux.sh stop`
  - `./scripts/daydayup_tmux.sh restart`
  - `./scripts/daydayup_tmux.sh status`
- The Web service is the control plane. It does not run scan tasks unless started with `--scan-worker`.

Local `launchctl` setup is not used for production anymore.

Check port:

```bash
lsof -nP -iTCP:8789 -sTCP:LISTEN
```

Stop port process:

```bash
lsof -tiTCP:8789 -sTCP:LISTEN | xargs -r kill
```

Production service lifecycle is controlled in the remote environment (not local host).

Local user CSV:

```bash
local/users.csv
```

Logs:

```bash
logs/web_console.log
logs/web_console_launchd_8789.log
logs/web_console_launchd_8789.err.log
logs/scan_worker_launchd.log
logs/scan_worker_launchd.err.log
```
