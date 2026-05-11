# daydayup

Personal utility scripts.

## Setup

```bash
uv sync
cp .env.example .env
```

Fill local values in `.env`; do not commit them.

## Local UI

```bash
uv run python web_console.py
```

Default URL: `http://127.0.0.1:8788`.

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
