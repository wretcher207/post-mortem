"""Job-folder sidecar service (Phase 3, P3-001).

A long-running process that watches an inbox of atomic JSON job files and
executes engine calls — the same no-socket architecture as the bridge, because
it is proven, debuggable, and firewall-inert. The ReaImGui panel is a THIN
client of this service: it writes job files and renders result files, nothing
more. The protocol contract lives in docs/SIDECAR_PROTOCOL.md.

Layout under the app-data root (never the repo folder):

    PostMortem/
      jobs/inbox/        panel writes job files here
      jobs/processing/   service moves a job here while executing it
      jobs/outbox/       results and progress files
      logs/service.log   internal errors land here, never in result files
      heartbeat.json     pid, version, updated_at, in_flight_job
      lock.d/            single-instance lock (atomic mkdir, pid-liveness checked)

Safety rules carried from Phase 2:
- The service adds NO mutation path. preview_fix/commit_fix call the same
  preview.py orchestration the CLI uses, restore-in-finally included.
- A job interrupted by a crash is NEVER re-executed on startup; it gets a
  typed `interrupted` error. The bridge's own startup recovery restores any
  half-open preview.
- Cancellation is only honored at stage boundaries BEFORE the model call,
  and never between preview apply and restore.
"""

import argparse
import json
import os
import shutil
import re
import sys
import time
import tempfile
import traceback
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import __version__, bridge, config, readiness
from . import preview as preview_mod
from .analysis import analyze_wav
from .cli import (
    TrackNotResolved,
    _assert_same_track,
    _track_names,
    capture_isolation_gate,
    resolve_track,
    silence_gate,
)
from .constants import DEFAULT_CAPTURE_SECONDS
from .diagnose import build_payload, diagnose_track
from .proposals import adjust_proposal
from .providers.anthropic_provider import AnthropicProvider
from .providers.base import ProviderError

_HEARTBEAT_INTERVAL_SECONDS = 2.0
_POLL_SECONDS = 0.25
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")

JOB_TYPES = (
    "get_status",
    "enable_capture",
    "validate_provider",
    "track_check",
    "preview_fix",
    "commit_fix",
    "cancel_job",
    "record_feedback",
    "record_mcp_measurement",
    "record_mcp_handoff",
)


class JobRefused(Exception):
    """A refusal with a stable machine-readable code and a human message."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class JobCancelled(Exception):
    """The panel cancelled this job at a safe stage boundary."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


_FRESH_CHECK_CODES = {
    "current_value_drift", "stale_identity", "track_identity_mismatch",
    "current_value_mismatch", "fx_identity_mismatch",
    "parameter_identity_mismatch", "revalidation_failed",
}
_EVIDENCE_CODES = {
    "evidence_missing", "evidence_path_missing", "evidence_value_null",
    "fx_bypass_evidence_missing",
}
_UNSUPPORTED_MOVE_CODES = {
    "move_limit_exceeded", "unsupported_goal", "unsupported_metric",
    "fx_bypass_not_preview",
}


