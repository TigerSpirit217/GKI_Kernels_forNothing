import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from nothing import run as runner


class SupervisorTests(unittest.TestCase):
    def exercise(self, code=17, timeout=10, signal_received=False):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "build.log"
            process = Mock(pid=1234)
            process.poll.return_value = code
            process.wait.return_value = code
            if signal_received:
                def send_signal():
                    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                    return None
                process.poll.side_effect = send_signal
            def launch(*args, **kwargs):
                self.assertTrue(kwargs["start_new_session"])
                self.assertIs(kwargs["stderr"], subprocess.STDOUT)
                kwargs["stdout"].write(b"compiler output\n")
                return process
            output = io.BytesIO()
            original_handler = signal.getsignal(signal.SIGTERM)
            with patch.object(runner.os, "killpg", create=True), \
                 patch.object(runner.subprocess, "Popen", side_effect=launch), \
                 patch.object(runner, "resources"), patch.object(runner, "stop_group") as stop, \
                 patch.object(runner.time, "monotonic", side_effect=[0, 1]), \
                 patch.object(runner.time, "sleep"), \
                 patch.object(runner.sys, "stdout", SimpleNamespace(buffer=output)):
                result = runner.run(["build"], log, timeout=timeout, grace=0.2)
                stop.assert_called_once_with(1234, 0.2)
            self.assertEqual(signal.getsignal(signal.SIGTERM), original_handler)
            self.assertIn(b"compiler output", output.getvalue())
            self.assertIn(b"compiler output", log.read_bytes())
            return result, output.getvalue()

    def test_failure_and_signal_exit_codes_are_preserved(self):
        self.assertEqual(self.exercise(code=17)[0], 17)
        self.assertEqual(self.exercise(code=-15)[0], 143)

    def test_timeout_stops_group_and_returns_124(self):
        status, output = self.exercise(code=None, timeout=0.5)
        self.assertEqual(status, 124)
        self.assertIn(b"exceeded", output)

    def test_runner_termination_stops_build_group_and_preserves_143(self):
        status, output = self.exercise(code=None, signal_received=True)
        self.assertEqual(status, 143)
        self.assertIn(b"received signal 15", output)

    def test_stubborn_group_is_killed_after_grace(self):
        with patch.object(runner, "group_exists", return_value=True), \
             patch.object(runner.os, "killpg", create=True) as kill, \
             patch.object(runner.signal, "SIGKILL", 9, create=True):
            runner.stop_group(1234, 0)
            self.assertEqual(kill.call_args_list, [call(1234, signal.SIGTERM), call(1234, 9)])

    def test_exited_group_does_not_receive_unnecessary_signals(self):
        with patch.object(runner, "group_exists", return_value=False), \
             patch.object(runner.os, "killpg", create=True) as kill:
            runner.stop_group(1234, 0)
            kill.assert_not_called()


@unittest.skipUnless(os.name == "posix", "Real process-group checks run on Linux CI")
class ProcessGroupTests(unittest.TestCase):
    def run_case(self, parent_exits):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pidfile = root / "child.pid"
            child = ("import os,signal,time; from pathlib import Path; "
                     "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                     f"Path({str(pidfile)!r}).write_text(str(os.getpid())); "
                     "print('linker running',flush=True); time.sleep(60)")
            parent = ("import subprocess,sys,time; from pathlib import Path; "
                      f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
                      f"p=Path({str(pidfile)!r}); "
                      "deadline=time.monotonic()+3\n"
                      "while not p.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
                      + ("sys.exit(17)\n" if parent_exits else "time.sleep(60)\n"))
            started = time.monotonic()
            try:
                result = subprocess.run([sys.executable, "-m", "nothing.run", "--log", str(root / "build.log"),
                                         "--timeout", "1", "--grace", "0.2", "--", sys.executable, "-c", parent],
                                        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=8)
                self.assertEqual(result.returncode, 17 if parent_exits else 124, result.stdout + result.stderr)
                self.assertLess(time.monotonic() - started, 6)
                self.assertIn("linker running", (root / "build.log").read_text())
                pid = int(pidfile.read_text())
                # A killed orphan can briefly remain as a zombie awaiting PID 1.
                stat = Path(f"/proc/{pid}/stat")
                def active():
                    try:
                        return stat.read_text().split()[2] != "Z"
                    except FileNotFoundError:
                        return False
                deadline = time.monotonic() + 1
                while active() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(active(), "linker survived cleanup")
            finally:
                if pidfile.exists():
                    try:
                        os.kill(int(pidfile.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_orphan_holding_stdout_and_ignoring_term_cannot_hang_failed_build(self):
        self.run_case(parent_exits=True)

    def test_timeout_kills_stubborn_compiler_descendants(self):
        self.run_case(parent_exits=False)
