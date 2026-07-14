# Booking engine v3.6.0

As of: 2026-07-14 20:34 CST

## Purpose

This release uses the first real v3.5 success and the failed adjacent-hour attempts from 2026-07-14 to harden the default direct-fast path. It preserves fast parallel candidate discovery while making the single-flight final-submit path adaptive, deadline-aware, and safe under ambiguous transport failures.

## Default behavior

- Adjacent candidate start delay: `0s`.
- Candidate discovery concurrency: `3` prewarmed exclusive clients.
- Ordinary failed-submit gap: `0.35s`.
- Confirmed-success and first `too_fast` gap: `1.2s`.
- Consecutive `too_fast` gaps: multiplied by `1.5`, capped at `3.0s`.
- Single `reservationPlace` timeout: `2.5s`.
- Minimum remaining scheduler budget before final submit: `0.75s`.
- Unknown-outcome reconciliation: wait `0.25s`, then query recent orders once with a maximum `1.5s` timeout.

The operator is not expected to tune these values.

## Reliability changes

- A transport failure from `reservationPlace` is never automatically replayed.
- Generic prerequisite reconnects share one total timeout budget; an elapsed timeout does not receive another full timeout period.
- A timed-out final submit is matched against active orders by target date, exact start/end time, and court.
- A confirmed order match is recorded as success with `source=order_reconciliation`.
- An unconfirmed final submit becomes `unknown_outcome`. It is not retried and reserves the affected goal hour against unsafe duplicate booking.
- A two-hour run may still attempt one adjacent hour after one unknown result, but stops once the goal is saturated by confirmed plus unknown outcomes.
- A final submit is skipped when less than 0.75 seconds remain in the scheduler window.

## Observability changes

- Transport outcomes distinguish `timeout` and `transport_error` from business failures.
- Structured events record reconciliation start/result, confirmation source, unknown hours, deadline-budget skips, adaptive cooldown reason, effective gap, and throttle streak.
- Reconciliation HTTP timing is reported separately as `getPlaceOrder`, not mixed into `reservationPlace` latency.
- Expected transport failures use concise redacted log lines rather than full stack traces.

## Verification status

- Focused safety and reconciliation tests: passed.
- Full project regression: 83 tests passed.
- Python compilation, JavaScript syntax, whitespace, version/default, and durable-artifact secret-pattern checks: passed.
- Deployment verification: passed on Aliyun. Both services run engine 3.6.0 with zero restarts; Web/API/default/state/read-only-order/log checks passed.
- No real booking or cancellation is used for smoke testing.
