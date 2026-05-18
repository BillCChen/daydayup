# Daydayup Deployment Operator Guide

本文件用于把项目迁移到其他机器后，向协作助手说明当前系统能力、部署方式、接口边界和验证要求。它不是可安装 skill，只是操作规范。

## Project Scope

Daydayup 是一个本地 Web 预约操作台，负责查询羽毛球场地、发起抢场任务、查看余额与活跃预约、精确提交选中场地、退订预约、创建扫描预约任务，并可通过临时公网隧道发送访问地址。

不要在公开仓库、提交记录、日志摘录或对话中暴露真实 `token`、`JSESSIONID`、访问密码、SMTP 授权码、会员卡号。

## Runtime

- Python package runner: `uv`
- Main Web process: `web_console.py`
- Booking script: `enhanced_book_smart_v2.py`
- Shared API client: `easyserp_client.py`
- Default local URL: `http://127.0.0.1:8788`
- Default shop number: `1001`
- Default EasySERP base URL: `https://www.147soft.cn/easyserpClient`
- Local runtime files: `local/`
- Runtime logs: `logs/`

Start local service:

```bash
uv run python web_console.py --host 127.0.0.1 --port 8788
```

Start LAN-accessible service:

```bash
uv run python web_console.py --host 0.0.0.0 --port 8788
```

Stop port listener:

```bash
lsof -tiTCP:8788 -sTCP:LISTEN | xargs -r kill
```

Verify:

```bash
curl -sS http://127.0.0.1:8788/ | rg "Daydayup|预约操作台"
uv run python -m py_compile web_console.py enhanced_book_smart_v2.py
uv run python -m unittest discover -s tests
```

## Configuration Files

- `local/users.csv`: user accounts, access password, admin password, token, optional `JSESSIONID`, preferred card name, enabled flag.
- `local/booking_history.json`: Web-triggered booking records.
- `local/scan_tasks.json`: persistent scan tasks and target states.
- `local/scan_events.json`: important scan decisions and email event history.
- `local/cloudflared_mail.env`: SMTP and recipient settings used by tunnel monitor and scan event emails.
- `local/cloudflared_url.txt`: latest known public tunnel URL.

Expected mail config keys:

```bash
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=<sender>
SMTP_PASSWORD=<smtp_auth_code>
MAIL_TO=<recipient>
```

## Web Features

### Authentication And Users

The Web UI requires an access password. User management is protected by an admin password. The user store supports multiple enabled users; API calls accept `user_key` and default to the first enabled user when omitted.

Relevant endpoints:

- `POST /api/auth/login`
- `GET /api/users`
- `POST /api/users/unlock`
- `POST /api/users`
- `POST /api/token/auth-url`
- `POST /api/token/exchange`

Deployment prompt:

```text
检查 Daydayup 用户配置。不要打印 token 或 Cookie。确认 local/users.csv 是否存在、至少有一个 enabled 用户、访问密码和管理密码可用，并说明如何在 Web 页面更新 token。
```

### Balance And Active Bookings

The UI displays card balance, primary card, other cards, and active bookings. Card selection prefers configured card name with positive balance, then any positive-balance card, then first card.

Relevant endpoints:

- `GET /api/cards`
- `GET /api/bookings?success=1&all=0`

Deployment prompt:

```text
检查余额和活跃预约链路。只汇报是否能拿到卡列表、主卡余额、活跃预约数量；不要输出完整订单号、token 或 Cookie。
```

### Manual Booking Job

The traditional booking task starts `enhanced_book_smart_v2.py` as a child process. It supports target date or relative days, time range, duration, priority courts, backup courts, dry run, immediate execution, window seconds, poll interval, and error backoff.

Relevant endpoints:

- `POST /api/booking/start`
- `GET /api/booking/job`
- `POST /api/booking/stop`
- `GET /api/booking/history`

Important behavior:

- Prewarm happens shortly before the target window.
- `window_seconds` controls each run window.
- `poll_interval` controls ordinary query spacing.
- UI presets currently use `0.05s` for fast and `0.08s` for balanced.
- Booking logs are written under `logs/booking_smart_v2_*.log`.

Deployment prompt:

```text
分析最近一次预约失败原因。读取最新 booking_smart_v2 日志和 booking_history.json，给出触发时间、首次看到候选时间、下单接口返回、失败统计和直接原因。
```

### Availability Distribution And Exact Booking

The availability panel queries upcoming days, renders bookable date × hour × court chips, supports selecting one or two hours on the same date, and submits exactly selected courts.

Relevant endpoints:

- `GET /api/availability?days=5`
- `POST /api/booking/exact`

Selection rules:

- First selected date locks the selection date.
- One hour can have only one selected court; selecting another court in the same hour replaces it.
- At most two distinct hours can be selected.
- Total selected `pay_value` must not exceed primary card balance.
- Warnings disappear after 3 seconds.
- Exact booking re-fetches current availability before reserving each selected slot.

Price rule:

- Monday to Friday start hour `< 16`: `pay_value = 20`.
- Monday to Friday start hour `>= 16`: `pay_value = 30`.
- Saturday and Sunday: `pay_value = 30`.

Auto-combo behavior:

- Recommended combinations are generated only when adjacent hours exist.
- Each two-hour window produces one recommendation.
- Same court is preferred, then configured safe court order is used.
- Clicking a recommendation only changes selection; submit still requires pressing the submit button.

Deployment prompt:

```text
检查精确预约功能。确认 /api/availability 返回 start_time/end_time/price_value/pay_value，确认前端最多选同一天两个小时，确认 /api/booking/exact 会重新查询并按选中场地提交。
```

