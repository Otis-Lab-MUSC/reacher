"""The NDJSON sink: a bounded queue drained by one daemon writer thread.

Design constraints that shaped this:

* **It must never block a producer.**  Wire logging sits inside the kernel's
  serial read loop; a slow disk must not stall acquisition.  The queue is
  bounded and ``put`` is non-blocking — under pressure the sink drops and counts,
  the same contract the existing WebSocket event queue already uses.
* **It must never log through itself.**  A failure inside the writer cannot call
  ``logger.*`` without risking unbounded recursion through the stdlib bridge, so
  failures increment a counter and go to ``sys.__stderr__`` only.
* **It must survive a hard kill.**  Batched ``fsync`` mirrors the pattern already
  proven in ``kernel/reacher.py`` for ``controller_log.json``.
"""

import os
import queue
import shutil
import sys
import threading
import time
from typing import Optional

from . import context
from .schema import LogRecord


class _FlushRequest:
    """Sentinel asking the writer to flush and signal completion.

    Routed through the queue rather than flushing from the caller's thread, so
    the file handle is only ever touched by the writer.
    """

    __slots__ = ("done",)

    def __init__(self) -> None:
        self.done = threading.Event()

#: Bounded so a stalled disk cannot grow memory without limit.
QUEUE_MAX = 20000

#: Rotate at this size, keeping BACKUPS older segments.  At the 115200-baud
#: serial ceiling (~11 KB/s) even a saturated wire log fills one segment in
#: roughly an hour, so this holds a long experiment comfortably.
ROTATE_BYTES = 32 * 1024 * 1024
BACKUPS = 5

#: fsync every N records rather than per line; a crash loses at most this many.
FSYNC_INTERVAL = 50

#: Also fsync if this long has passed, so a quiet run still lands on disk.
FSYNC_SECONDS = 5.0

#: Consecutive write failures before the sink stops trying, and how long it
#: waits before re-probing the filesystem.
DEGRADE_AFTER = 5
DEGRADE_SECONDS = 30.0

#: Minimum gap between identical stderr complaints.
PANIC_REPEAT_SECONDS = 30.0


def default_log_root() -> str:
    """Root directory for run logs.

    Deliberately beside the per-session experiment directories, never inside
    them: the scientific data path stays byte-identical to what it is today.
    """
    override = os.environ.get("REACHER_LOG_DIR")
    if override:
        return os.path.expanduser(override)
    return os.path.expanduser("~/REACHER/LOG/runs")


