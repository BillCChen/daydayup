# Web Service

Local URL:

```bash
http://127.0.0.1:8789
```

Start once:

```bash
uv run python web_console.py --host 127.0.0.1 --port 8789
```

The Web service is the control plane. It does not run scan tasks unless started with `--scan-worker`.

Start scan worker once:

```bash
uv run python scan_worker.py
```

Check port:

```bash
lsof -nP -iTCP:8789 -sTCP:LISTEN
```

Stop port process:

```bash
lsof -tiTCP:8789 -sTCP:LISTEN | xargs -r kill
```

Run as macOS background service:

```bash
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.billchen.daydayup.webconsole.plist"
launchctl enable "gui/$(id -u)/com.billchen.daydayup.webconsole"
launchctl kickstart -k "gui/$(id -u)/com.billchen.daydayup.webconsole"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.billchen.daydayup.scanworker.plist"
launchctl enable "gui/$(id -u)/com.billchen.daydayup.scanworker"
launchctl kickstart -k "gui/$(id -u)/com.billchen.daydayup.scanworker"
```

Pause background service:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.billchen.daydayup.webconsole.plist"
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.billchen.daydayup.scanworker.plist"
```

Service config:

```bash
$HOME/Library/LaunchAgents/com.billchen.daydayup.webconsole.plist
$HOME/Library/LaunchAgents/com.billchen.daydayup.scanworker.plist
```

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
