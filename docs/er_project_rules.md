ER Project – AI Operating Rules (Canonical)
1. Source of truth

The uploaded repository (zip / files) is the only source of truth

Chats are never the source of truth

If something isn’t in the repo or explicitly pasted, it does not exist

2. Chat scope

One chat = one task

If task changes → new chat

No multi-riddle, multi-device, or meta discussions in execution chats

3. Two modes only
MODE: DESIGN

Used for:

algorithms

thresholds

logic discussion

calibration strategy

Rules:

No full sketches

No refactors

No assumptions about final implementation

MODE: EXECUTION

Used for:

modifying firmware

generating code

fixing bugs

Rules:

The user pastes the exact base file

Start ONLY from that file

Change ONLY what the user requests

No cleanup, no refactor, no improvements unless asked

Always generate the FULL updated file

Output as downloadable .cpp

4. Firmware handling rules

Always start from the last working version provided

Never invent a new baseline

Never merge logic from memory or older chats

If unsure which version is current → stop and ask

5. Versioning

Every firmware has:

FW_VERSION (numeric, used in logs/MQTT)

FW_DESC (human readable)

FW_VERSION must always increment on any change

6. Architecture invariants (unless user explicitly changes them)

ESP32 + W5500

Ethernet only (no WiFi)

Static IP

MQTT + OTA

FSM, non-blocking loop

Centralized maglock control

Logs are compact JSON

If breaking an invariant → must be explicitly requested.

7. Logging & diagnostics

Log only what helps debugging

No spam unless in DEV mode

When logs are shown, assume they may be saved and replayed

8. Memory discipline

Do NOT rely on long-term memory across chats

Assume every chat starts cold

Important decisions must be:

written into repo docs, or

pasted again when needed

9. Error handling

If something is unclear:

Stop

Ask one precise question

Do not guess

Wrong code is worse than no code.

10. User authority

The user’s pasted code > everything else

The user’s explicit instruction overrides all defaults

If instructions conflict → point it out, don’t choose silently

11. Output discipline

No unnecessary explanations in execution mode

No emotional language

No “helpful improvements”

Exact compliance beats cleverness

Recommendation (important)

Create two permanent chats:

ER1-CORE → repo, architecture, rules, invariants

ER1-TASK-X → one task, then discarded

This keeps performance high forever.
