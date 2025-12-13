$ErrorActionPreference = "Stop"

# Ensure git repo
git rev-parse --is-inside-work-tree *> $null

function Commit-IfChanged($msg) {
  $hasChanges = $false
  git diff --quiet; if ($LASTEXITCODE -ne 0) { $hasChanges = $true }
  git diff --cached --quiet; if ($LASTEXITCODE -ne 0) { $hasChanges = $true }

  if ($hasChanges) {
    git add -A
    try { git commit -m $msg } catch {}
  }
}

Commit-IfChanged "codex: snapshot before run"

# Run codex with all passed args
codex @args

Commit-IfChanged "codex: snapshot after run"
