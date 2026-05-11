# Web Service

Local URL:

```bash
http://127.0.0.1:8788
```

Start once:

```bash
uv run python web_console.py --host 127.0.0.1 --port 8788
```

Check port:

```bash
lsof -nP -iTCP:8788 -sTCP:LISTEN
```

Stop port process:

```bash
lsof -tiTCP:8788 -sTCP:LISTEN | xargs -r kill
```

Run as macOS background service:

```bash
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.billchen.daydayup.webconsole.plist"
launchctl enable "gui/$(id -u)/com.billchen.daydayup.webconsole"
launchctl kickstart -k "gui/$(id -u)/com.billchen.daydayup.webconsole"
```

Pause background service:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.billchen.daydayup.webconsole.plist"
```

Service config:

```bash
$HOME/Library/LaunchAgents/com.billchen.daydayup.webconsole.plist
```

Local user CSV:

```bash
local/users.csv
```

Logs:

```bash
logs/web_console.log
logs/web_console_launchd.log
logs/web_console_launchd.err.log
```
