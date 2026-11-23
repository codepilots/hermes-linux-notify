"""linux-notify: local freedesktop desktop notifications for Hermes Agent on Linux.

Events:
  on_human_input_request    -> "Hermes needs approval / has a question / needs your sudo password"
  on_human_input_resolved   -> closes that notification
  on_session_end (per turn) -> "Hermes: task complete" / "Hermes: task stopped"
                               for turns that ran >= completion_min_seconds
  pre_llm_call              -> records when a turn started (timing only)
  subagent_start            -> remembers child sessions so subagents never notify

Every notify-send / gdbus call runs on a short-lived daemon thread, so hooks never
block the agent. Nothing leaves the machine: notifications go over the local D-Bus
session bus to whatever notification daemon is running.
"""

from __future__ import annotations

import html
import logging
import os
import shutil
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

APP_NAME = "Hermes"
_NOTIFY = shutil.which("notify-send") or "/usr/bin/notify-send"
_GDBUS = shutil.which("gdbus")

_lock = threading.Lock()
_turn_start: dict[str, float] = {}      # session_id -> monotonic start
_subagent_sessions: set[str] = set()     # child session ids (never notify)
_last_done: dict[str, float] = {}        # session_id -> last completion notify time (dedupe)
_input_ids: dict[str, int] = {}          # request_id -> notification id
_pending: set[str] = set()               # request_ids still awaiting an answer

_KIND_TITLES = {
    "approval": "Hermes needs approval",
    "clarify": "Hermes has a question",
    "sudo": "Hermes needs your sudo password",
}


def _env() -> dict:
    env = dict(os.environ)
    uid = os.getuid()
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    return env


def _clip(text, n: int) -> str:
    if not isinstance(text, str):
        text = str(text or "")
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _send(title: str, body: str, urgency: str, icon: str, category: str,
          request_id: str | None = None) -> None:
    def run():
        cmd = [_NOTIFY, "--app-name", APP_NAME, "--urgency", urgency,
               "--category", category, "--print-id"]
        if icon:
            cmd += ["--icon", icon]
        cmd += [title, html.escape(body, quote=False)]
        try:
            out = subprocess.run(cmd, env=_env(), capture_output=True, text=True, timeout=10)
            if out.returncode != 0:
                logger.debug("notify-send failed: %s", out.stderr.strip())
                return
            if request_id:
                try:
                    nid = int(out.stdout.strip())
                except ValueError:
                    return
                with _lock:
                    still_pending = request_id in _pending
                    if still_pending:
                        _input_ids[request_id] = nid
                if not still_pending:  # answered before notify-send returned
                    _close(nid)
        except Exception as exc:  # never let a notifier break the agent
            logger.debug("notify-send error: %s", exc)

    threading.Thread(target=run, name="linux-notify", daemon=True).start()



def _close(nid: int) -> None:
    if not _GDBUS:
        return
    try:
        subprocess.run(
            [_GDBUS, "call", "--session", "--dest", "org.freedesktop.Notifications",
             "--object-path", "/org/freedesktop/Notifications",
             "--method", "org.freedesktop.Notifications.CloseNotification", str(nid)],
            env=_env(), capture_output=True, timeout=5,
        )
    except Exception as exc:
        logger.debug("close notification failed: %s", exc)


def register(ctx):
    def cfg(key, default):
        try:
            val = ctx.get_config(key, default=default)
        except Exception:
            return default
        return default if val is None else val

    def skipped(platform) -> bool:
        skip = cfg("skip_platforms", ["cron"]) or []
        return bool(platform) and platform in skip

    # --- turn timing -------------------------------------------------------
    def on_pre_llm_call(session_id=None, platform=None, **_):
        if session_id and not skipped(platform):
            with _lock:
                _turn_start[session_id] = time.monotonic()
        return None

    def on_subagent_start(child_session_id=None, **_):
        if child_session_id:
            with _lock:
                _subagent_sessions.add(child_session_id)

    def on_session_end(session_id=None, completed=False, failed=False, interrupted=False,
                       turn_exit_reason=None, platform=None, **_):
        if not session_id or not cfg("notify_completion", True) or skipped(platform):
            return
        with _lock:
            start = _turn_start.pop(session_id, None)
            if session_id in _subagent_sessions:
                return
            if start is None:  # no matching turn start (e.g. CLI exit-handler duplicate)
                return
            now = time.monotonic()
            if now - _last_done.get(session_id, 0) < 5:
                return
            elapsed = now - start
            if elapsed < int(cfg("completion_min_seconds", 30)):
                return
            _last_done[session_id] = now
        mins, secs = divmod(int(elapsed), 60)
        took = f"{mins}m {secs:02d}s" if mins else f"{secs}s"
        where = f" · {platform}" if platform else ""
        if completed and not failed:
            _send("Hermes: task complete", f"Finished in {took}{where}",
                  "normal", cfg("icon", "hermes-desktop"), "transfer.complete")
        elif cfg("notify_failures", True) and not interrupted:
            reason = f" ({turn_exit_reason})" if turn_exit_reason else ""
            _send("Hermes: task stopped", f"Ended without finishing after {took}{reason}{where}",
                  "normal", cfg("icon", "hermes-desktop"), "transfer.error")

    # --- needs you ---------------------------------------------------------
    def on_input_request(kind=None, request_id=None, platform=None, prompt="", **_):
        if not cfg("notify_input", True) or skipped(platform):
            return
        title = _KIND_TITLES.get(kind or "", "Hermes needs your attention")
        body = _clip(prompt, 220) or "Waiting for you"
        if platform:
            body += f"\n({platform})"
        urgency = str(cfg("input_urgency", "critical"))
        if urgency not in ("low", "normal", "critical"):
            urgency = "critical"
        rid = request_id if cfg("close_on_resolve", True) else None
        if rid:
            with _lock:
                _pending.add(rid)
        _send(title, body, urgency, cfg("icon", "hermes-desktop"), "im.received", rid)

    def on_input_resolved(request_id=None, **_):
        if not request_id:
            return
        with _lock:
            _pending.discard(request_id)
            nid = _input_ids.pop(request_id, None)
        if nid is not None:
            threading.Thread(target=_close, args=(nid,), daemon=True).start()

    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("subagent_start", on_subagent_start)
    ctx.register_hook("on_session_end", on_session_end)
    ctx.register_hook("on_human_input_request", on_input_request)
    ctx.register_hook("on_human_input_resolved", on_input_resolved)