def _error_recovery(code):
    """Engine-owned explanation and next action for every stable error code."""
    if code == "internal_error":
        return {
            "explanation": "Post Mortem hit an internal error.",
            "action": "Copy the diagnostics and include them with a bug report.",
            "copy_diagnostics": True,
        }
    known = {
        "isolation_gate": (
            "This track cannot produce verified isolated evidence yet.",
            "Choose an item-less routing stem for now.",
        ),
        "isolation_gate_preview": (
            "This track cannot produce verified isolated evidence yet.",
            "Choose an item-less routing stem for now.",
        ),
        "capture_not_isolated": (
            "This track cannot produce verified isolated evidence yet.",
            "Choose an item-less routing stem for now.",
        ),
        "silence_gate": (
            "The capture came back essentially silent.",
            "Move the edit cursor to a section where the track is playing, then check it again.",
        ),
        "insufficient_signal": (
            "The capture did not contain enough signal to measure safely.",
            "Move the edit cursor to a section where the track is playing, then check it again.",
        ),
        "track_not_resolved": (
            "Post Mortem could not pin down one track.",
            "Select exactly one track, then check it again.",
        ),
        "bridge_error": (
            "REAPER did not answer the way it should have.",
            "Wait for the watchdog to reconnect. If it does not, restart REAPER once and test again.",
        ),
        "cancelled": ("Cancelled. Nothing was changed.", "Try the check again when ready."),
        "interrupted": (
            "The engine restarted mid-check. Nothing was re-run.",
            "Run a fresh Track Check.",
        ),
        "bad_job": (
            "The panel and engine disagreed about this request.",
            "Update Post Mortem, then try again.",
        ),
        "bad_adjustment": (
            "The requested adjustment was not valid.",
            "Run a fresh preview before adjusting again.",
        ),
        "unknown_job_type": (
            "The panel and engine versions do not agree.",
            "Update Post Mortem, then try again.",
        ),
        "nothing_to_cancel": ("That check already finished or stopped.", None),
        "provider_authentication": (
            "The key, endpoint, or configured model was rejected.",
            "Reconnect the API key, then test it again.",
        ),
        "provider_rate_limit": (
            "The provider is out of credit or rate-limited.",
            "Add credit or wait for the limit to clear, then try again.",
        ),
        "provider_network": (
            "The provider could not be reached.",
            "Check the internet connection, then try again.",
        ),
        "provider_refusal": (
            "The provider declined this check.",
            "Try once more. If it repeats, use a different connected provider.",
        ),
        "provider_incomplete_response": (
            "The provider did not return a complete diagnosis.",
            "Run a fresh Track Check. If it repeats, reconnect the provider.",
        ),
        "provider_invalid_response": (
            "The provider returned a diagnosis Post Mortem could not validate.",
            "Run a fresh Track Check. If it repeats, reconnect the provider.",
        ),
        "not_actionable": (
            "There is no previewable move in this result.",
            "Use the explanation as guidance or run a fresh Track Check after making changes.",
        ),
        "bad_diagnosis": (
            "This diagnosis cannot be previewed safely.",
            "Run a fresh Track Check before trying another move.",
        ),
        "cross_track_claim": (
            "A single-track check cannot support a claim about another track.",
            "Run a fresh check and treat this result as advice only.",
        ),
        "proposed_value_unchanged": (
            "The proposed value is already set.", "No preview is needed.",
        ),
        "mcp_receipt_missing": (
            "No measured MCP Track Check is ready for this diagnosis.",
            "Run analyze_track for one track, then return its diagnosis.",
        ),
        "mcp_receipt_invalid": (
            "This MCP diagnosis does not match the latest measured Track Check.",
            "Run a fresh 10-second analyze_track check, then return that diagnosis.",
        ),
    }
    if code in _FRESH_CHECK_CODES:
        known[code] = (
            "The track, plug-in, parameter, or value changed after the check.",
            "Run a fresh Track Check against the current project state.",
        )
    elif code in _EVIDENCE_CODES:
        known[code] = (
            "The proposed move does not point to verified measured evidence.",
            "Run a fresh Track Check. Post Mortem will not preview it as-is.",
        )
    elif code in _UNSUPPORTED_MOVE_CODES:
        known[code] = (
            "The proposed move is unsupported or outside the safe preview limit.",
            "Use the finding as advice or make a smaller change yourself.",
        )
    if code in known:
        explanation, action = known[code]
        return {"explanation": explanation, "action": action, "copy_diagnostics": False}
    return {
        "explanation": f"Post Mortem returned error code: {code}",
        "action": "Copy the diagnostics and include them with a bug report.",
        "copy_diagnostics": True,
    }


def app_data_root():
    """Platform-appropriate application data directory (PRODUCT_PLAN §9).
    POSTMORTEM_DATA_DIR (env or config file) overrides, mainly for tests."""
    override = config.get("POSTMORTEM_DATA_DIR")
    if override:
        return os.path.expanduser(override)
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/PostMortem")
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "PostMortem")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "postmortem")


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) is NOT a probe on Windows: it calls TerminateProcess
        # (killing a live pid) and raises plain OSError for a dead one. Query
        # the process instead.
        import ctypes
        import ctypes.wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_ACCESS_DENIED = 5
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # Access denied means SOMETHING owns that pid; anything else is dead.
            return kernel32.GetLastError() == ERROR_ACCESS_DENIED
        try:
            exit_code = ctypes.wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # Permission/edge errors mean SOMETHING owns that pid; assume alive.
        return True
    return True


