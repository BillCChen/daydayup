# Booking Reliability v3.5 Progress

## Session: 2026-07-13

### Phase 1: Baseline and evidence

- **Status:** complete
- Confirmed local, GitHub `origin/main`, and Aliyun all point to `a502ea62f217ddaff288d30f93a49db0ea8b2da6`.
- Confirmed local and Aliyun working trees are clean.
- Recomputed production outcome counts and critical-path timing from the mounted server logs.
- Confirmed current upstream read-only endpoints, NTP, Docker, and Nginx are healthy.
- Identified candidate truncation, ignored window semantics, cold speculative clients, final-submit serialization, weak single-hour scheduling, and insufficient exact-booking observability.

### Phase 2: Specification and design

- **Status:** complete
- Compared four routes and selected the bounded wave scheduler.
- Defined initial acceptance criteria, logging contract, and deployment/rollback boundaries.
- Fixed the initial concurrency bound at three prewarmed clients with exclusive borrowing.
- Chose a unified direct-fast wave scheduler for one- and two-hour goals.
- Kept the captured four-call API order and rejected call skipping or speculative reordering.

### Phase 3: Implementation

- **Status:** complete
- Added engine version `3.5.0` and stable structured event logging.
- Added a bounded direct-fast wave scheduler shared by one- and two-hour goals.
- Added an exclusive prewarmed HTTP client pool, candidate-attempt limits, deadline-aware commit gating, and normalized HTTP metrics.
- Removed full successful upstream response payloads from HTTP logs.
- Static compilation currently succeeds; integration review identified remaining parser/Web defaults, shutdown cleanup, exact-booking tracing, and legacy-test updates.
- Focused review confirmed the Web launcher still overrides the new engine defaults with v3.4 values and the job-history poll path can mark a just-finished job orphaned before finalization; both are now explicit Phase 3 fixes.
- Updated parser and Web command defaults to the v3.5 scheduler values and exposed bounded inflight/attempt controls.
- Added exact-booking per-stage timing/outcome traces plus one same-candidate `too_fast` retry.
- Closed the history-finalization race and ensured pooled clients are closed during shutdown.
- Added p50/p90/max endpoint summaries and redacted transport-exception events.

### Phase 4: Verification and review

- **Status:** complete
- First full regression run: 56/62 passed; six failures were legacy assertions tied to the removed fixed initial batch.
- The regression signal prompted a scheduler improvement: each bounded wave now prioritizes distinct target hours before adding another court for an already represented hour.
- UI defaults now match the v3.5 launcher defaults.
- Final v3.5 regression run passes all 70 tests.
- JavaScript syntax, Git diff whitespace, and 18 Python source files pass static compilation checks.
- Final log review identified two last hardening changes: deferred fast retries must not reserve the gate owner across waves, and concurrent jobs must never share the same second-resolution log filename.

### Phase 5: Version, publish, and deploy

- **Status:** complete
- Committed release code as `8199435f6d721cd506dc2905f82210ef8ea7eaaa` and published the annotated v3.5 tag.
- Created and checksum-verified an Aliyun state/deployment backup at `/opt/huairou/daydayup/backups/20260713_122200_v3_5_0`.
- Fast-forwarded the Aliyun checkout, rebuilt the shared runtime image, and recreated only the existing `daydayup-prod` Web and scan services.
- Verified engine version, repository identity, Web/API status codes, process commands, zero restarts, state preservation, public HTTPS routing, v3.5 UI markers, and zero recent error-pattern matches.

## Test Results

| Test | Expected | Actual | Status |
|---|---|---|---|
| Baseline production suite | Existing code passes before edits | 62 tests passed in the production-equivalent container | Passed with non-fatal SQLite ResourceWarnings |
| Current credential read-only probes | Authenticated read APIs succeed | Place type, venue, card, and availability calls succeeded | Passed |
| Clock check | Aliyun clock synchronized | `NTP=yes`, `NTPSynchronized=yes` | Passed |
| First v3.5 regression run | Identify integration regressions | 56/62 passed; six legacy strategy assertions require replacement | Expected update required |
| Second v3.5 regression run | Updated behavior and new coverage pass | 67/67 passed | Passed |
| Final v3.5 regression run | Hardening and release candidate pass | 70/70 passed | Passed |
| Static source checks | Python and JavaScript parse cleanly | 18 Python sources compiled in memory; `node --check web/app.js` passed | Passed |
| Aliyun deployment identity | Server runs the pushed release commit and tag | Repo, `origin/main`, and tag matched `8199435f`; engine reported `3.5.0` | Passed |
| Aliyun service smoke | No booking/cancellation; Web and scan stay healthy | Local/public Web `200`, unauthenticated API `401`, both containers running with zero restarts | Passed |
| State and rollback artifacts | State preserved and backup readable | 22 state files remained; archive checksum passed; prior commit/image metadata saved | Passed |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|---|---|---:|---|
| 2026-07-13 11:43 | Unit tests instantiated loggers in the production log mount and created four fixture files | 1 | Removed only the exact fixture files, confirmed zero remained, original production log count returned to 16, and remote Git stayed clean |
| 2026-07-13 12:13 | Test shell cleanup used reserved zsh variable `status`, masking the unittest exit code after output already showed six failures | 1 | Future test wrapper uses `test_rc`; the failed assertions are being replaced before rerun |
| 2026-07-13 12:15 | Wave-selection helper was initially inserted into `ReservationPlaceGate` because the patch matched the first `_candidate_key` method | 1 | Moved the helper to `SmartBookingBotV2`; four targeted tests and the full suite then passed |
| 2026-07-13 12:20 | A shell inspection loop used zsh's reserved `path` variable and lost command lookup within that subprocess | 1 | Reran with a safe variable name and absolute commands; confirmed all fragment/cache directories predate this session |
| 2026-07-13 12:22 | A process-command probe used malformed `tr` quoting and stopped the first verification script after the already-passed HTTP/container checks | 1 | Replaced it with `docker top`; process, backup, state, Nginx, and log checks passed |
| 2026-07-13 12:23 | Local macOS `curl` hit a synthetic `198.18.2.185` resolver path and failed its TLS data exchange | 1 | OpenSSL verified the certificate locally; an Aliyun-origin public HTTPS probe returned `200/401` and served the v3.5 JavaScript markers |

## Files Created or Modified

- `docs/plans/booking_reliability_task_plan_2026_07_13_114500.md`
- `docs/plans/booking_reliability_findings_2026_07_13_114500.md`
- `docs/plans/booking_reliability_progress_2026_07_13_114500.md`
- `enhanced_book_smart_v2.py`
- `web_console.py`
- `easyserp_client.py`
- `web/index.html`
- `web/app.js`
- `tests/test_fast_booking_modes.py`
- `tests/test_exact_booking.py`
- `tests/test_scan_booking.py`
- `docs/releases/booking_engine_v3.5.0.md`

## 5-Question Reboot Check

| Question | Answer |
|---|---|
| Where am I? | Complete |
| Where am I going? | Observe the next real release-time run before further concurrency tuning |
| What's the goal? | Improve booking reliability and observability while preserving the verified API contract |
| What have I learned? | See `booking_reliability_findings_2026_07_13_114500.md` |
| What have I done? | v3.5 is tested, tagged, published, backed up, deployed, and verified without a real booking or cancellation |
