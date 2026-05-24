# daydayup

Personal utility scripts.

## Setup

```bash
uv sync
cp .env.example .env
```

Fill local values in `.env`; do not commit them.

## Deployment mode

This project is deployed on the remote server (`a55002`) and should not be treated as a local production deployment.

### Local usage (development only)

```bash
uv run python web_console.py
```

Local debug URL: `http://127.0.0.1:8789`.

The Web UI creates and edits scan tasks. Run the scan worker separately for background execution:

```bash
uv run python scan_worker.py
```

For LAN access, bind all interfaces and use the CSV access password:

```bash
uv run python web_console.py --host 0.0.0.0
```

## CLI

```bash
uv run python enhanced_book_smart_v2.py -t 17-21 --duration 2
uv run python list_bookings.py
uv run python card_balance.py
```

Runtime state and logs stay local.
