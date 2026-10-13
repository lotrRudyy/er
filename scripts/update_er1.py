#!/usr/bin/env python3
"""Safely update the ER1 Python runtimes from a committed local checkout."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


MARKER_NAME = ".deploy-source-commit"
DEFAULT_TARGET = "rudyy@100.108.1.80"
DEFAULT_REMOTE_ROOT = "/home/rudyy/er1"
SAFE_TARGET_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.:-]+$")
SAFE_REMOTE_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
SAFE_REPO_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManagedFile:
    repo_path: str
    remote_path: str
    blob: str


@dataclass(frozen=True)
class UpdateAction:
    repo_path: str
    remote_path: str
    action: str
    old_blob: str | None
    new_blob: str | None


def run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise UpdateError(f"Command failed ({result.returncode}): {' '.join(args)}\n{detail}")
    return result


def git(repo_root: Path, *args: str, check: bool = True) -> str:
    return run(("git", *args), cwd=repo_root, check=check).stdout.strip()


def validate_destination(target: str, remote_root: str) -> None:
    if not SAFE_TARGET_RE.fullmatch(target):
        raise UpdateError(f"Unsafe SSH target: {target!r}")
    if (
        not SAFE_REMOTE_RE.fullmatch(remote_root)
        or remote_root in {"/", "/home", "/home/rudyy"}
        or ".." in PurePosixPath(remote_root).parts
    ):
        raise UpdateError(f"Unsafe remote root: {remote_root!r}")
    if remote_root != DEFAULT_REMOTE_ROOT:
        raise UpdateError(
            f"ER1 update only supports the production root {DEFAULT_REMOTE_ROOT!r}"
        )


def map_repo_path(repo_path: str) -> str | None:
    if not SAFE_REPO_PATH_RE.fullmatch(repo_path):
        raise UpdateError(f"Unsupported repository path: {repo_path!r}")

    game_master_prefix = "scripts/game_master/"
    if repo_path.startswith(game_master_prefix):
        relative = repo_path[len(game_master_prefix) :]
        if relative.endswith(".py") or relative == "requirements.txt":
            return f"scripts/game_master/{relative}"
        return None

    dashboard_prefix = "web/er1_dashboard/"
    if not repo_path.startswith(dashboard_prefix):
        return None

    relative = repo_path[len(dashboard_prefix) :]
    if relative.endswith((".bak", ".orig")):
        return None
    if relative in {
        "app.py",
        "log_archive.py",
        "hint_templates.defaults.json",
        "requirements.txt",
    }:
        return f"web/{relative}"
    if relative.startswith("templates/"):
        return f"web/{relative}"
    if relative.startswith("static/") and not relative.endswith(".bak"):
        return f"web/{relative}"
    return None


def manifest_for_commit(repo_root: Path, commit: str) -> dict[str, ManagedFile]:
    output = git(
        repo_root,
        "ls-tree",
        "-r",
        "--name-only",
        commit,
        "--",
        "scripts/game_master",
        "web/er1_dashboard",
    )
    manifest: dict[str, ManagedFile] = {}
    remote_paths: set[str] = set()
    for repo_path in sorted(line for line in output.splitlines() if line):
        remote_path = map_repo_path(repo_path)
        if remote_path is None:
            continue
        if remote_path in remote_paths:
            raise UpdateError(f"Multiple sources map to {remote_path}")
        blob = git(repo_root, "rev-parse", f"{commit}:{repo_path}")
        manifest[repo_path] = ManagedFile(repo_path, remote_path, blob)
        remote_paths.add(remote_path)
    if not manifest:
        raise UpdateError(f"No managed ER1 runtime files found at {commit}")
    return manifest


def build_update_plan(
    base: dict[str, ManagedFile],
    head: dict[str, ManagedFile],
) -> list[UpdateAction]:
    actions: list[UpdateAction] = []
    for repo_path in sorted(set(base) | set(head)):
        old = base.get(repo_path)
        new = head.get(repo_path)
        if old and new and old.blob == new.blob and old.remote_path == new.remote_path:
            continue
        if old and new and old.remote_path != new.remote_path:
            actions.append(
                UpdateAction(repo_path, old.remote_path, "delete", old.blob, None)
            )
            actions.append(
                UpdateAction(repo_path, new.remote_path, "write", None, new.blob)
            )
        elif new:
            actions.append(
                UpdateAction(
                    repo_path,
                    new.remote_path,
                    "write",
                    old.blob if old else None,
                    new.blob,
                )
            )
        elif old:
            actions.append(
                UpdateAction(repo_path, old.remote_path, "delete", old.blob, None)
            )
    return actions


def assert_dashboard_idle_state(state: dict[str, object]) -> None:
    game = state.get("game")
    if not isinstance(game, dict):
        raise UpdateError("Dashboard state has no game object")
    required = {"phase", "timer_running", "is_live", "run_id"}
    missing = sorted(required - set(game))
    if missing:
        raise UpdateError("Dashboard state is incomplete: " + ", ".join(missing))
    if (
        game.get("phase") != 0
        or game.get("timer_running") is not False
        or game.get("is_live") is not False
        or bool(game.get("run_id"))
    ):
        raise UpdateError(
            "ER1 update refused: game is not idle "
            f"(phase={game.get('phase')}, timer={game.get('timer_running')}, "
            f"live={game.get('is_live')}, run_id={game.get('run_id')!r})"
        )
    meta = state.get("meta")
    if not isinstance(meta, dict) or "persistence_degraded" not in meta:
        raise UpdateError("Dashboard persistence state is unavailable")
    if meta.get("persistence_degraded") is not False:
        raise UpdateError("ER1 persistence is degraded")


def assert_game_master_idle_state(state: dict[str, object]) -> None:
    required = {"phase", "timer_running", "run", "recovery"}
    missing = sorted(required - set(state))
    if missing:
        raise UpdateError("Game Master state is incomplete: " + ", ".join(missing))
    recovery = state.get("recovery")
    if not isinstance(recovery, dict) or "durability_degraded" not in recovery:
        raise UpdateError("Game Master recovery state is unavailable")
    if (
        state.get("phase") != 0
        or state.get("timer_running") is not False
        or state.get("run") is not None
    ):
        raise UpdateError(
            "ER1 update refused: authoritative Game Master state is not idle "
            f"(phase={state.get('phase')}, timer={state.get('timer_running')}, "
            f"run={bool(state.get('run'))})"
        )
    if recovery.get("durability_degraded") is not False:
        raise UpdateError("Game Master persistence is degraded")


class Remote:
    def __init__(self, target: str):
        self.target = target
        self.ssh_prefix = (
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            target,
        )

    def command(self, command: str, *, check: bool = True) -> str:
        return run((*self.ssh_prefix, command), check=check).stdout.strip()

    def copy(self, source: Path, destination: str) -> None:
        result = run(
            (
                "scp",
                "-q",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                str(source),
                f"{self.target}:{destination}",
            )
        )
        if result.returncode != 0:
            raise UpdateError(result.stderr.strip() or f"Failed to copy {source}")


def q(value: str) -> str:
    return shlex.quote(value)


def read_dashboard_state(remote: Remote) -> dict[str, object]:
    raw = remote.command("curl -fsS --max-time 10 http://127.0.0.1:8080/api/state")
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as error:
        raise UpdateError(f"Invalid /api/state response: {error}") from error
    if not isinstance(state, dict):
        raise UpdateError("Invalid /api/state response type")
    return state


def read_game_master_state(remote: Remote) -> dict[str, object]:
    raw = remote.command(
        "set -eu; tmp=$(mktemp); "
        "trap 'rm -f \"$tmp\"' EXIT; "
        "mosquitto_sub -h 127.0.0.1 -t game/dashboard_state -R -C 1 -W 3 > \"$tmp\" & "
        "subscriber=$!; "
        "for delay in 0.1 0.2 0.2 0.2 0.2; do "
        "sleep \"$delay\"; kill -0 \"$subscriber\" 2>/dev/null || break; "
        "mosquitto_pub -h 127.0.0.1 -t game/cmd -m '{\"cmd\":\"get_state\"}'; "
        "done; "
        "wait \"$subscriber\"; cat \"$tmp\""
    )
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as error:
        raise UpdateError(f"Invalid retained Game Master state: {error}") from error
    if not isinstance(state, dict):
        raise UpdateError("Invalid retained Game Master state type")
    return state


def acquire_update_lock(remote: Remote, lock_path: str, owner: str) -> None:
    command = (
        f"if mkdir {q(lock_path)} 2>/dev/null; then "
        f"if ! printf '%s\\n' {q(owner)} > {q(f'{lock_path}/owner')}; then "
        f"rm -rf {q(lock_path)}; exit 74; fi; "
        "else echo 'ER1 update lock already exists' >&2; exit 73; fi"
    )
    remote.command(command)


def release_owned_update_lock(
    remote: Remote,
    lock_path: str,
    owner: str,
    *,
    check: bool,
) -> None:
    owner_path = f"{lock_path}/owner"
    remote.command(
        f"if test -f {q(owner_path)} && test \"$(cat {q(owner_path)})\" = {q(owner)}; "
        f"then rm -rf {q(lock_path)}; fi",
        check=check,
    )


def remote_hashes(
    remote: Remote,
    remote_root: str,
    relative_paths: Iterable[str],
) -> dict[str, str | None]:
    paths = sorted(set(relative_paths))
    commands: list[str] = ["set -eu"]
    for index, relative_path in enumerate(paths):
        absolute = f"{remote_root}/{relative_path}"
        commands.append(
            f"if test -f {q(absolute)}; then printf '{index}\\t'; "
            f"git hash-object {q(absolute)}; else printf '{index}\\t__MISSING__\\n'; fi"
        )
    output = remote.command("; ".join(commands))
    hashes: dict[str, str | None] = {}
    for line in output.splitlines():
        index_text, value = line.split("\t", 1)
        hashes[paths[int(index_text)]] = None if value == "__MISSING__" else value
    if set(hashes) != set(paths):
        raise UpdateError("Incomplete remote hash response")
    return hashes


def assert_remote_matches(
    remote: Remote,
    remote_root: str,
    manifest: dict[str, ManagedFile],
    *,
    additionally_absent: Iterable[str] = (),
) -> None:
    expected = {item.remote_path: item.blob for item in manifest.values()}
    absent = set(additionally_absent) - set(expected)
    actual = remote_hashes(remote, remote_root, set(expected) | absent)
    mismatches = [
        f"{path}: expected {blob}, got {actual.get(path) or 'missing'}"
        for path, blob in sorted(expected.items())
        if actual.get(path) != blob
    ]
    mismatches.extend(
        f"{path}: expected missing, got {actual[path]}"
        for path in sorted(absent)
        if actual.get(path) is not None
    )
    if mismatches:
        detail = "\n".join(f"  - {line}" for line in mismatches)
        raise UpdateError(f"Remote runtime drift detected:\n{detail}")


def validate_local_checkout(repo_root: Path) -> str:
    if git(repo_root, "rev-parse", "--is-inside-work-tree") != "true":
        raise UpdateError(f"Not a Git repository: {repo_root}")
    if git(repo_root, "status", "--porcelain", "--untracked-files=all"):
        raise UpdateError("Local checkout is dirty; commit or remove all changes first")
    branch = git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        raise UpdateError(f"ER1 updates require branch main, got {branch!r}")
    upstream = git(repo_root, "rev-parse", "--abbrev-ref", "@{u}", check=False)
    if not upstream:
        raise UpdateError("Branch main has no upstream")
    head = git(repo_root, "rev-parse", "HEAD")
    upstream_head = git(repo_root, "rev-parse", "@{u}")
    if head != upstream_head:
        raise UpdateError(f"HEAD {head} does not match {upstream} {upstream_head}")
    return head


def read_marker(remote: Remote, marker_path: str) -> str | None:
    output = remote.command(
        f"if test -f {q(marker_path)}; then cat {q(marker_path)}; fi"
    ).strip()
    if not output:
        return None
    if not re.fullmatch(r"[0-9a-f]{40}", output):
        raise UpdateError(f"Invalid deployment marker: {output!r}")
    return output


def write_marker(remote: Remote, marker_path: str, commit: str) -> None:
    temporary = f"{marker_path}.tmp"
    remote.command(
        f"printf '%s\\n' {q(commit)} > {q(temporary)} && mv {q(temporary)} {q(marker_path)}"
    )


def verify_staging(
    remote: Remote,
    staging_root: str,
    actions: Sequence[UpdateAction],
) -> None:
    writes = [action for action in actions if action.action == "write"]
    actual = remote_hashes(
        remote,
        staging_root,
        (action.remote_path for action in writes),
    )
    for action in writes:
        if actual[action.remote_path] != action.new_blob:
            raise UpdateError(f"Staging hash mismatch: {action.remote_path}")

    game_master_python = [
        f"{staging_root}/{action.remote_path}"
        for action in writes
        if action.remote_path.startswith("scripts/game_master/")
        and action.remote_path.endswith(".py")
    ]
    dashboard_python = [
        f"{staging_root}/{action.remote_path}"
        for action in writes
        if action.remote_path.startswith("web/") and action.remote_path.endswith(".py")
    ]
    javascript = [
        f"{staging_root}/{action.remote_path}"
        for action in writes
        if action.remote_path.endswith(".js")
    ]
    if game_master_python:
        remote.command(
            "/home/rudyy/er1/scripts/game_master/.venv/bin/python -m py_compile "
            + " ".join(q(path) for path in game_master_python)
        )
    if dashboard_python:
        remote.command(
            "/home/rudyy/er1/web/venv/bin/python -m py_compile "
            + " ".join(q(path) for path in dashboard_python)
        )
    for path in javascript:
        remote.command(f"node --check {q(path)}")


def verify_health(
    remote: Remote,
    remote_root: str,
    head_manifest: dict[str, ManagedFile],
    actions: Sequence[UpdateAction],
    services: Sequence[str],
    journal_since: str,
) -> None:
    statuses = remote.command(
        "systemctl is-active game_master.service er1-web.service"
    ).splitlines()
    if statuses != ["active", "active"]:
        raise UpdateError(f"ER1 services are not healthy: {statuses}")

    for path in ("/", "/games", "/admin", "/api/state"):
        code = remote.command(
            f"curl -sS -o /dev/null -w '%{{http_code}}' --max-time 10 "
            f"http://127.0.0.1:8080{path}"
        )
        if code != "200":
            raise UpdateError(f"Dashboard health check failed for {path}: HTTP {code}")

    assert_dashboard_idle_state(read_dashboard_state(remote))
    assert_game_master_idle_state(read_game_master_state(remote))
    assert_remote_matches(remote, remote_root, head_manifest)
    deleted_paths = [
        action.remote_path for action in actions if action.action == "delete"
    ]
    if deleted_paths:
        deleted_hashes = remote_hashes(remote, remote_root, deleted_paths)
        remaining = [path for path, blob in deleted_hashes.items() if blob is not None]
        if remaining:
            raise UpdateError(
                "Deleted runtime files are still present: " + ", ".join(remaining)
            )

    if services:
        unit_args = " ".join(f"-u {q(service)}" for service in services)
        errors = remote.command(
            f"journalctl -p err {unit_args} --since {q(journal_since)} --no-pager"
        )
        meaningful = [
            line
            for line in errors.splitlines()
            if line.strip() and not line.startswith("-- No entries --")
        ]
        if meaningful:
            raise UpdateError("Error-priority journal entries after restart:\n" + "\n".join(meaningful))


def update(
    repo_root: Path,
    target: str,
    remote_root: str,
    *,
    initialize: bool,
    dry_run: bool,
) -> None:
    validate_destination(target, remote_root)
    repo_root = repo_root.resolve()
    head = validate_local_checkout(repo_root)
    short_head = head[:8]
    head_manifest = manifest_for_commit(repo_root, head)
    remote = Remote(target)
    marker_path = f"{remote_root}/{MARKER_NAME}"
    lock_path = f"{remote_root}/.update-lock"

    print(f"[er1 update] Source: {repo_root} @ {head}")
    print(f"[er1 update] Target: {target}:{remote_root}")
    remote.command("true")
    lock_acquired = False
    owner = ""
    if not dry_run:
        owner = f"{head} {datetime.now(timezone.utc).isoformat()} {uuid.uuid4().hex}"
        try:
            acquire_update_lock(remote, lock_path, owner)
        except UpdateError:
            release_owned_update_lock(
                remote,
                lock_path,
                owner,
                check=False,
            )
            raise
        lock_acquired = True

    try:
        statuses = remote.command(
            "systemctl is-active game_master.service er1-web.service"
        ).splitlines()
        if statuses != ["active", "active"]:
            raise UpdateError(f"ER1 services are not healthy: {statuses}")
        assert_dashboard_idle_state(read_dashboard_state(remote))
        assert_game_master_idle_state(read_game_master_state(remote))

        marker = read_marker(remote, marker_path)
        if initialize:
            if marker:
                raise UpdateError(f"Deployment marker already exists at {marker}")
            assert_remote_matches(remote, remote_root, head_manifest)
            if dry_run:
                print(f"[er1 update] Baseline matches; would write marker {head}")
            else:
                write_marker(remote, marker_path, head)
                print(f"[er1 update] Baseline initialized at {head}")
            return

        if marker is None:
            raise UpdateError(
                "Deployment marker is missing. First verify with "
                "'er1 update -Initialize -DryRun', then run 'er1 update -Initialize'."
            )
        if run(
            ("git", "cat-file", "-e", f"{marker}^{{commit}}"),
            cwd=repo_root,
            check=False,
        ).returncode:
            raise UpdateError(f"Remote marker commit is unavailable locally: {marker}")
        if run(
            ("git", "merge-base", "--is-ancestor", marker, head),
            cwd=repo_root,
            check=False,
        ).returncode:
            raise UpdateError(f"Remote marker {marker} is not an ancestor of HEAD {head}")

        base_manifest = manifest_for_commit(repo_root, marker)
        head_only_targets = {
            item.remote_path
            for path, item in head_manifest.items()
            if path not in base_manifest
        }
        assert_remote_matches(
            remote,
            remote_root,
            base_manifest,
            additionally_absent=head_only_targets,
        )
        actions = build_update_plan(base_manifest, head_manifest)
        if any(action.remote_path.endswith("requirements.txt") for action in actions):
            raise UpdateError(
                "requirements.txt changed. Dependency updates require a separately reviewed "
                "venv migration and are intentionally not automatic."
            )

        print(f"[er1 update] Baseline: {marker}")
        if not actions:
            print("[er1 update] No runtime file changes.")
            if dry_run:
                print(f"[er1 update] Would advance marker to {head}")
            else:
                write_marker(remote, marker_path, head)
                print(f"[er1 update] Marker advanced to {head}")
            return

        for action in actions:
            print(f"[er1 update] {action.action.upper():6} {action.remote_path}")
        services = ["game_master.service", "er1-web.service"]
        print("[er1 update] Fence/restart: er1-web.service, game_master.service")
        if dry_run:
            print("[er1 update] Dry run complete; no remote files were changed.")
            return

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        unique = uuid.uuid4().hex[:8]
        staging_root = f"{remote_root}/deploy-staging/{short_head}-{timestamp}-{unique}"
        backup_path = f"{remote_root}/deploy-backups/pre-{short_head}-{timestamp}-{unique}.tar.gz"
        writes = [action for action in actions if action.action == "write"]
        new_targets = [action.remote_path for action in writes if action.old_blob is None]
        existing_targets = [
            action.remote_path for action in actions if action.old_blob is not None
        ]
        backup_created = False
        services_fenced = False
        install_started = False

        try:
            directories = sorted(
                {str(PurePosixPath(action.remote_path).parent) for action in writes}
            )
            remote.command(
                f"mkdir -p {q(staging_root)} "
                + " ".join(q(f"{staging_root}/{directory}") for directory in directories)
            )
            for action in writes:
                source = repo_root / Path(action.repo_path)
                if not source.is_file():
                    raise UpdateError(f"Committed source file is missing: {source}")
                remote.copy(source, f"{staging_root}/{action.remote_path}")
            verify_staging(remote, staging_root, actions)

            assert_dashboard_idle_state(read_dashboard_state(remote))
            assert_game_master_idle_state(read_game_master_state(remote))
            remote.command(f"mkdir -p {q(f'{remote_root}/deploy-backups')}")
            if existing_targets:
                remote.command(
                    f"tar -czf {q(backup_path)} -C {q(remote_root)} "
                    + " ".join(q(path) for path in existing_targets)
                )
            else:
                remote.command(f"tar -czf {q(backup_path)} --files-from /dev/null")
            backup_created = True

            # Stop the normal operator entrypoint, then recheck authoritative state
            # before stopping the Game Master. This closes the dashboard start race.
            services_fenced = True
            remote.command("sudo -n systemctl stop er1-web.service")
            assert_game_master_idle_state(read_game_master_state(remote))
            remote.command("sudo -n systemctl stop game_master.service")

            install_commands = ["set -eu"]
            for action in actions:
                destination = f"{remote_root}/{action.remote_path}"
                if action.action == "write":
                    source = f"{staging_root}/{action.remote_path}"
                    install_commands.append(
                        f"install -D -m 0644 {q(source)} {q(destination)}"
                    )
                else:
                    install_commands.append(f"rm -f {q(destination)}")
            install_started = True
            remote.command("; ".join(install_commands))

            journal_since = remote.command("date --iso-8601=seconds")
            remote.command("sudo -n systemctl start game_master.service")
            time.sleep(2)
            if remote.command("systemctl is-active game_master.service") != "active":
                raise UpdateError("game_master.service did not start")
            assert_game_master_idle_state(read_game_master_state(remote))
            remote.command("sudo -n systemctl start er1-web.service")
            time.sleep(3)
            verify_health(
                remote,
                remote_root,
                head_manifest,
                actions,
                services,
                journal_since,
            )
            write_marker(remote, marker_path, head)
            try:
                remote.command(f"rm -rf {q(staging_root)}")
            except UpdateError as cleanup_error:
                print(f"[er1 update] WARNING: staging cleanup failed: {cleanup_error}")
            print(f"[er1 update] Update complete at {head}")
            print(f"[er1 update] Rollback archive: {backup_path}")
        except Exception:
            if services_fenced or install_started:
                try:
                    if not install_started:
                        remote.command(
                            "sudo -n systemctl start game_master.service er1-web.service"
                        )
                        unfenced_statuses = remote.command(
                            "systemctl is-active game_master.service er1-web.service"
                        ).splitlines()
                        if unfenced_statuses != ["active", "active"]:
                            raise UpdateError(
                                f"Service fence recovery failed: {unfenced_statuses}"
                            )
                    else:
                        remote.command("sudo -n systemctl stop er1-web.service")
                        game_master_active = (
                            remote.command(
                                "systemctl is-active game_master.service", check=False
                            )
                            == "active"
                        )
                        if game_master_active:
                            assert_game_master_idle_state(read_game_master_state(remote))
                            remote.command("sudo -n systemctl stop game_master.service")
                        if not backup_created:
                            raise UpdateError("Rollback archive was not created")
                        rollback_commands = [
                            "set -eu",
                            f"tar -xzf {q(backup_path)} -C {q(remote_root)}",
                        ]
                        rollback_commands.extend(
                            f"rm -f {q(f'{remote_root}/{path}')}" for path in new_targets
                        )
                        remote.command("; ".join(rollback_commands))
                        write_marker(remote, marker_path, marker)
                        remote.command(
                            "sudo -n systemctl start game_master.service er1-web.service"
                        )
                        time.sleep(5)
                        assert_remote_matches(
                            remote,
                            remote_root,
                            base_manifest,
                            additionally_absent=new_targets,
                        )
                        rollback_statuses = remote.command(
                            "systemctl is-active game_master.service er1-web.service"
                        ).splitlines()
                        if rollback_statuses != ["active", "active"]:
                            raise UpdateError(
                                f"Rollback services are not healthy: {rollback_statuses}"
                            )
                        assert_dashboard_idle_state(read_dashboard_state(remote))
                        assert_game_master_idle_state(read_game_master_state(remote))
                        print(
                            f"[er1 update] Rollback verified from {backup_path}",
                            file=sys.stderr,
                        )
                except Exception as rollback_error:
                    print(
                        f"[er1 update] Automatic rollback could not be verified: {rollback_error}",
                        file=sys.stderr,
                    )
                    if backup_created:
                        print(
                            f"[er1 update] Rollback archive: {backup_path}",
                            file=sys.stderr,
                        )
            print(
                f"[er1 update] Staging retained for diagnosis: {staging_root}",
                file=sys.stderr,
            )
            raise
    finally:
        if lock_acquired:
            try:
                release_owned_update_lock(
                    remote,
                    lock_path,
                    owner,
                    check=True,
                )
            except UpdateError as lock_error:
                print(
                    f"[er1 update] WARNING: update lock cleanup failed: {lock_error}",
                    file=sys.stderr,
                )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        update(
            args.repo_root,
            args.target,
            args.remote_root.rstrip("/"),
            initialize=args.initialize,
            dry_run=args.dry_run,
        )
    except UpdateError as error:
        print(f"[er1 update] ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
