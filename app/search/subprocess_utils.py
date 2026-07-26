"""One place where this project starts an external process.

Rules enforced here rather than at each call site:

  * `shell=False` with an argument list, so no part of a user submission is
    ever parsed by a shell;
  * a mandatory timeout, and the process group is killed on expiry so a
    hung child does not survive the request;
  * stdout/stderr go to files in the job directory, not pipes, so a runaway
    tool cannot fill the parent's memory, and the raw output remains for
    debugging;
  * failures come back as a structured object. Callers never see a traceback
    from a subprocess, and the user never sees a command line or a path.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# How much of a tool's stderr we keep in memory for the log/diagnostics.
STDERR_SNIPPET_BYTES = 4_000


class ToolNotFound(RuntimeError):
    def __init__(self, binary: str):
        super().__init__(f"executable not found: {binary}")
        self.binary = binary


@dataclass
class ToolRun:
    binary: str
    args: list[str]
    returncode: int | None
    timed_out: bool
    duration: float
    stdout_path: Path
    stderr_path: Path
    stderr_snippet: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def redacted_command(self) -> str:
        """Argument list with absolute paths reduced to basenames, for logs."""
        return " ".join([Path(self.binary).name] + [
            Path(a).name if os.sep in a else a for a in self.args
        ])


def run_tool(
    binary: str,
    args: list[str],
    *,
    timeout: int,
    log_dir: Path,
    log_name: str,
    cwd: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> ToolRun:
    """Run one external tool. Raises only ToolNotFound; everything else is
    reported in the returned ToolRun."""
    resolved = shutil.which(binary)
    if resolved is None:
        raise ToolNotFound(binary)

    log_dir.mkdir(parents=True, exist_ok=True)
    out_path = log_dir / f"{log_name}.stdout.txt"
    err_path = log_dir / f"{log_name}.stderr.txt"

    # A minimal environment: the tools need PATH and a temp dir, nothing else.
    # This keeps database credentials out of every child process.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", str(log_dir)),
        "TMPDIR": str(log_dir),
        "LC_ALL": "C",
    }
    if env_extra:
        env.update(env_extra)

    start = time.monotonic()
    timed_out = False
    returncode: int | None = None

    with out_path.open("wb") as out_fh, err_path.open("wb") as err_fh:
        # start_new_session puts the child in its own process group so that on
        # timeout we can kill any grandchildren (mafft and mmseqs both fork).
        proc = subprocess.Popen(
            [resolved, *args],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=out_fh,
            stderr=err_fh,
            cwd=str(cwd) if cwd else None,
            env=env,
            start_new_session=True,
        )
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
            returncode = None

    duration = time.monotonic() - start
    snippet = _read_tail(err_path, STDERR_SNIPPET_BYTES)

    run = ToolRun(
        binary=resolved, args=args, returncode=returncode, timed_out=timed_out,
        duration=duration, stdout_path=out_path, stderr_path=err_path,
        stderr_snippet=snippet,
    )
    if timed_out:
        log.warning("tool timed out after %.1fs: %s", duration, run.redacted_command())
    elif returncode != 0:
        log.warning(
            "tool exited %s in %.1fs: %s | stderr: %s",
            returncode, duration, run.redacted_command(), snippet[:400].replace("\n", " "),
        )
    else:
        log.info("tool ok in %.1fs: %s", duration, run.redacted_command())
    return run


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        proc.wait(timeout=5)


def _read_tail(path: Path, limit: int) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > limit:
                fh.seek(size - limit)
            data = fh.read(limit)
        return data.decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


_VERSION_LINE = re.compile(r"\d+\.\d+[\w.+-]*")


def tool_version(binary: str, args: list[str] | None = None, timeout: int = 20) -> str | None:
    """Best-effort version string, recorded with every result.

    BLAST+ answers `-version` on stdout; HMMER prints its banner on stderr in
    response to `-h`; MAFFT and MMseqs2 each do something else again. All four
    are handled by scanning both streams for the first line with a version
    number in it.
    """
    resolved = shutil.which(binary)
    if resolved is None:
        return None
    for candidate in ([args] if args else [["-version"], ["--version"], ["-h"]]):
        try:
            p = subprocess.run(
                [resolved, *candidate], shell=False, timeout=timeout,
                check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        for line in (p.stdout + "\n" + p.stderr).splitlines():
            line = line.strip().lstrip("#").strip()
            if line and _VERSION_LINE.search(line):
                return line[:120]
    return None
