# ER1 Dashboard Reliability And Operations Plan

## 1. Project Goal

Keep ER1 game control fully usable on the local LAN, remove start and time-edit
race conditions, align booking and riddle behavior with operator expectations,
and add a bounded maintainable Dashboard log archive.

## 2. Functions And Behavior

- Start is enabled as soon as phase 2 has an authoritative run ID. Booking and
  ranking network work must never block local monitoring or game controls.
- Confirmed Start keeps the existing five-second countdown. Preparation has no
  running timer.
- Exactly one riddle-time editor can be open. It survives live snapshots while
  open, keeps the selected adjustment until Save, and shows Previous above Next.
- The measured time before the first manual correction and the cumulative
  correction relationships remain durable. An undo button beside the time gear
  restores that baseline without discarding later independent corrections.
- Time inputs use fixed `MM:SS`. Focus selects minutes; two typed minute digits
  select seconds. The same duration editor is used for hint thresholds.
- The current-booking card follows the supplied desktop reference layout.
- The Stars live box always shows the full solution text above Current:
  `Waage ♎ (7), Wassermann ♒ (13), Skorpion ♏ (10), Zwillinge ♊ (7),`
  `Schütze ♐ (10), Löwe ♌ (10), Fische ♓ (13)`.
- Prison and Wheel remain timed progression stages. Chains alone decides the
  shared outcome: solved Chains marks all three solved; otherwise all three are
  skipped. Existing individual times and hint counts remain intact.
- Booking choices contain only confirmed appointments that are still upcoming
  or whose one-hour slot is currently running in Europe/Rome. Cancelled,
  deleted, completed, malformed, unconfirmed, refunded, failed, and ended
  appointments are excluded.
- Hint thresholds are labeled `Seit Spielstart` and `Seit Raetselstart`, use
  `MM:SS`, and mark a hint due when either configured threshold is reached.
  Hints are not sent automatically.
- Admin exposes durable Dashboard log history and a maintained node/log-level
  capability reference.

## 3. Scope And Exclusions

Included log sources: normalized MQTT node logs shown by the Dashboard,
server-side operator mutation outcomes, and Dashboard WARNING/ERROR/CRITICAL
records. Heartbeat/state/event traffic, serial-only diagnostics, and Game Master
journal ingestion are excluded. Authentication and automatic hint delivery are
also excluded.

No firmware change is planned. No commit, push, browser action, or deployment is
implicit in implementation; those require their normal explicit boundaries.

## 4. Chosen Approach

- Separate booking-list loading from booking mutation state in the browser.
- Use one native auto popover and bind edit protection to its open lifetime.
- Keep backend time parsing compatible while presenting a strict integer
  `00:00` through `99:59` editor.
- Store the first pre-correction value and bounded outgoing adjustment deltas in
  the authoritative run/checkpoint and restore them through an idempotent Game
  Master command. A later direct edit supersedes older incoming adjustments.
- Enforce the three-riddle outcome invariant in Game Master code, not only UI.
- Filter booking eligibility server-side with shared Europe/Rome rules.
- Store logs as bounded daily/25-MiB JSONL segments in an environment-configured
  directory. Retain 30 days, enforce a 256-MiB aggregate cap, and never delete
  the active segment.
- Query/download/purge logs only through server-generated segment IDs. Purge is
  limited to closed segments and maintenance phase with confirmation.
- Queue MQTT archive writes off the Paho callback, fsync JSONL batches and
  directory changes, expose queue failures, and cap each search snapshot at the
  newest 32 MiB. Individual segments remain searchable and downloadable.

## 5. Constraints

- Preserve persisted runs, bookings, command receipts, and existing old files.
- Keep the deployed Flask service single-process unless log ownership is later
  redesigned for multiple workers.
- Local Dashboard state and controls cannot depend on Internet availability.
- Production phase must never be changed automatically.

## 6. Implementation Steps

1. Fix Start gating, popover lifetime/exclusivity, segmented duration input,
   booking card, Stars solution, and cache versions.
2. Persist and expose original riddle times and add server-authoritative undo.
3. Enforce Chains group outcomes in Game Master and adapt Dashboard controls.
4. Add completed-booking filtering and hint due-state calculation/rendering.
5. Add the JSONL sink, operator/server integration, archive APIs, Admin archive
   UI, retention controls, and node-level capability reference.
6. Add focused unit/source tests, then run full Python/Node/syntax gates.
7. Perform visual browser verification only after separate action-specific
   approval.

## 7. Success Criteria

- Start remains enabled during a pending/failed booking refresh and begins the
  game on the existing five-second countdown.
- Local control and `/api/state` remain usable when website/ranking hosts fail.
- Time and booking interactions match the agreed desktop/mobile behavior.
- The first measured riddle time survives later edits and restart and can be
  restored from the adjacent undo control.
- Prison/Wheel/Chains outcomes cannot diverge in new or corrected runs.
- Ineligible past bookings never appear in fresh or cached lists.
- Due hints are deterministic and never auto-send.
- Logs survive restart, remain within age/size bounds, are safely searchable and
  maintainable in Admin, and failures never stop game control.
- Full automated test and syntax suites pass.

## 8. Decisions

- Fixed two-digit minute entry, then automatic seconds selection: accepted.
- JSONL, 30 days, 25-MiB rollover, 256-MiB cap: accepted.
- Hint editor plus due marking, no automatic sending: accepted.
- Chains is authoritative for the Prison/Wheel/Chains outcome: required.
- Original riddle time means the value before the first manual correction; it
  is not overwritten by later corrections or by restoring it: accepted.

## 9. Ideas Parking Lot

- Accepted, deferred: align the control-room visuals with the supplied reference
  screenshots in a separate change. Preserve the compact booking hierarchy
  (date/time and players; name and language; email; outlined edit action), use
  larger dark lighting scene tiles with clear active orange/purple states,
  separate slider labels/value percentages/actions, and render lock controls as
  larger neutral/red/green state cards with Room 2 closed and Room 3 open styling.
  The supplied full-dashboard reference is the composition target for spacing,
  typography, panel density, tables, current-riddle/tip area, nodes, and log.
- Proposed: authenticate Admin actions and record trustworthy operator identity.
- Proposed: firmware acknowledgment of active log level.
- Proposed: compressed closed log segments.
- Proposed: automatic hint delivery with durable acknowledgment.

## 10. Current Status And Next Step

Implementation is complete locally. The full automated gate passes with 146
Python tests and 7 Node tests, plus Python/JavaScript syntax and whitespace
checks. Visual browser verification, commit, push, and deployment remain pending
their separate explicit approvals.
