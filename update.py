#!/usr/bin/env python3
"""Check the git repository behind this deployment and apply updates.

The directory this service runs from is a plain copy; the git working copy
lives elsewhere (``/root/thermal-web`` by default).  An update pulls in that
working copy and then runs its ``install.sh``, which copies the files into the
run directory and restarts the service.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time

DEFAULT_TIMEOUT = 60
LOG_NAME = "update.log"


class UpdateError(Exception):
    """Raised when git or the deploy script cannot be used."""


def _env() -> dict:
    """git/ssh need a HOME to find ~/.ssh, and must never prompt."""
    env = dict(os.environ)
    env.setdefault("HOME", "/root")
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    return env


def _git(repo: str, *args: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, timeout=timeout, env=_env(),
        )
    except FileNotFoundError as exc:
        raise UpdateError("git is not installed on this machine") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("git timed out after %ss" % timeout) from exc
    if result.returncode != 0:
        raise UpdateError((result.stderr or result.stdout).strip() or "git failed")
    return result.stdout.strip()


def is_repo(path: str) -> bool:
    return bool(path) and os.path.isdir(os.path.join(path, ".git"))


def _commit(repo: str, rev: str = "HEAD") -> dict:
    raw = _git(repo, "log", "-1", "--date=format:%Y-%m-%d %H:%M",
               "--pretty=format:%H\x1f%h\x1f%ad\x1f%an\x1f%s", rev)
    full, short, date, author, subject = (raw.split("\x1f") + [""] * 5)[:5]
    return {"sha": full, "short": short, "date": date, "author": author,
            "subject": subject}


def log_path(data_dir: str) -> str:
    return os.path.join(data_dir, LOG_NAME)


def read_log(data_dir: str, lines: int = 20) -> str:
    try:
        with open(log_path(data_dir), "r", errors="replace") as handle:
            return "".join(handle.readlines()[-lines:])
    except OSError:
        return ""


def check(repo: str, data_dir: str, fetch: bool = True) -> dict:
    """Report the local and remote commit, and whether a pull is needed."""
    started = time.time()
    report = {
        "repo": repo,
        "is_repo": False,
        "branch": "",
        "current": None,
        "remote": None,
        "update_available": False,
        "behind": 0,
        "ahead": 0,
        "dirty": False,
        "fetched": False,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": 0.0,
        "log_tail": read_log(data_dir),
        "error": "",
    }

    if not is_repo(repo):
        report["error"] = ("%s is not a git working copy - set the repository path "
                           "in the settings" % (repo or "(empty)"))
        return report

    report["is_repo"] = True
    try:
        report["branch"] = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        report["current"] = _commit(repo, "HEAD")
        report["dirty"] = bool(_git(repo, "status", "--porcelain"))

        if fetch:
            _git(repo, "fetch", "--quiet", "origin",
                 report["branch"], timeout=DEFAULT_TIMEOUT)
            report["fetched"] = True

        remote_rev = "origin/%s" % report["branch"]
        report["remote"] = _commit(repo, remote_rev)

        counts = _git(repo, "rev-list", "--left-right", "--count",
                      "HEAD...%s" % remote_rev).split()
        report["ahead"], report["behind"] = int(counts[0]), int(counts[1])
        report["update_available"] = report["behind"] > 0
    except UpdateError as exc:
        report["error"] = str(exc)

    report["duration"] = round(time.time() - started, 2)
    report["log_tail"] = read_log(data_dir)
    return report


def apply(repo: str, dest: str, data_dir: str) -> dict:
    """Start a detached pull + redeploy.  The service will restart itself."""
    if not is_repo(repo):
        raise UpdateError("%s is not a git working copy" % (repo or "(empty)"))
    if not os.path.isfile(os.path.join(repo, "install.sh")):
        raise UpdateError("%s has no install.sh to deploy with" % repo)

    before = _commit(repo, "HEAD")
    target = None
    try:
        target = _commit(repo, "origin/%s" % _git(repo, "rev-parse", "--abbrev-ref", "HEAD"))
    except UpdateError:
        pass

    script = (
        "cd {repo} && "
        "echo '=== update started' $(date '+%F %T') && "
        "git pull --ff-only && "
        "./install.sh {dest} && "
        "echo '=== update finished' $(date '+%F %T')"
    ).format(repo=shlex.quote(repo), dest=shlex.quote(dest))

    path = log_path(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    with open(path, "ab") as log:
        log.write(("\n$ %s\n" % script).encode())
        log.flush()
        subprocess.Popen(
            ["setsid", "bash", "-lc", script],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            cwd="/", env=_env(), start_new_session=True,
        )

    return {"from": before, "to": target, "log": path}
