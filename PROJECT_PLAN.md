# ER1 Dashboard Reliability And Operations Plan

## 1. Project Goal

Keep ER1 game control fully usable on the local LAN, remove start and time-edit
race conditions, align booking and riddle behavior with operator expectations,
and provide reliable game-history and bounded log-archive operations.

## 2. Functions And Behavior

- Start is enabled as soon as phase 2 has an authoritative run ID. Booking and
  ranking network work must never block local monitoring or game controls.
- Confirmed Start keeps the existing five-second countdown. Preparation has no
  running timer.
- Exactly one compact riddle-time editor can be open. It survives live snapshots,
  uses a large centered `MM:SS` input, shows the affected previous riddle as
  `Name: old -> new`, places Save directly beside it, and submits on Enter.
- Manual riddle-time edits must never change total game time. The time undo
  feature is removed completely.
- Time inputs use fixed `MM:SS`. Focus selects minutes; two typed minute digits
  select seconds. The same duration editor is used for hint thresholds.
- Remove the `Phase, Zeit und Buchung` heading and move `Phase X - Name` upward.
  The booking card uses three compact columns: date/time, name, email on the
  left; players and language in the middle; a centered icon plus `Buchung
  ändern` action on the right.
- Candles live data shows `Lösung: 2413`.
- Stars live data shows a seven-row Name/Symbol/Star-count table on the left.
  On the right, solution and current positions use symbols only, correct current
  positions are green, and comma-separated attempts are manually scrollable
  without automatic downward scrolling.
- Prison and Wheel remain timed progression stages but their operator outcome
  can be changed independently like other riddles. Their outcome does not count
  toward the authoritative solved-riddle result.
- Booking choices contain only eligible confirmed appointments on the current
  Europe/Rome calendar day. Cancelled, deleted, completed, malformed,
  unconfirmed, refunded, and failed appointments are excluded.
- Hint thresholds are labeled `Seit Spielstart` and `Seit Raetselstart`, use
  `MM:SS`, and mark a hint due when either configured threshold is reached.
  Hints are not sent automatically.
- Admin exposes durable Dashboard log history and a maintained node/log-level
  capability reference.
- The control-page log is searchable inside a bounded, manually scrollable
  container.
- Hint templates are editable in Off phase as well as Maintenance. Saving the
  German text also translates and stores English and Italian versions.
- Lighting and lock controls follow the supplied reference composition and
  visual states.
- Deleting a saved game reliably moves it to the removed-games database without
  relying on a browser-native confirmation dialog.

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
- Keep riddle-time edits server-authoritative while preserving total game time;
  do not expose a restore/undo mutation.
- Keep Prison and Wheel outcome controls independent while excluding their
  outcome from the solved-result calculation.
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
2. Add completed-booking filtering and hint due-state calculation/rendering.
3. Add the JSONL sink, operator/server integration, archive APIs, Admin archive
   UI, retention controls, and node-level capability reference.
4. Repair game deletion first, with a database backup and focused regression
   tests before deployment.
5. In a separate revision, remove time undo and implement the queued control,
   booking, riddle-live-data, same-day booking, hint, and log UI changes.
6. Add focused unit/source tests, then run full Python/Node/syntax gates.
7. Perform visual browser verification only after separate action-specific
   approval.

## 7. Success Criteria

- Start remains enabled during a pending/failed booking refresh and begins the
  game on the existing five-second countdown.
- Local control and `/api/state` remain usable when website/ranking hosts fail.
- Time and booking interactions match the agreed desktop/mobile behavior.
- A riddle-time edit never changes total game time, and no undo control or
  restore endpoint remains.
- Prison and Wheel can be marked independently without changing the counted
  solved-riddle result.
- Only eligible bookings from the current Europe/Rome day appear in fresh or
  cached lists.
- Due hints are deterministic and never auto-send.
- Logs survive restart, remain within age/size bounds, are safely searchable and
  maintainable in Admin, and failures never stop game control.
- Full automated test and syntax suites pass.

## 8. Decisions

- Fixed two-digit minute entry, then automatic seconds selection: accepted.
- JSONL, 30 days, 25-MiB rollover, 256-MiB cap: accepted.
- Hint editor plus due marking, no automatic sending: accepted.
- Independent Prison and Wheel operator status, excluded from counted results:
  accepted; this supersedes the Chains group-outcome decision.
- Remove time undo and guarantee invariant total game time during every manual
  riddle-time edit: accepted; this supersedes original-time restoration.
- Automatic German-to-English/Italian hint translation is required. The
  translation mechanism and failure behavior remain an implementation decision
  for that later revision.

## 9. Ideas Parking Lot

- Accepted, queued after game deletion: implement the compact phase/booking
  hierarchy, larger time editor, Candles solution, symbol-based Stars table and
  attempt list, same-day bookings, independent Prison/Wheel controls, reference
  lighting/lock cards, Off-phase hint editing with DE-to-EN/IT translation, and
  searchable scrollable control logs. The supplied screenshots are the visual
  target for spacing, typography, panel density, state colors, and control sizes.
- Proposed: authenticate Admin actions and record trustworthy operator identity.
- Proposed: firmware acknowledgment of active log level.
- Proposed: compressed closed log segments.
- Proposed: automatic hint delivery with durable acknowledgment.

## 10. Current Status And Next Step

Commit `2ec80dbb` and the isolated saved-game deletion repair are deployed. The
repair has a fresh production backup and passes the full 148-test Python suite.
All other newly accepted behavior and visual changes above remain queued for a
separate revision.
