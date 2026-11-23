"""Unit tests for linux-notify. No D-Bus needed: subprocess.run is mocked."""

import importlib.util
import pathlib
import subprocess
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_plugin():
    spec = importlib.util.spec_from_file_location("linux_notify_under_test", ROOT / "__init__.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod  # type: ignore[return-value]


class FakeCtx:
    def __init__(self, settings=None):
        self.hooks = {}
        self.settings = settings or {}

    def register_hook(self, name, cb):
        self.hooks[name] = cb

    def get_config(self, key, default=None):
        return self.settings.get(key, default)


class Base(unittest.TestCase):
    settings: dict = {}

    def setUp(self):
        self.mod = load_plugin()
        self.ctx = FakeCtx(dict(self.settings))
        self.mod.register(self.ctx)
        self.calls = []
        self.next_id = 41

        def fake_run(cmd, **kw):
            self.calls.append(cmd)
            self.next_id += 1
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{self.next_id}\n", stderr="")

        patcher = mock.patch.object(self.mod.subprocess, "run", side_effect=fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)
        setattr(self.mod, "_GDBUS", "/usr/bin/gdbus")

    def fire(self, name, **kw):
        self.ctx.hooks[name](**kw)
        time.sleep(0.05)  # let the notifier thread run

    def notifications(self):
        return [c for c in self.calls if c[0].endswith("notify-send")]

    def closes(self):
        return [c for c in self.calls if "CloseNotification" in " ".join(c)]

    def finish_turn(self, sid="s1", seconds=60, platform="cli", **kw):
        self.fire("pre_llm_call", session_id=sid, platform=platform)
        with self.mod._lock:
            self.mod._turn_start[sid] -= seconds
        self.fire("on_session_end", session_id=sid, platform=platform, **kw)


class TestRegistration(Base):
    def test_registers_declared_hooks(self):
        self.assertEqual(set(self.ctx.hooks), {
            "pre_llm_call", "on_session_end", "subagent_start",
            "on_human_input_request", "on_human_input_resolved"})


class TestInput(Base):
    def test_approval_notifies_and_closes_on_resolve(self):
        self.fire("on_human_input_request", kind="approval", request_id="r1",
                  platform="cli", prompt="rm -rf ./build")
        (n,) = self.notifications()
        self.assertIn("Hermes needs approval", n)
        self.assertEqual(n[n.index("--urgency") + 1], "critical")
        self.fire("on_human_input_resolved", request_id="r1", outcome="approved")
        self.assertEqual(len(self.closes()), 1)

    def test_prompt_is_html_escaped_and_clipped(self):
        self.fire("on_human_input_request", kind="clarify", request_id="r2",
                  prompt="<b>pick</b> " + "x" * 500)
        body = self.notifications()[0][-1]
        self.assertIn("&lt;b&gt;", body)
        self.assertLess(len(body), 300)

    def test_resolved_before_id_known_still_closes(self):
        # Resolve lands before the notifier thread records the id.
        with mock.patch.object(self.mod.threading, "Thread") as T:
            self.ctx.hooks["on_human_input_request"](kind="sudo", request_id="r3", prompt="")
            target = T.call_args.kwargs["target"]
        self.ctx.hooks["on_human_input_resolved"](request_id="r3")
        target()  # notify-send "returns" after the prompt was answered
        self.assertEqual(len(self.closes()), 1)

    def test_skipped_platform(self):
        self.fire("on_human_input_request", kind="approval", request_id="r4", platform="cron")
        self.assertEqual(self.notifications(), [])


class TestCompletion(Base):
    def test_long_turn_notifies(self):
        self.finish_turn(completed=True)
        (n,) = self.notifications()
        self.assertIn("Hermes: task complete", n)
        self.assertIn("1m 00s", n[-1])

    def test_short_turn_silent(self):
        self.finish_turn(seconds=2, completed=True)
        self.assertEqual(self.notifications(), [])

    def test_interrupt_silent_failure_notifies(self):
        self.finish_turn(sid="a", completed=False, interrupted=True)
        self.assertEqual(self.notifications(), [])
        self.finish_turn(sid="b", completed=False, failed=True, turn_exit_reason="budget_exhausted")
        (n,) = self.notifications()
        self.assertIn("Hermes: task stopped", n)
        self.assertIn("budget_exhausted", n[-1])

    def test_subagent_silent(self):
        self.fire("subagent_start", child_session_id="child")
        self.finish_turn(sid="child", completed=True)
        self.assertEqual(self.notifications(), [])

    def test_duplicate_end_ignored(self):
        self.finish_turn(completed=True)
        self.fire("on_session_end", session_id="s1", completed=True)
        self.assertEqual(len(self.notifications()), 1)


class TestDisabled(Base):
    settings = {"notify_input": False, "notify_completion": False}

    def test_everything_off(self):
        self.fire("on_human_input_request", kind="approval", request_id="r", prompt="x")
        self.finish_turn(completed=True)
        self.assertEqual(self.notifications(), [])


if __name__ == "__main__":
    unittest.main()
