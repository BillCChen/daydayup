# daydayup

Private local web console for court booking.

## Setup

```bash
uv sync
```

## Run

```bash
uv run python web_console.py
```

Default URL:

```bash
http://127.0.0.1:8788
```

On first launch, the page asks for a WeChat OAuth redirect URL and account credentials to obtain a token. Local runtime state is stored under `local/` and is not committed.

## macOS App

Build an Apple Silicon `.app` package:

```bash
scripts/build_macos_app.sh
```

Build outputs:

```text
dist/Daydayup.app
dist/Daydayup-v0.1.0-macos.zip
```

Install by dragging `Daydayup.app` into `/Applications`, then double-click it. The App starts the local web service and opens `http://127.0.0.1:8788` in the default browser.

The packaged App stores runtime data under:

```text
~/Library/Application Support/Daydayup/
```

The current build is for Apple Silicon Macs. Intel Macs require a separate build on an Intel macOS environment.

## Files

- `app_launcher.py`: macOS App entrypoint.
- `Daydayup.spec`: PyInstaller App build specification.
- `scripts/build_macos_app.sh`: macOS App build script.
- `web_console.py`: local HTTP service and API bridge.
- `web/`: browser UI.
- `easyserp_client.py`: shared EasySERP request helpers.
- `enhanced_book_smart_v2.py`: booking runner used by the web service.
- `pyproject.toml` and `uv.lock`: uv environment control.