class Service:
    def __init__(self, root=None):
        self.root = root or app_data_root()
        self.inbox = os.path.join(self.root, "jobs", "inbox")
        self.processing = os.path.join(self.root, "jobs", "processing")
        self.outbox = os.path.join(self.root, "jobs", "outbox")
        self.logs = os.path.join(self.root, "logs")
        self._last_heartbeat = 0.0
        self._ensure_layout()

    # -- filesystem plumbing -------------------------------------------------

    def _ensure_layout(self):
        for path in (self.inbox, self.processing, self.outbox, self.logs):
            os.makedirs(path, exist_ok=True)

    def _atomic_write_json(self, path, obj):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
        os.replace(tmp, path)

    def _log(self, message):
        line = f"{_utc_now()} {message}\n"
        try:
            with open(os.path.join(self.logs, "service.log"), "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def write_heartbeat(self, in_flight_job=None, force=False):
        now = time.monotonic()
        if not force and now - self._last_heartbeat < _HEARTBEAT_INTERVAL_SECONDS:
            return
        self._last_heartbeat = now
        self._atomic_write_json(
            os.path.join(self.root, "heartbeat.json"),
            {
                "pid": os.getpid(),
                "service_version": __version__,
                "updated_at": _utc_now(),
                "in_flight_job": in_flight_job,
                "last_progress_at": in_flight_job and _utc_now() or None,
            },
        )

    def _progress_path(self, stem):
        return os.path.join(self.outbox, f"{stem}.progress.json")

    def _write_progress(self, stem, job_id, stage):
        self._atomic_write_json(
            self._progress_path(stem),
            {"id": job_id, "stage": stage, "updated_at": _utc_now()},
        )
        # Bump the heartbeat so a client can distinguish a live in-flight job
        # (heartbeat advancing at each stage) from a dead one (stale despite
        # in_flight_job being set). Without this, a crashed sidecar's last
        # heartbeat would read "busy" forever.
        self.write_heartbeat(in_flight_job=job_id, force=True)

    def _write_result(self, stem, job_id, ok, result=None, error=None):
        """Final result for a job. The reply filename is ALWAYS derived from
        the inbox filename, never from the job's own id field — a hostile id
        must not choose where its reply lands (bridge finding 13)."""
        if isinstance(error, dict) and "recovery" not in error:
            error = {**error, "recovery": _error_recovery(str(error.get("code") or "unknown"))}
        self._atomic_write_json(
            os.path.join(self.outbox, f"{stem}.json"),
            {
                "id": job_id,
                "ok": ok,
                "result": result,
                "error": error,
                "finished_at": _utc_now(),
            },
        )
        try:
            os.unlink(self._progress_path(stem))
        except OSError:
            pass

    # -- single-instance lock ------------------------------------------------

    def acquire_lock(self):
        """Single-instance lock using atomic directory creation.

        os.mkdir is atomic on all platforms: exactly one process succeeds.
        The PID is written to a file inside the lockdir after creation.
        If the lockdir exists, the PID is checked: a dead pid is reclaimed;
        a live pid or an unreadable pid file (the writer is still writing)
        refuses. We never delete a lockdir whose owner we cannot prove dead.
        """
        lockdir = os.path.join(self.root, "lock.d")
        pid_path = os.path.join(lockdir, "pid")
        while True:
            try:
                os.mkdir(lockdir)
            except FileExistsError:
                held = None
                try:
                    with open(pid_path, encoding="utf-8") as f:
                        held = json.load(f)
                except (OSError, ValueError):
                    # The pid file is missing or unreadable. This is either
                    # the brief write window (the owner is alive) or a
                    # crashed process. Wait once and retry; if still
                    # unreadable, refuse rather than deleting a lock we
                    # cannot prove is stale.
                    time.sleep(0.2)
                    try:
                        with open(pid_path, encoding="utf-8") as f:
                            held = json.load(f)
                    except (OSError, ValueError):
                        raise JobRefused(
                            "already_running",
                            "another sidecar holds the lock but its pid is "
                            "unreadable; retry in a moment",
                        )
                pid = held.get("pid") if isinstance(held, dict) else None
                if isinstance(pid, int) and pid != os.getpid() and _pid_alive(pid):
                    raise JobRefused(
                        "already_running",
                        f"another sidecar (pid {pid}) holds the lock",
                    )
                # PID is dead or is us: reclaim.
                shutil.rmtree(lockdir, ignore_errors=True)
                continue
            # We won the mkdir race. Write the PID file atomically.
            self._atomic_write_json(pid_path, {"pid": os.getpid(), "created_at": _utc_now()})
            return

    def release_lock(self):
        shutil.rmtree(os.path.join(self.root, "lock.d"), ignore_errors=True)

    # -- startup -------------------------------------------------------------

    def sweep_interrupted(self):
        """Jobs stranded in processing/ died with a previous service instance.
        Never re-execute them (preview_fix may have mutated the project; the
        bridge's own startup recovery restores it). Report them typed."""
        for name in sorted(os.listdir(self.processing)):
            if not name.endswith(".json"):
                continue
            stem = name[: -len(".json")]
            path = os.path.join(self.processing, name)
            job_id = stem
            try:
                with open(path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and isinstance(loaded.get("id"), str):
                    job_id = loaded["id"]
            except (OSError, ValueError):
                pass
            self._write_result(
                stem,
                job_id,
                ok=False,
                error={
                    "code": "interrupted",
                    "message": "the sidecar restarted while this job was running; "
                    "it was not re-executed. Submit it again.",
                },
            )
            self._log(f"interrupted job swept: {stem}")
            try:
                os.unlink(path)
            except OSError:
                pass

    def sweep_orphan_wavs(self):
        """Remove capture WAVs orphaned by a previous crashed process.

        Scans the bridge temp dir for .wav files older than 24 hours that are
        not referenced by outbox results. The 24-hour floor preserves recently
        created CLI --keep-wav inspection files and panel preview keep_wav
        files that may not yet have an outbox result. Only files in the
        'reaper-diagnosis' temp dir are touched; the app-data root's other
        directories are not.
        """
        _ORPHAN_WAV_MIN_AGE = 24 * 3600
        temp_dir = os.path.join(tempfile.gettempdir(), "reaper-diagnosis")
        if not os.path.isdir(temp_dir):
            return
        # Collect WAV paths still referenced by live outbox results so we
        # don't delete preview keep_wav files the panel still needs.
        referenced = set()
        for name in os.listdir(self.outbox):
            if not name.endswith(".json") or name.endswith(".progress.json"):
                continue
            try:
                with open(os.path.join(self.outbox, name), encoding="utf-8") as f:
                    result = json.load(f)
                wav_paths = (result.get("result") or {}).get("wav_paths")
                if isinstance(wav_paths, dict):
                    for v in wav_paths.values():
                        if isinstance(v, str):
                            referenced.add(os.path.realpath(v))
            except (OSError, ValueError):
                continue
        now = time.time()
        for name in os.listdir(temp_dir):
            if not name.endswith(".wav"):
                continue
            path = os.path.join(temp_dir, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            # Only sweep files older than 24 hours. This preserves recent
            # CLI --keep-wav inspection files and panel preview WAVs that
            # may not yet have an outbox result, while still cleaning up
            # WAVs orphaned by crashes days ago.
            if now - stat.st_mtime < _ORPHAN_WAV_MIN_AGE:
                continue
            if os.path.realpath(path) in referenced:
                continue
            try:
                os.unlink(path)
                self._log(f"orphan wav swept: {name}")
            except OSError:
                pass

    # -- cancellation --------------------------------------------------------

    def _consume_cancels_for(self, target_id):
        """Consume any queued cancel_job files aimed at target_id. Returns True
        when at least one was found (each gets its own ok result)."""
        found = False
        for name in sorted(os.listdir(self.inbox)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.inbox, name)
            try:
                with open(path, encoding="utf-8") as f:
                    job = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(job, dict) or job.get("type") != "cancel_job":
                continue
            if (job.get("payload") or {}).get("target_id") != target_id:
                continue
            stem = name[: -len(".json")]
            self._write_result(
                stem, self._job_id(job, stem), ok=True,
                result={"cancelled": target_id},
            )
            try:
                os.unlink(path)
            except OSError:
                pass
            found = True
        return found

    def _check_cancel(self, job_id):
        if self._consume_cancels_for(job_id):
            raise JobCancelled()

    # -- job execution -------------------------------------------------------

    @staticmethod
    def _job_id(job, stem):
        job_id = job.get("id")
        if isinstance(job_id, str) and _SAFE_ID.match(job_id):
            return job_id
        return stem

    def run_once(self):
        """Drain the inbox (oldest filename first), one job at a time.
        Returns the number of jobs processed."""
        processed = 0
        for name in sorted(os.listdir(self.inbox)):
            if not name.endswith(".json"):
                continue
            inbox_path = os.path.join(self.inbox, name)
            if not os.path.exists(inbox_path):
                continue  # consumed by a cancel scan mid-drain
            processing_path = os.path.join(self.processing, name)
            try:
                os.replace(inbox_path, processing_path)
            except OSError:
                continue
            self._process_file(name, processing_path)
            processed += 1
        return processed

    def _process_file(self, name, path):
        stem = name[: -len(".json")]
        job_id = stem
        try:
            with open(path, encoding="utf-8") as f:
                job = json.load(f)
            if not isinstance(job, dict):
                raise JobRefused("bad_job", "job file is not a JSON object")
            job_id = self._job_id(job, stem)
            job_type = job.get("type")
            if job_type not in JOB_TYPES:
                raise JobRefused(
                    "unknown_job_type",
                    f"unknown job type {job_type!r}; supported: {', '.join(JOB_TYPES)}",
                )
            self.write_heartbeat(in_flight_job=job_id, force=True)
            self._write_progress(stem, job_id, "started")
            handler = _HANDLERS[job_type]
            result = handler(self, job, stem, job_id)
            self._write_result(stem, job_id, ok=True, result=result)
        except JobCancelled:
            self._write_result(
                stem, job_id, ok=False,
                error={"code": "cancelled", "message": "cancelled before completion"},
            )
        except JobRefused as e:
            self._write_result(
                stem, job_id, ok=False, error={"code": e.code, "message": str(e)}
            )
        except TrackNotResolved as e:
            self._write_result(
                stem, job_id, ok=False,
                error={"code": "track_not_resolved", "message": str(e)},
            )
        except preview_mod.PreviewRefused as e:
            self._write_result(
                stem, job_id, ok=False, error={"code": e.code, "message": str(e)}
            )
        except bridge.BridgeError as e:
            self._write_result(
                stem, job_id, ok=False,
                error={"code": "bridge_error", "message": str(e)},
            )
        except ProviderError as e:
            self._write_result(
                stem, job_id, ok=False,
                error={"code": f"provider_{e.category.value}", "message": str(e)},
            )
        except ValueError as e:
            self._write_result(
                stem, job_id, ok=False,
                error={"code": "bad_job", "message": f"unreadable job file: {e}"},
            )
        except Exception:
            # Unknown failures never leak tracebacks into the panel; the log
            # holds the detail, the result holds a typed pointer to it.
            self._log(f"internal_error on {stem}:\n{traceback.format_exc()}")
            self._write_result(
                stem, job_id, ok=False,
                error={
                    "code": "internal_error",
                    "message": "unexpected failure; see logs/service.log",
                },
            )
        finally:
            self.write_heartbeat(in_flight_job=None, force=True)
            try:
                os.unlink(path)
            except OSError:
                pass

    def run_forever(self, poll_seconds=_POLL_SECONDS):
        self.acquire_lock()
        try:
            self.sweep_interrupted()
            self.sweep_orphan_wavs()
            self.write_heartbeat(force=True)
            self._log(f"sidecar {__version__} started (pid {os.getpid()})")
            while True:
                self.run_once()
                self.write_heartbeat()
                time.sleep(poll_seconds)
        finally:
            self.release_lock()


# -- handlers ------------------------------------------------------------


def _provider_label():
    """Return the analysis destination without exposing credentials or paths."""

    base_url = config.get("ANTHROPIC_BASE_URL")
    if not base_url:
        return "Anthropic"
    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return "Configured provider"
    if hostname == "anthropic.com" or hostname.endswith(".anthropic.com"):
        return "Anthropic"
    return hostname


def _validated_seconds(payload):
    seconds = payload.get("seconds", DEFAULT_CAPTURE_SECONDS)
    if not isinstance(seconds, int) or not 1 <= seconds <= 600:
        raise JobRefused("bad_job", "payload.seconds must be an integer 1-600")
    return seconds


def _job_get_status(svc, job, stem, job_id):
    live = readiness.probe_bridge()
    bridge_ok = live["bridge_ok"]
    line = live["bridge_status"]
    capture_preflight = live["capture_preflight"]
    try:
        _, profile = AnthropicProvider.from_config()
        provider_configured = True
        model = profile.model
    except ProviderError:
        provider_configured = False
        model = config.get("POSTMORTEM_MODEL")
    mcp_handoff = {"ready": False}
    started_at = (job.get("payload") or {}).get("mcp_started_at")
    try:
        with open(os.path.join(svc.root, "mcp-handoff.json"), encoding="utf-8") as f:
            candidate = json.load(f)
        delivered = datetime.fromisoformat(
            str(candidate.get("delivered_at", "")).replace("Z", "+00:00")
        )
        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        tracks = candidate.get("tracks")
        summary = candidate.get("diagnosis_summary")
        if (
            delivered.tzinfo is not None and started.tzinfo is not None
            and delivered >= started
            and isinstance(tracks, list) and len(tracks) == 1
            and isinstance(tracks[0], str) and bool(tracks[0].strip())
            and isinstance(summary, str) and len(summary.strip()) >= 20
        ):
            mcp_handoff = {**candidate, "ready": True}
    except (OSError, ValueError, AttributeError):
        pass
    return {
        "service_version": __version__,
        "data_root": svc.root,
        "bridge_ok": bridge_ok,
        "bridge_status": line,
        "capture_preflight": capture_preflight,
        "capture_preflight_detail": live["capture_preflight_detail"],
        "provider_configured": provider_configured,
        "provider": _provider_label(),
        "model": model,
        "setup": readiness.setup_state(
            bridge_ok, line, capture_preflight, provider_configured,
            (job.get("payload") or {}).get("panel_registered", True) is True,
        ),
        "mcp_handoff": mcp_handoff,
    }


def _job_track_check(svc, job, stem, job_id):
    payload = job.get("payload") or {}
    track = payload.get("track")
    if not isinstance(track, str) or not track:
        raise JobRefused("bad_job", "payload.track (string) is required")
    seconds = _validated_seconds(payload)

    svc._check_cancel(job_id)
    svc._write_progress(stem, job_id, "reading_track")
    context = bridge.get_context()
    resolved = resolve_track(track, _track_names(context))
    track_scan = bridge.scan_fx(resolved)
    routing = bridge.get_track_routing(resolved)

    svc._check_cancel(job_id)
    svc._write_progress(stem, job_id, "capturing")
    capture_data, wav_path = bridge.capture_track_audio(
        resolved, duration_seconds=seconds
    )
    try:
        _assert_same_track(track_scan, routing, capture_data)
        gate = capture_isolation_gate(capture_data)
        if gate:
            raise JobRefused("isolation_gate", gate)

        svc._check_cancel(job_id)
        svc._write_progress(stem, job_id, "measuring")
        stats = analyze_wav(wav_path)
        if not payload.get("force"):
            gate = silence_gate(stats)
            if gate:
                raise JobRefused("silence_gate", gate)
        payload_doc = build_payload(
            context, track_scan, routing, capture_data, stats, target_name=resolved
        )

        # Last safe exit: past here the model call is in flight.
        svc._check_cancel(job_id)
        svc._write_progress(stem, job_id, "diagnosing")
        result = diagnose_track(payload_doc)
        return {
            "track": resolved,
            "diagnosis": json.loads(result.model_dump_json()),
            # The exact measured payload the provider saw. The panel's
            # Evidence section resolves finding.evidence_refs[].path against
            # this document; without it a thin client cannot show measured
            # values without re-deriving them (which it must never do).
            "payload": payload_doc,
        }
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


def _job_enable_capture(svc, job, stem, job_id):
    return bridge.enable_capture()


def _job_validate_provider(svc, job, stem, job_id):
    payload = job.get("payload") or {}
    api_key = payload.get("api_key")
    if api_key is None:
        provider, profile = AnthropicProvider.from_config()
        key_name = None
    elif isinstance(api_key, str) and api_key.strip():
        api_key = api_key.strip()
        provider, profile, key_name = AnthropicProvider.from_api_key(api_key)
    else:
        raise JobRefused("bad_job", "payload.api_key must be a non-empty string")
    svc._write_progress(stem, job_id, "validating_access")
    provider.validate_access(profile)
    if key_name is not None:
        config.set_file_value(key_name, api_key)
    return {"validated": True, "model": profile.model}


def _loaded_diagnosis(payload):
    diagnosis = payload.get("diagnosis")
    if not isinstance(diagnosis, dict):
        raise JobRefused(
            "bad_job", "payload.diagnosis (a DiagnosisResult object) is required"
        )
    return preview_mod.load_diagnosis(json.dumps(diagnosis))


def _loaded_adjusted_diagnosis(payload):
    result = _loaded_diagnosis(payload)
    if "proposed_value" not in payload:
        return result
    try:
        return adjust_proposal(result, payload["proposed_value"])
    except ValueError as error:
        raise JobRefused("bad_adjustment", str(error)) from None


def _job_preview_fix(svc, job, stem, job_id):
    payload = job.get("payload") or {}
    result = _loaded_adjusted_diagnosis(payload)
    seconds = _validated_seconds(payload)
    # Cancellation is honored here and never again: once preview_change runs,
    # the apply -> capture -> restore sequence must finish so restore-always
    # holds. run_preview's try/finally owns the project state from here.
    svc._check_cancel(job_id)
    svc._write_progress(stem, job_id, "previewing")
    return preview_mod.run_preview(
        result, seconds=seconds, keep_wav=bool(payload.get("keep_wav"))
    )


def _job_commit_fix(svc, job, stem, job_id):
    payload = job.get("payload") or {}
    result = _loaded_adjusted_diagnosis(payload)
    seconds = _validated_seconds(payload)
    svc._check_cancel(job_id)
    svc._write_progress(stem, job_id, "committing")
    return preview_mod.run_commit(result, seconds=seconds)


def _job_cancel_job(svc, job, stem, job_id):
    """A cancel that reaches the FRONT of the queue: its target is neither
    running nor ahead of it, so look for the target in the inbox. In-flight
    cancellation is handled by _check_cancel scans between stages."""
    target_id = (job.get("payload") or {}).get("target_id")
    if not isinstance(target_id, str) or not target_id:
        raise JobRefused("bad_job", "payload.target_id (string) is required")
    for name in sorted(os.listdir(svc.inbox)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(svc.inbox, name)
        target_stem = name[: -len(".json")]
        try:
            with open(path, encoding="utf-8") as f:
                queued = json.load(f)
        except (OSError, ValueError):
            continue
        queued_id = (
            svc._job_id(queued, target_stem) if isinstance(queued, dict) else target_stem
        )
        if queued_id != target_id:
            continue
        try:
            os.unlink(path)
        except OSError:
            continue
        svc._write_result(
            target_stem, target_id, ok=False,
            error={"code": "cancelled", "message": "cancelled while queued"},
        )
        return {"cancelled": target_id}
    raise JobRefused(
        "nothing_to_cancel",
        f"no queued or running job with id {target_id!r}; it may have finished.",
    )


def _job_record_feedback(svc, job, stem, job_id):
    """Phase 5 stub: no feedback is lost, no history UI yet. Appends one JSONL
    line per job to feedback.jsonl in the app-data root."""
    payload = job.get("payload")
    if not isinstance(payload, dict) or not payload:
        raise JobRefused("bad_job", "payload (a non-empty object) is required")
    entry = {"recorded_at": _utc_now(), "job_id": job_id, **payload}
    path = os.path.join(svc.root, "feedback.jsonl")
    # O_APPEND prevents interleaving from concurrent appends: each write
    # atomically seeks to end and appends. A single os.write of the full
    # line minimizes the window. A crash or power loss may still leave a
    # partial final line; readers must tolerate trailing lines that fail
    # JSON parsing.
    line = (json.dumps(entry) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        n = os.write(fd, line)
        if n != len(line):
            raise OSError(f"short write: {n}/{len(line)} bytes")
    finally:
        os.close(fd)
    return {"recorded": True}


def _job_record_mcp_handoff(svc, job, stem, job_id):
    """Accept the MCP client's completed diagnosis through the sidecar.

    The panel reads this only through get_status, so the sidecar remains the
    sole owner of onboarding state and validation.
    """
    payload = job.get("payload") or {}
    tracks = payload.get("tracks")
    summary = payload.get("diagnosis_summary")
    receipt_id = payload.get("receipt_id")
    if (
        not isinstance(tracks, list)
        or len(tracks) != 1
        or not isinstance(tracks[0], str)
        or not tracks[0].strip()
    ):
        raise JobRefused(
            "bad_job", "payload.tracks must contain exactly one track name"
        )
    if not isinstance(summary, str) or len(summary.strip()) < 20:
        raise JobRefused(
            "bad_job", "payload.diagnosis_summary must be at least 20 characters"
        )
    try:
        with open(os.path.join(svc.root, "mcp-receipt.json"), encoding="utf-8") as f:
            receipt = json.load(f)
        received = datetime.fromisoformat(
            str(receipt.get("received_at", "")).replace("Z", "+00:00")
        )
    except (OSError, ValueError, AttributeError):
        raise JobRefused(
            "mcp_receipt_missing", "no measured MCP Track Check receipt is available"
        ) from None
    age = (datetime.now(timezone.utc) - received).total_seconds()
    if (
        not isinstance(receipt_id, str)
        or receipt_id != receipt.get("receipt_id")
        or receipt.get("tracks") != [tracks[0].strip()]
        or receipt.get("seconds") != 10
        or age < 0 or age > 15 * 60
    ):
        raise JobRefused(
            "mcp_receipt_invalid",
            "the MCP diagnosis does not match a fresh 10-second Track Check",
        )
    handoff = {
        "tracks": [tracks[0].strip()],
        "diagnosis_summary": summary.strip(),
        "delivered_at": _utc_now(),
    }
    svc._atomic_write_json(os.path.join(svc.root, "mcp-handoff.json"), handoff)
    try:
        os.unlink(os.path.join(svc.root, "mcp-receipt.json"))
    except OSError:
        pass
    return handoff


def _job_record_mcp_measurement(svc, job, stem, job_id):
    payload = job.get("payload") or {}
    receipt_id = payload.get("receipt_id")
    tracks = payload.get("tracks")
    seconds = payload.get("seconds")
    if not isinstance(receipt_id, str) or not re.fullmatch(r"[a-f0-9]{32,128}", receipt_id):
        raise JobRefused("bad_job", "payload.receipt_id must be an unguessable hex token")
    if (
        not isinstance(tracks, list) or len(tracks) != 1
        or not isinstance(tracks[0], str) or not tracks[0].strip()
    ):
        raise JobRefused("bad_job", "payload.tracks must contain exactly one track name")
    if seconds != 10:
        raise JobRefused("bad_job", "MCP onboarding requires a 10-second Track Check")
    svc._atomic_write_json(os.path.join(svc.root, "mcp-receipt.json"), {
        "receipt_id": receipt_id,
        "tracks": [tracks[0].strip()],
        "seconds": 10,
        "received_at": _utc_now(),
    })
    return {"recorded": True}


_HANDLERS = {
    "get_status": _job_get_status,
    "enable_capture": _job_enable_capture,
    "validate_provider": _job_validate_provider,
    "track_check": _job_track_check,
    "preview_fix": _job_preview_fix,
    "commit_fix": _job_commit_fix,
    "cancel_job": _job_cancel_job,
    "record_feedback": _job_record_feedback,
    "record_mcp_measurement": _job_record_mcp_measurement,
    "record_mcp_handoff": _job_record_mcp_handoff,
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="postmortem-service",
        description="Post Mortem sidecar: watches a jobs folder and runs "
        "engine calls for the panel. See docs/SIDECAR_PROTOCOL.md.",
    )
    parser.add_argument(
        "--data-dir", help="override the app-data root (default: platform dir)"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="process pending jobs and exit (acquires lock, no loop)",
    )
    args = parser.parse_args(argv)

    service = Service(root=os.path.expanduser(args.data_dir) if args.data_dir else None)
    if args.once:
        try:
            service.acquire_lock()
        except JobRefused as e:
            print(f"[postmortem-service] {e}", file=sys.stderr)
            return 1
        try:
            service.sweep_interrupted()
            service.sweep_orphan_wavs()
            count = service.run_once()
            print(f"[postmortem-service] processed {count} job(s)", file=sys.stderr)
            return 0
        finally:
            service.release_lock()
    try:
        service.run_forever()
    except JobRefused as e:
        print(f"[postmortem-service] {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
