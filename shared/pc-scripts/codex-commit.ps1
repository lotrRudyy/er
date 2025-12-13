$ErrorActionPreference = "Stop"

# Ensure git repo
git rev-parse --is-inside-work-tree *> $null

function Invoke-CommitIfChanged($msg) {
  $hasChanges = $false

  git diff --quiet
  if ($LASTEXITCODE -ne 0) { $hasChanges = $true }

  git diff --cached --quiet
  if ($LASTEXITCODE -ne 0) { $hasChanges = $true }

  if ($hasChanges) {
    git add -A
    try { git commit -m $msg } catch {}
  }
}

Invoke-CommitIfChanged "codex: snapshot before run"
codex @args
Invoke-CommitIfChanged "codex: snapshot after run"
