# Booking Engine v3.7.0

As of: 2026-07-15 14:53 CST

## Purpose

Engine 3.7.0 repairs the timeout-reconciliation and rate-limit behavior observed in the 2026-07-15 production booking run. It is designed to work with the Web console defaults and does not require operator tuning.

## Booking behavior

- A successful final submit now enforces a 1.8-second cooldown before the next `reservationPlace` request.
- The first `操作过快` result immediately raises the 1.2-second adaptive base to 1.8 seconds; subsequent consecutive results use 2.7 seconds and then the 3.0-second cap.
- A timed-out `reservationPlace` request is never replayed automatically.
- Reconciliation queries recent orders without server-side date filters and matches the target date, start/end hour, and court locally.
- Read-only reconciliation snapshots are scheduled 0.25, 1.0, and 2.5 seconds after the submit timeout begins reconciliation.
- A matching order in any snapshot confirms success.
- Any failed or incomplete snapshot leaves the outcome indeterminate and stops further final-submit writes.
- Only three successful snapshots with no target match classify the request as stably absent; the scheduler may then continue with other candidates but not the timed-out candidate.

## Logging

Structured events now record:

- the complete reconciliation schedule;
- each snapshot number, scheduled delay, HTTP status, query time, order count, and remaining booking-window budget;
- the final confirmed, stable-not-found, query-failed, or deadline-expired decision;
- whether recovery continues with other candidates;
- the 1.8-second success cooldown and reconciliation schedule in every run configuration.

## Local verification

- `python -m unittest discover -s tests -v`: 87 tests passed.
- `node --check web/app.js`: passed with Node.js 26.0.0.
- Python AST parse: passed with Python 3.12.11 from the project environment.
- `git diff --check`: passed.
- Protected skip-worktree files retained their pre-change SHA-1 values.

## Deployment status

Deployed to Alibaba Cloud from commit `2488a7e8a1f3ede6a1e6728b235db3a3fc8d4242`.

- Release tag: `v3.7-booking-timeout-recovery`.
- Rollback backup: `/opt/huairou/daydayup/backups/20260715_144810_v3_7_0`.
- Production Web and scan containers were rebuilt/recreated and remained up with zero restarts.
- Runtime imports in both containers reported engine 3.7.0.
- Host-local HTTP and ECS-side public HTTPS returned 200.
- Nginx configuration validation passed.
- Production state remained mounted and readable: one user row, two configuration rows, and 24 state files.
- A production read-only, unfiltered recent-order query succeeded with two active orders and one exact 2026-07-19 20:00–21:00 match.
- No Web or scan error lines were found in the first ten minutes after deployment.

The local Mac public-domain probe remained blocked by the current VPN/network path: DNS resolved to `198.18.1.60` and LibreSSL failed before HTTP. The ECS-side public-domain check succeeded, so this is recorded as a client-path verification limitation rather than a production service failure.

## Residual validation

The next real 12:00 booking run is still required to measure conversion under live contention. The most important log signals are `reservation_reconcile_snapshot`, `reservation_reconcile_result`, `reservation_not_confirmed`, and the gate's cooldown reason and streak.