class LogSink:
    """Serializes records onto disk from a single background thread."""

    def __init__(self, root: Optional[str] = None, rotate_bytes: int = ROTATE_BYTES, backups: int = BACKUPS):
        self.root = root or default_log_root()
        self.rotate_bytes = rotate_bytes
        self.backups = backups
        self.run_dir = os.path.join(self.root, f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{context.RUN_ID}")
        self.path = os.path.join(self.run_dir, "app.ndjson")

        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._fh = None
        self._size = 0
        self._writes = 0
        self._last_fsync = time.monotonic()
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()

        # Counters surfaced on /health so silent log loss is observable.
        self.dropped = 0
        self.write_failures = 0
        self._lock = threading.Lock()

        # Degraded mode: after repeated open failures (read-only home, full
        # disk) stop hammering the filesystem on every record.  Re-probe
        # occasionally so logging resumes on its own if the condition clears.
        self._consecutive_failures = 0
        self._degraded_until = 0.0
        self._last_panic = ("", 0.0)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> "LogSink":
        # Degrade, never raise.  A read-only home, a full disk, or a sandbox
        # that blocks directory creation must not stop the server from booting
        # — the writer retries _open() on each record, so logging recovers by
        # itself if the condition clears.
        try:
            self._open()
            self._update_latest_symlink()
        except Exception as exc:
            self._fh = None
            with self._lock:
                self.write_failures += 1
            self._panic(f"could not open {self.path}: {type(exc).__name__}: {exc}")
        self._thread = threading.Thread(target=self._run, name="reacher-logsink", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 3.0) -> None:
        """Drain and close.  Safe to call more than once."""
        if self._thread is None:
            return
        self._stopping.set()
        try:
            self._q.put_nowait(None)  # sentinel wakes a blocked writer
        except queue.Full:
            pass
        self._thread.join(timeout=timeout)
        self._close()

    # -- producer side -----------------------------------------------------

    def emit(self, record: LogRecord) -> None:
        """Enqueue a record.  Never blocks, never raises."""
        try:
            record.finalize()
            self._q.put_nowait(record)
        except queue.Full:
            with self._lock:
                self.dropped += 1
        except Exception:
            with self._lock:
                self.dropped += 1

    def flush_now(self, timeout: float = 2.0) -> bool:
        """Block until everything queued so far is on disk.

        Used before exporting the active run, whose tail would otherwise still
        be buffered and missing from the download.
        """
        if self._thread is None or not self._thread.is_alive():
            return False
        request = _FlushRequest()
        try:
            self._q.put_nowait(request)
        except queue.Full:
            return False
        return request.done.wait(timeout)

    def stats(self) -> dict:
        with self._lock:
            return {
                "run_id": context.RUN_ID,
                "path": self.path,
                "queued": self._q.qsize(),
                "dropped": self.dropped,
                "write_failures": self.write_failures,
            }

    # -- writer thread -----------------------------------------------------

    def _run(self) -> None:
        dirty = False
        while True:
            try:
                record = self._q.get(timeout=0.25)
            except queue.Empty:
                # Caught up.  flush() is cheap and hands the bytes to the OS,
                # so a *process* crash (the common case) loses nothing; fsync
                # stays on its batch interval because it is not cheap.
                if dirty:
                    self._flush()
                    dirty = False
                self._maybe_fsync(force_after=FSYNC_SECONDS)
                if self._stopping.is_set():
                    break
                continue
            if record is None:
                break
            if isinstance(record, _FlushRequest):
                self._flush()
                self._maybe_fsync(force_after=0.0)
                dirty = False
                record.done.set()
                continue
            self._write(record)
            dirty = True
        # Final drain so records queued during shutdown are not lost.
        while True:
            try:
                record = self._q.get_nowait()
            except queue.Empty:
                break
            if isinstance(record, _FlushRequest):
                record.done.set()
            elif record is not None:
                self._write(record)
        self._close()

    def _write(self, record: LogRecord) -> None:
        if self._degraded_until and time.monotonic() < self._degraded_until:
            with self._lock:
                self.dropped += 1
            return
        line = record.to_json() + "\n"
        try:
            self._write_line(line)
            self._consecutive_failures = 0
            self._degraded_until = 0.0
        except Exception as exc:
            with self._lock:
                self.write_failures += 1
            self._panic(f"write failed: {type(exc).__name__}: {exc}")
            # Reopen and retry once.  A transient failure (handle closed under
            # us, disk briefly unavailable) should cost zero records, not the
            # one unlucky record that happened to hit it.
            self._reopen()
            try:
                self._write_line(line)
                self._consecutive_failures = 0
            except Exception:
                with self._lock:
                    self.dropped += 1
                self._consecutive_failures += 1
                if self._consecutive_failures >= DEGRADE_AFTER:
                    self._degraded_until = time.monotonic() + DEGRADE_SECONDS
                    self._panic(
                        f"disabling log writes for {DEGRADE_SECONDS:.0f}s after "
                        f"{self._consecutive_failures} consecutive failures"
                    )

    def _write_line(self, line: str) -> None:
        if self._fh is None:
            self._open()
            if self._fh is None:
                raise OSError("log file unavailable")
        self._fh.write(line)
        self._size += len(line)
        self._writes += 1
        if self._size >= self.rotate_bytes:
            self._rotate()
        else:
            self._maybe_fsync()

    def _flush(self) -> None:
        """Push buffered bytes to the OS without paying for an fsync."""
        if self._fh is None:
            return
        try:
            self._fh.flush()
        except Exception:
            with self._lock:
                self.write_failures += 1

    def _maybe_fsync(self, force_after: Optional[float] = None) -> None:
        if self._fh is None:
            return
        due = self._writes % FSYNC_INTERVAL == 0 and self._writes > 0
        if force_after is not None:
            due = due or (time.monotonic() - self._last_fsync) >= force_after
        if not due:
            return
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._last_fsync = time.monotonic()
        except Exception:
            with self._lock:
                self.write_failures += 1

    # -- file management ---------------------------------------------------

    def _open(self) -> None:
        os.makedirs(self.run_dir, exist_ok=True)
        if not os.path.isdir(self.run_dir):
            # os.makedirs can be a no-op under test patching; fail clearly here
            # rather than with a confusing FileNotFoundError from open().
            raise OSError(f"log directory not created: {self.run_dir}")
        self._fh = open(self.path, "a", encoding="utf-8")
        try:
            self._size = os.path.getsize(self.path)
        except OSError:
            self._size = 0

    def _close(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None

    def _reopen(self) -> None:
        self._close()
        try:
            self._open()
        except Exception:
            self._fh = None

    def _rotate(self) -> None:
        self._close()
        try:
            oldest = f"{self.path}.{self.backups}"
            if os.path.exists(oldest):
                os.remove(oldest)
            for i in range(self.backups - 1, 0, -1):
                src, dst = f"{self.path}.{i}", f"{self.path}.{i + 1}"
                if os.path.exists(src):
                    os.replace(src, dst)
            if os.path.exists(self.path):
                os.replace(self.path, f"{self.path}.1")
        except Exception as exc:
            self._panic(f"rotate failed: {type(exc).__name__}: {exc}")
        self._writes = 0
        self._open()

    def _update_latest_symlink(self) -> None:
        """Point ``runs/latest`` at this run, for support requests.

        Best-effort: symlinks require elevation on some Windows configurations,
        and a missing convenience link is not worth failing startup over.
        """
        link = os.path.join(self.root, "latest")
        try:
            if os.path.islink(link) or os.path.exists(link):
                if os.path.islink(link):
                    os.unlink(link)
                else:
                    return
            os.symlink(self.run_dir, link, target_is_directory=True)
        except Exception:
            pass

    def _panic(self, message: str) -> None:
        """Report a sink failure without going through the logging system.

        Deduplicated: an unwritable disk would otherwise emit one stderr line
        per record and bury whatever the operator was actually looking at.
        """
        now = time.monotonic()
        last_msg, last_at = self._last_panic
        if message == last_msg and (now - last_at) < PANIC_REPEAT_SECONDS:
            return
        self._last_panic = (message, now)
        try:
            print(f"[reacher.diagnostics] {message}", file=sys.__stderr__, flush=True)
        except Exception:
            pass


def prune_runs(root: Optional[str] = None, keep_runs: int = 20, max_age_days: int = 30) -> int:
    """Delete old run directories.  Returns the number removed.

    Bounds disk use on long-lived lab machines that are never manually cleaned.
    """
    root = root or default_log_root()
    if not os.path.isdir(root):
        return 0
    try:
        entries = [
            os.path.join(root, name)
            for name in os.listdir(root)
            if name != "latest" and os.path.isdir(os.path.join(root, name))
        ]
    except OSError:
        return 0

    entries.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for index, path in enumerate(entries):
        try:
            too_many = index >= keep_runs
            too_old = os.path.getmtime(path) < cutoff
            if too_many or too_old:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed
