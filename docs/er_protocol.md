ER Protocol (Hard Truths)
1. System Model

The ER system is a distributed node-based system

Physical rooms are not a first-class concept

Architecture is defined by nodes, responsibilities, and topics

2. Communication

All inter-node communication uses MQTT

Nodes do not communicate directly by any other means

MQTT is not a queue; it is a state + event bus

3. Message Classes (Closed Set)

Exactly these message classes exist:

cmd — request an action

evt — something happened

state — current truth (retained)

hb — periodic health snapshot

log — concise, meaningful, human-readable logs

dbg — noisy or high-frequency debug output

cfg — desired configuration (retained)

No additional message classes are allowed.

4. Truth vs History

State represents truth, not history

Events are not replayed to rebuild state

Debug output is never required for correctness

If correctness depends on it, it is not dbg

5. Logging Rules

Logs exist to help humans detect and understand problems

Logging raw data without interpretation in log is forbidden

High-volume or raw data belongs only in dbg

Absence of logs must itself be meaningful

6. Firmware Structure

Firmware is structured by responsibility, not location

Entry points are explicit and single-purpose

Hardware access, coordination, and puzzle logic are separated

Cross-layer shortcuts are forbidden

7. Naming

Naming is normative, not stylistic

Naming rules defined in docs/naming.md are mandatory

Topic names, binaries, services, files, and folders must align

8. Scripts and Tooling

Deployment and tooling live under:

er/scripts/


pc-scripts no longer exists

Scripts are ER-wide unless explicitly ERx-specific

Scripts must not encode ER1-only assumptions unless documented

9. Forbidden Concepts

The following concepts are explicitly forbidden:

Physical “rooms” as an architectural or documentation unit

Encoding layout or geography into logic

Ad-hoc message types

Using MQTT as a command queue or database

10. Authority

If there is a conflict between:

this file

any other ER doc

code comments

or developer preference

This file wins.

11. Change Control

Changes to this file are rare and intentional

Any change requires updating affected documentation

Violating this protocol is considered a regression
12. Node Migration Status

**maglock:** Fully migrated to canonical ER1 topics (fw 1.4+); legacy `maglock/lock/+/cmd` commands accepted with deprecation warnings until further notice.