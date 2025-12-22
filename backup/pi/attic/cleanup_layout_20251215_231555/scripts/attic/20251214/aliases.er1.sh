#!/usr/bin/env bash
# Source this file manually to load ER1 helper aliases.

alias log_live="scripts/mqtt-logs.sh live"
alias log_tail="scripts/mqtt-logs.sh tail"
alias log_grep="scripts/mqtt-logs.sh grep"
alias log_help="scripts/mqtt-logs.sh help"

alias lock_open="scripts/mqtt-locks.sh open"
alias lock_close="scripts/mqtt-locks.sh close"

_er1_lock_ids="images r2 r3 slider knocking"
_er1_complete_locks() {
  COMPREPLY=( $(compgen -W "$_er1_lock_ids" -- "${COMP_WORDS[COMP_CWORD]}") )
}
complete -F _er1_complete_locks lock_open
complete -F _er1_complete_locks lock_close
