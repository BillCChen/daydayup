# Web Service

Local URL:

```bash
http://127.0.0.1:8788
```

Start:

```bash
uv run python web_console.py --host 127.0.0.1 --port 8788
```

Build macOS App:

```bash
scripts/build_macos_app.sh
```

App build outputs:

```bash
dist/Daydayup.app
dist/Daydayup-v0.1.0-macos.zip
```

Packaged App runtime data:

```bash
$HOME/Library/Application Support/Daydayup
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

Local config:

```bash
local/config.json
```

Logs:

```bash
logs/web_console.log
logs/web_console_launchd.log
logs/web_console_launchd.err.log
```