### Cancel Booking

Cancellation follows captured EasySERP flow:

1. Query recent orders with `place/getPlaceOrder`.
2. Preview refund with `common/getRefundTime` and `place/getCanclePlaceMoney`.
3. Confirm cancellation with `POST place/canclePlaceAppointment`.

Backend endpoints:

- `POST /api/cancel/preview`
- `POST /api/cancel`

Captured cancellation payload fields:

```text
outtradeno=<bill_num>
token=<token>
reason=<reason>
affiliateCard=<affiliate_card>
```

UI behavior:

- Active booking rows show a clickable `可退` chip for refundable bookings.
- Clicking `可退` opens a warning dialog.
- The dialog loads refund preview and requires typing `CANCEL`.
- Confirming calls `/api/cancel`, then refreshes active bookings and cards.

Deployment prompt:

```text
检查退订功能是否按抓包流程实现。确认预览接口、正式取消接口、payload 字段、确认文本和取消后刷新逻辑；不要猜测新接口。
```

### Scan Booking Tasks

Scan tasks run inside the Web service process and persist under `local/scan_tasks.json`. A task can contain multiple targets. Each target is a specific date and time window.

Relevant endpoints:

- `GET /api/scan/tasks`
- `POST /api/scan/tasks`
- `POST /api/scan/tasks/update`

Task parameters:

- `targets[]`: each target has `date`, `start_time`, `end_time`.
- `success_mode`: `any` or `all`.
- `scan_interval_minutes`: `5` to `1440`, default `30`.
- `court_mode`: `selected` or `all`.
- `selected_courts`: default safe courts `2,3,4,6,7,8,9,10,11`.
- `same_court_required`: applies to multi-hour candidates.
- `iterative_optimization`: allows automatic cancellation and rebooking when a better candidate appears.

Scheduling rules:

- `release_at = target_date - 5 days at 12:30`.
- Before release time, the task stays quiet.
- Every day `11:30 <= now < 12:30` is a no-scan window.
- Within 24 hours before target start, the task does not book, cancel, or rebook.
- Service stop means scanning stops; service restart reloads task state from JSON.

Candidate rules:

- A one-hour target can book one available slot.
- A target longer than one hour searches for continuous two-hour candidates.
- If `same_court_required` is true, the two-hour candidate must use the same court.
- Candidate score prioritizes later start time, then same court, then safe court order.
- Successful booking records matched `bill_num` for future cancellation or rebooking.

Important email events:

- Booking success or partial success.
- Booking failure.
- Cancellation success or failure.
- Rebooking success or failure.
- Task completion.
- Target expiration.
- Daily summary after 22:00 for important events in the past 24 hours.

Deployment prompt:

```text
检查扫描任务能力。确认单小时目标可创建并生成单 slot 候选，确认多小时目标仍找连续两小时，确认 11:30-12:30 静默、24 小时禁止区和邮件事件都保留。
```

### Public Tunnel Monitor

`cloudflared_watch.py` monitors a quick Cloudflare Tunnel, restarts it if unreachable, stores the latest public URL, and emails the URL when it changes or refreshes.

Core files:

- `cloudflared_watch.py`
- `local/cloudflared_mail.env`
- `local/cloudflared_url.txt`
- `logs/cloudflared.log`
- `logs/cloudflared_launchd.err.log`

Manual check:

```bash
uv run python cloudflared_watch.py --send-always
```

Force refresh:

```bash
uv run python cloudflared_watch.py --force --send-always
```

Deployment prompt:

```text
检查公网隧道监控。确认 cloudflared 日志中能提取 trycloudflare URL，确认 URL 可访问，确认 local/cloudflared_url.txt 已更新；不要输出 SMTP 授权码。
```

## API Summary

```text
GET  /api/status
GET  /api/users
POST /api/users/unlock
POST /api/users
POST /api/token/auth-url
POST /api/token/exchange
GET  /api/cards
GET  /api/bookings
GET  /api/availability
GET  /api/booking/history
GET  /api/booking/job
POST /api/booking/start
POST /api/booking/stop
POST /api/booking/exact
POST /api/cancel/preview
POST /api/cancel
GET  /api/scan/tasks
POST /api/scan/tasks
POST /api/scan/tasks/update
```

All protected API requests require the local access password through `X-Daydayup-Key` after login.

## Deployment Checklist

1. Install dependencies with `uv sync`.
2. Create `local/` and configure `local/users.csv`.
3. Optional: configure `local/cloudflared_mail.env`.
4. Start `web_console.py`.
5. Open local URL and log in.
6. Verify `/api/status`, cards, active bookings, availability.
7. Run tests before editing behavior.
8. Keep `logs/` and `local/` out of commits unless explicitly needed.

## Regression Checklist

Run after code changes:

```bash
uv run python -m unittest discover -s tests
uv run python -m py_compile web_console.py enhanced_book_smart_v2.py
/Users/billchen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node --check web/app.js
```

Manual UI checks:

- Login screen loads.
- Cards and active bookings refresh.
- `可退` opens warning dialog and requires `CANCEL`.
- Availability query renders court chips and exact submit area.
- Scan task form can create a one-hour target and a multi-hour target.
- Booking job can start in dry-run mode and be stopped.

## Safe Collaboration Rules

- Reproduce bugs from logs or tests before changing behavior.
- Do not invent upstream API endpoints; use captured traffic or existing code.
- Do not run real booking or cancellation commands unless explicitly asked.
- Prefer narrow tests for each changed flow.
- Preserve unrelated local files, especially `local/`, `logs/`, and `screenshots/`.
- Keep secrets out of reports and examples.
