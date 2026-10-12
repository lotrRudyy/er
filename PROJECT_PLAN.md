# ER1 Dashboard Reliability And Operations Plan

## 1. Project Goal

Keep ER1 game control fully usable on the local LAN, make operator actions and
player events traceable, prevent time-edit side effects, align the control-room
layout with the supplied references, and deliver the game summary reliably.

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
- Hint templates are editable in Off phase as well as Maintenance. The current
  German production inventory is synchronized once into English and Italian;
  later free-text saves do not invoke automatic translation.
- Lighting and lock controls follow the supplied reference composition and
  visual states.
- Each riddle card places icon/name at the left of the same row as Solve, Hints,
  and Skip. In Maintenance, Prison, Wheel, Chains, Tangram, and Magnet are not
  shown as active-riddle cards.
- The phase/riddle column aligns with the phase panel; lighting and logs move
  left to preserve the reference widths. Tabs, buttons, spacing, typography,
  booking, lighting, and lock cards are matched responsively to the references.
- Room 2 and Room 3 render below the three main locks. Open rooms offer Close;
  closed rooms offer Open. Unknown lock/light state is shown as unknown.
- Eight active physical nodes render as two columns of large status-colored
  reboot buttons labeled `Node name (Xs)`.
- In Off and Maintenance, actions in the Game Control tab run without a confirm
  dialog. During Preparation/Live, existing confirmations remain and node
  reboots require confirmation. Database/game/log deletion always confirms.
- The control log records meaningful player events, operator actions, state
  transitions, connection loss/recovery, and errors. Raw heartbeat samples are
  not emitted as individual log rows. Both the Game Control log and Admin live/
  archive logs are searchable; optional auto-scroll and unclipped entry counts
  are available.
- Proxied operator actions carry a cryptographically verified website-admin
  identity. Logs include actor, action, target, old/new state, run/phase/booking,
  operation ID, duration, result, and sanitized errors. Unsigned LAN actions are
  labeled local/unknown rather than attributed to an admin.
- Connection loss is debounced so one failed poll does not flash a transient
  message. User-facing transient errors are rendered in the log instead of as
  unanchored white/black viewport text.
- The top ER1 `Menu` button opens and closes the responsive tab navigation with
  correct keyboard and accessibility state.
- Thirty minutes after a fully persisted completion, the summary email is sent
  automatically unless that run was already sent manually. Durable claim/status
  and retries prevent duplicates while leaving a 30-minute correction window.
- Current German hint templates and timing changes are synchronized once into
  English and Italian. Future free-text saves are not translated automatically.
- Deleting a saved game reliably moves it to the removed-games database without
  relying on a browser-native confirmation dialog.
- The ER1 tabs remain visible at every viewport. A separate Menu links to every
  website `/admin` page with absolute top-level navigation, so it works both on
  the LAN and inside the website iframe.
- Maintenance omits Sissi from the live-riddle cards without changing Sissi in
  the all-riddles table or its real game phase.
- Dimmable room LEDs use controller telemetry as the displayed truth whenever an
  operator is not actively dragging a slider. Their full icon/name button toggles
  to real off/zero and back to the last positive percentage, while its background
  continuously represents the actual zero-to-100-percent brightness.
- The four upper lighting buttons show their complete labels above their icons.
- During Preparation and Live, the lock-control node (`maglock`) cannot be
  rebooted; the bulk action that includes it is also unavailable. Other physical
  nodes remain rebootable.
- Operator logs identify the concrete action, target, requested state and domain
  result. Signed website actions include the admin actor; direct LAN actions stay
  `local/unknown`.
- Hint cards are larger and scroll within a bounded list when necessary. A newly
  due hint receives one short emphasis, never a continuously repeating blink.
- Node hover text includes device uptime as `DD:HH:MM:SS`, using heartbeat uptime,
  and the all-riddles table is visually centered except for the riddle name.

