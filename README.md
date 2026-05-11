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

## Files

- `web_console.py`: local HTTP service and API bridge.
- `web/`: browser UI.
- `easyserp_client.py`: shared EasySERP request helpers.
- `enhanced_book_smart_v2.py`: booking runner used by the web service.
- `pyproject.toml` and `uv.lock`: uv environment control.
