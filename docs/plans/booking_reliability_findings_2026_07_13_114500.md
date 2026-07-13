# Booking Reliability v3.5 Findings

## Requirements

- Confirm local, GitHub, and Aliyun code alignment before editing.
- Update the booking strategy to address recent zero-hour failures and fragile single-hour direct booking.
- Add sufficient redacted logs for continued optimization.
- Version, publish, and deploy the verified change to Aliyun.
- Preserve credentials, state, logs, and unrelated files.

## Verified Production Findings

- Twelve release-time runs reached `canBook`; only one completed two adjacent hours, three obtained one hour, and eight obtained zero hours.
- The latest six such runs obtained zero hours.
- On the current gate implementation, 18 `canBook` responses included seven successes; the downstream final outcomes were three `数据错误`, five `操作过快`, two timeouts/exceptions, and zero successes.
- On 2026-07-11, two `canBook` successes occurred at 12:00:00.611 and 12:00:00.653. Final submission responses arrived at 12:00:01.746, 12:00:02.689, and 12:00:04.122; none succeeded.
- Current read-only upstream checks succeed, Aliyun NTP is synchronized, and release triggers occur at 12:00:00.001-00.002. Authentication, host clock, and process liveness are not the primary cause.

## Verified Code Findings

- Each candidate follows `canBook -> getOfferInfo -> getUseCardInfo -> reservationPlace`, with fixed sleeps between stages.
- Two-hour direct-fast generates the full candidate list but slices center and adjacent groups to `per_hour_limit=3`.
- When the initial speculative batch has no final success, it returns immediately without using `window_seconds` for further waves.
- Speculative workers construct new `KeepAliveClient` instances after release; the prewarmed primary client is not reused by those workers.
- Single-hour direct-fast uses sequential candidate execution and does not use the two-hour speculative scheduler.
- Exact booking re-fetches availability, executes the four calls sequentially, and has no same-candidate `操作过快` retry.
- Existing tests pass but do not cover a competitive `duration=1 + direct-fast` timing path.

## Main Design Decision

Use a bounded wave scheduler that treats winning any first hour as the primary objective. Keep the verified API order, prewarm a client pool before release, attempt the configured court pool in deterministic waves, serialize or tightly bound final commits through one scheduler, and continue until success or `window_seconds` expires.

Scheduler invariants:

- Use one shared scheduler for one- and two-hour direct-fast runs.
- Default to three prewarmed, exclusively borrowed HTTP clients; never share one connection concurrently.
- A wave contains at most `direct_max_inflight` candidates.
- The candidate list includes the full configured court pool and all eligible target hours.
- After the first success, only same-goal adjacent hours remain eligible for a two-hour target; a one-hour target stops all later final submissions.
- `reservationPlace` remains globally single-flight. A fast-rate response is deferred to a later wave instead of owning and blocking the final-submit gate.
- Retry only transient outcomes within a bounded per-candidate attempt count; do not repeat permanently taken candidates.
- `window_seconds` is an upper bound. Finishing early is allowed only for target completion or explicit candidate exhaustion.

## Implementation Status

- The core scheduler now uses bounded waves and an exclusive prewarmed client pool for both one- and two-hour direct-fast goals.
- Final submissions remain single-flight and are deadline-aware; transient outcomes can return to a later wave under the attempt bound.
- HTTP logging now records status, elapsed time, payload size, normalized outcome, and data shape without emitting full successful payloads.
- Regression coverage and UI default parity remain open before release; the Web command defaults, exact-booking stage tracing, and shutdown cleanup are implemented.
- The first static compile passes. Review found two privacy hardening items before testing: exception text must be redacted and failure responses must not stringify structured `data` objects.
- `build_booking_command` still emits the old `0.2/0.85/1.35` direct-fast timing values, so parser-only default changes would not affect Web-launched production jobs.
- `JobManager.snapshot` and `active_job_ids` poll finished subprocesses without finalizing history, allowing the separate orphan sweep to race with the output-reader finalizer.
- A three-client bound with the original candidate ordering spent the first wave on three courts for the same hour. The scheduler now selects distinct hours first, then fills unused wave capacity by rank.
- A fast retry deferred to another wave cannot keep an exclusive retry owner, because the next wave is not launched until current workers drain; only the longer cooldown should carry across waves.
- Second-resolution filenames collide when the Web console starts concurrent jobs for the same target. v3.5 filenames therefore need microseconds and PID, while `run_id` carries the same correlation identity.

Version and rollback decisions:

- Engine version: `3.5.0`.
- Planned Git tag: `v3.5-booking-reliability-observability`.
- Baseline rollback commit: `a502ea62f217ddaff288d30f93a49db0ea8b2da6`.

## Logging Contract

Emit single-line key-value events with stable event names. Required fields where applicable:

- `engine_version`, `run_id`, wall-clock timestamp, monotonic offset.
- target date/range/duration/mode and redacted credential fingerprints.
- phase, wave, candidate rank, hour, court, and reason for selection/skip.
- endpoint, HTTP status, elapsed time, queue wait, gate wait, and attempt number.
- normalized outcome categories: success, taken, too_fast, data_error, timeout, server_error, auth_error, other_business_error, skipped.
- per-wave and final counts plus p50/p90/max endpoint latency.

Never log raw tokens, cookies, card indices, offer IDs, member names, card balances, full upstream response payloads, or booking identifiers.

## Deployment Contract

- Deploy only after local tests and review pass.
- Push the exact commit and tag first; Aliyun must fast-forward to that commit.
- Preserve `/opt/huairou/daydayup/state` and do not run real booking/cancellation in smoke tests.
- Rebuild and restart only the Daydayup Compose project.
- Rollback target is baseline commit `a502ea6` until the new release proves stable.

## Resources

- `enhanced_book_smart_v2.py`: scheduler and critical API chain.
- `web_console.py`: Web job launch, exact booking, and history finalization.
- `tests/test_fast_booking_modes.py`: current direct/guided mode coverage.
- `/opt/huairou/daydayup/state/logs`: production booking evidence.
- `/opt/huairou/daydayup/docker-compose.yml`: production service definition.

## Deployment Findings

- The production containers belong to Compose project `daydayup-prod`; invoking Compose without `-p daydayup-prod` would target a different project and risk duplicate services.
- The production image contains the runtime while the repository is mounted into the containers, so a source fast-forward plus service recreation activates the new code; rebuilding preserves the established deployment workflow.
- Aliyun-origin public HTTPS verification is authoritative for this deployment because the local resolver returned a synthetic `198.18.2.185` address. The server-origin public route returned `200`, and TLS verification succeeded there.
- No real booking or cancellation was used as a smoke test. Reliability improvement remains a high-confidence engineering inference until the next competitive release window supplies v3.5 event evidence.