## 3. Scope And Exclusions

Included log sources: normalized MQTT logs and player events, meaningful state
transitions, server-side operator outcomes, connection incidents, and Dashboard
warnings/errors. Raw heartbeat samples, serial-only diagnostics, and unbounded
debug streams are excluded. Authentication and automatic hint delivery are also
excluded.

No firmware change is planned. Correlated device acknowledgements are a separate
project. The earlier code deployment and one-time production hint import were
completed after the isolated local browser session. The UI/control follow-up was
subsequently authorized, pushed, and deployed on 2026-10-12.

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
- Use one event taxonomy for player, operator, system, and connectivity records;
  archive once server-side and merge relevant records into the live control log.
- Start summary delivery from durable completion state after a 30-minute delay.
  Persist one claim/result per run; a successful manual send satisfies the same
  claim and suppresses automatic delivery.
- Use responsive CSS/layout changes rather than proxy-side HTML injection.
- Build the website menu from the configured website API origin and a fixed
  server-side route model. Use absolute `_top` links for website pages and keep
  local ER1 tabs independent so proxy response rewriting cannot corrupt them.
- Translate dimmable off/zero requests to physical off commands and let retained
  controller telemetry own displayed brightness outside an active slider edit.
- Enforce the `maglock`/bulk reboot restriction in the API and mirror it in the
  browser; frontend disabling is never the security boundary.

## 5. Constraints

- Preserve persisted runs, bookings, command receipts, and existing old files.
- Keep the deployed Flask service single-process unless log ownership is later
  redesigned for multiple workers.
- Local Dashboard state and controls cannot depend on Internet availability.
- Production phase must never be changed automatically.
- Summary claims, stable message IDs, and persisted retries prevent ordinary
  duplicate sends. SMTP has one unavoidable uncertainty window if the provider
  accepts a message and the process dies before persisting success; without a
  provider idempotency API, retrying favors delivery over a strict at-most-once
  guarantee.

## 6. Implementation Steps

1. Remove time undo end-to-end, preserve total time during every riddle edit,
   and decouple Prison/Wheel status from counted results.
2. Implement phase-sensitive confirmation rules and accurate unknown hardware
   states.
3. Restructure the Game Control layout, booking, riddles, Candles, Stars,
   lighting, locks, nodes, Maintenance visibility, and responsive menu.
4. Unify operator/player/connectivity logging, debounce connection incidents,
   and add search plus optional auto-scroll.
5. Add duplicate-safe automatic summary delivery after 30 minutes with manual
   early-send status/retry.
6. Synchronize current German hints/timings into English and Italian.
7. Run focused and full tests, then the approved isolated local visual session.
8. Separate permanent ER1 tabs from the website-wide admin menu and fix
   Maintenance Sissi visibility.
9. Rework light controls around authoritative telemetry and enforce the
   lock-control reboot rule.
10. Expand operator action taxonomy, hint readability, node uptime, and table
    alignment, then run focused and full tests.

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
- One failed state poll causes no visible disconnect flash; sustained loss and
  recovery each create one useful log record.
- Summary email sends 30 minutes after completion unless sent manually; transient
  failure can retry without accidental duplication.
- Desktop/tablet/mobile layouts match the supplied hierarchy without clipped
  controls, overlapping cards, or nested-scroll traps.
- Website admin navigation is reachable from direct and proxied ER1 views while
  ER1 tabs always remain visible.
- External/game-driven LED changes update button, slider and percentage; off is
  always zero and on restores the last positive setting.
- `maglock` and the bulk reboot are rejected in Preparation/Live while every
  other valid individual physical node remains rebootable.
- Command logs name the actual action/target/result and do not misattribute an
  unsigned LAN user.
- Hint due emphasis runs once, node uptime is readable, and table contents remain
  centered without breaking mobile layouts.
- Full automated test and syntax suites pass.

## 8. Decisions

