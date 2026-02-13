from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

RUNTIME_DIRNAME = ".tgcodex-bot"


@dataclass(frozen=True)
class PidRecord:
    pid: int
    argv: tuple[str, ...] = ()
    start_time: Optional[str] = None


def runtime_dir_for_config(config_path: Path) -> Path:
    """
    Derive a per-config runtime directory for pid/log files.

    We keep these next to the config file by default to avoid polluting $HOME
    and to allow multiple independent configs.
    """

    return Path(config_path).expanduser().parent / RUNTIME_DIRNAME


def pid_file_for_config(config_path: Path) -> Path:
    return runtime_dir_for_config(config_path) / f"{Path(config_path).name}.pid"


def log_file_for_config(config_path: Path) -> Path:
    return runtime_dir_for_config(config_path) / f"{Path(config_path).name}.log"


def _read_proc_cmdline(pid: int) -> Optional[tuple[str, ...]]:
    """
    Best-effort process argv from /proc/<pid>/cmdline (Linux).
    """

    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except Exception:
        return None
    if not raw:
        return None
    parts = [p.decode("utf-8", errors="replace") for p in raw.split(b"\x00") if p]
    return tuple(parts) if parts else None


def _read_proc_start_time(pid: int) -> Optional[str]:
    """
    Best-effort process start-time tick from /proc/<pid>/stat (Linux).
    """

    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        # stat format starts with: "<pid> (<comm>) <state> ... <starttime> ..."
        _, rest = raw.rsplit(") ", 1)
        fields = rest.split()
        # `starttime` is field 22 overall -> index 19 after removing first 2 fields.
        return fields[19]
    except Exception:
        return None


def _looks_like_tgcodex_process(cmdline: tuple[str, ...]) -> bool:
    flat = " ".join(cmdline)
    has_identity = ("tgcodex.cli" in flat) or ("tgcodex-bot" in flat)
    has_run = any(tok == "run" for tok in cmdline)
    return has_identity and has_run


def read_pid_record(pid_file: Path) -> Optional[PidRecord]:
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except Exception:
        return None

    # Backward compatible with old "<pid>\n" format.
    try:
        pid = int(raw)
    except Exception:
        pid = None
    if isinstance(pid, int):
        if pid <= 0:
            return None
        return PidRecord(pid=pid)

    # New JSON format.
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    pid_val = obj.get("pid")
    if not isinstance(pid_val, int) or pid_val <= 0:
        return None

    argv_raw = obj.get("argv")
    argv: tuple[str, ...] = ()
    if isinstance(argv_raw, list):
        vals: list[str] = []
        for v in argv_raw:
            if isinstance(v, str):
                vals.append(v)
        argv = tuple(vals)

    start_time = obj.get("start_time")
    if not isinstance(start_time, str):
        start_time = None

    return PidRecord(pid=pid_val, argv=argv, start_time=start_time)


def _write_pid_record(pid_file: Path, record: PidRecord) -> None:
    pid_file.write_text(
        json.dumps(
            {
                "pid": int(record.pid),
                "argv": list(record.argv),
                "start_time": record.start_time,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def read_pid(pid_file: Path) -> Optional[int]:
    rec = read_pid_record(pid_file)
    return rec.pid if rec is not None else None


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it.
        return True
    except OSError:
        return False
    return True


def _is_expected_process(record: PidRecord) -> bool:
    """
    Best-effort guard against PID reuse.

    We compare recorded process metadata where available. If no metadata exists
    (legacy pid file), require that the process looks like a tgcodex run.
    """

    inspected = False
    strong_match = False

    if record.start_time is not None:
        current_start = _read_proc_start_time(record.pid)
        if current_start is not None:
            inspected = True
            if current_start != record.start_time:
                return False
            strong_match = True

    if record.argv:
        current_cmd = _read_proc_cmdline(record.pid)
        if current_cmd is not None:
            inspected = True
            if tuple(current_cmd) != tuple(record.argv):
                return False
            strong_match = True
    else:
        # Legacy pid files have no argv metadata; only stop if it still looks
        # like one of our run commands.
        current_cmd = _read_proc_cmdline(record.pid)
        if current_cmd is not None:
            inspected = True
            if not _looks_like_tgcodex_process(current_cmd):
                return False
            strong_match = True

    # If platform/introspection doesn't provide process metadata, we cannot
    # verify identity safely.
    if not inspected:
        return False
    return strong_match


def pid_file_matches_running_process(pid_file: Path) -> bool:
    record = read_pid_record(pid_file)
    if record is None:
        return False
    if not is_pid_running(record.pid):
        return False
    return _is_expected_process(record)


def start_detached(
    argv: Sequence[str],
    *,
    pid_file: Path,
    log_file: Path,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[str] = None,
) -> int:
    """
    Spawn a detached background process and write its PID to `pid_file`.

    This is a lightweight alternative to systemd/supervisord for local dev.
    """

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    child_env = None
    if env is not None:
        child_env = os.environ.copy()
        child_env.update(dict(env))

    # Parent closes its fd, child keeps its own inherited fd.
    with log_file.open("a", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=child_env,
            cwd=cwd,
            start_new_session=True,
            close_fds=True,
        )
        record = PidRecord(
            pid=int(proc.pid),
            argv=tuple(argv),
            start_time=_read_proc_start_time(int(proc.pid)),
        )
        _write_pid_record(pid_file, record)
        return int(proc.pid)


def stop(pid_file: Path, *, timeout_seconds: float = 10.0) -> bool:
    """
    Stop a background process referenced by `pid_file`.

    Returns:
      - True if we observed the process exit and removed the pid file.
      - False if there was no running process, or it did not stop in time.
    """

    record = read_pid_record(pid_file)
    if record is None:
        return False

    pid = record.pid
    if not is_pid_running(pid):
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return False

    if not _is_expected_process(record):
        try:
            pid_file.unlink()
        except Exception:
            pass
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        return False

    # Always do at least one post-signal check, even with timeout=0.
    if not is_pid_running(pid):
        try:
            pid_file.unlink()
        except Exception:
            pass
        return True

    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        time.sleep(0.1)
        if not is_pid_running(pid):
            try:
                pid_file.unlink()
            except Exception:
                pass
            return True

    return False
