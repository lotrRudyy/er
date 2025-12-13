$ErrorActionPreference = "Stop"

# Always run from repo root
$repoRoot = (git rev-parse --show-toplevel).Trim()
Set-Location $repoRoot

# Pre-change snapshot if dirty
git diff --quiet
$dirtyWorktree = ($LASTEXITCODE -ne 0)
git diff --cached --quiet
$dirtyIndex = ($LASTEXITCODE -ne 0)

if ($dirtyWorktree -or $dirtyIndex) {
  git add -A
  git commit -m "chore: pre-codex snapshot"
}

$baseCommit = (git rev-parse HEAD).Trim()

# Run Codex (passes through any args)
codex @args

# Commit any changes Codex made
git diff --quiet
if ($LASTEXITCODE -ne 0) {
  git add -A
  git commit -m "wip: codex changes"
}

# Squash into one commit
git reset --soft $baseCommit
git commit -m "feat: codex update"