- Fixed two-digit minute entry, then automatic seconds selection: accepted.
- JSONL, 30 days, 25-MiB rollover, 256-MiB cap: accepted.
- Hint editor plus due marking, no automatic sending: accepted.
- Independent Prison and Wheel operator status, excluded from counted results:
  accepted; this supersedes the Chains group-outcome decision.
- Remove time undo and guarantee invariant total game time during every manual
  riddle-time edit: accepted; this supersedes original-time restoration.
- Current German hints/timings are translated once by the implementation into
  English and Italian. Automatic translation of future edits is not included.
- Phase 0/1 Game Control actions do not confirm. Preparation/Live keeps current
  confirmations, including node reboot. Database/game/log deletion always
  confirms.
- Summary email automatically sends after 30 minutes unless already sent
  manually; both paths share durable duplicate prevention.
- Firmware acknowledgements remain a separate project; no node firmware changes
  are part of this implementation.
- Chess remains unchanged.
- Direct ER1 actions remain `local/unknown`; no new local login or operator picker
  is introduced.
- ER1 tabs are permanent. Menu controls website-wide admin navigation instead of
  hiding/showing those tabs.
- Controller telemetry is authoritative for displayed LED brightness. No phase
  scene or firmware brightness values are changed.
- The `maglock` physical node and any bulk reboot containing it are blocked only
  during Preparation/Live; other individual physical nodes remain available.

## 9. Ideas Parking Lot

- Proposed: authenticate Admin actions and record trustworthy operator identity.
- Accepted separate project: correlated MQTT command acknowledgement. Estimate:
  10-20 person-days for a low-risk proof of concept, 30-50 for all active nodes,
  and 45-70 for fully hardened QoS/reboot/OTA fault handling.
- Proposed: compressed closed log segments.
- Proposed: automatic hint delivery with durable acknowledgment.

## 10. Current Status And Next Step

Production runs the operational dashboard files from commit `9b94c410`, deployed
on 2026-10-12. The current rollback archive is
`/home/rudyy/er1/deploy-backups/pre-9b94c410-20261012T135115Z.tar.gz`.
Steps 1-7 are implemented and independently reviewed. The final ER1 suite passes
167 tests; JavaScript syntax and diff checks pass. An
isolated headless-Chromium review used 17 of the approved 18 renderings across
desktop, tablet, 500-pixel mobile, and true 390-pixel emulation. No tested view
had horizontal document overflow; the 1024-pixel ER1 layout now uses the compact
responsive flow. Both ER1 services, Dashboard HTTP, read-only archive API, MQTT
phase-0 state, deployed hashes, and the new summary-delivery database were
verified after restart. The website/ER1 audit secrets match without being stored
in Git. The one-time hint candidate was imported on 2026-10-12 through completed
idempotent operation `72ec73a1-52b5-41cc-88e8-2f1db17da912`; its persisted hash is
`24e1b2e707a828e5f6b25d11dfafdc381f192953c8dd57ce30965f8727293771`.
The immediate rollback copy is
`/home/rudyy/er1/deploy-backups/hint_templates.pre-import-20261012T121859Z.json`
with source hash `1ad31bd0ad79ba80444a08591e2a485cbaab5db8ffd78b9d0015d3139d9c2425`.
The API, operation receipt, file permissions, service health, phase 0, empty run,
and translated hint cardinalities were verified after import. All temporary
servers, databases, profiles, staging files, and screenshots were removed. The
follow-up covering navigation, lighting, reboot policy, detailed logs, hints,
uptime, and table layout passes all 170 offline tests plus Python/JavaScript
syntax checks. Its staging hashes matched the committed files, Python and Jinja
syntax passed on the Pi, `er1-web.service` and `game_master.service` are active,
and dashboard, games, and state HTTP checks return 200. The retained state after
restart is Maintenance without an active run or timer. The follow-up is pushed
and deployed but not yet visually browser-verified.
