"""Run a Linux build in its own process group, with bounded shutdown and logs."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def stop_group(pgid, grace):
    if not group_exists(pgid):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def resources(path, stage):
    with Path(path).open("a", encoding="utf-8") as log:
        log.write(f"\n[{datetime.now(timezone.utc).isoformat()}] {stage}\n")
        for name in ("/proc/meminfo", "/proc/pressure/memory", "/sys/fs/cgroup/memory.events",
                     "/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.peak"):
            try:
                log.write(name + "\n" + Path(name).read_text() + "\n")
            except OSError as error:
                log.write(name + ": " + str(error) + "\n")
        try:
            result = subprocess.run(["ps", "-eo", "pid,ppid,pgid,stat,rss,comm", "--sort=-rss"],
                                    capture_output=True, text=True, timeout=3)
            log.write("\n".join(result.stdout.splitlines()[:16]) + "\n")
        except (OSError, subprocess.TimeoutExpired) as error:
            log.write(str(error) + "\n")


def run(command, log_path, timeout=7200, grace=3):
    if not hasattr(os, "killpg"):
        raise ValueError("The kernel build supervisor requires Linux/POSIX")
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    resource_path = log_path.parent / "build-resources.log"
    resources(resource_path, "before build")
    received = []
    handlers = {}
    process = None
    status = 1
    # A regular file avoids waiting for EOF from an orphan holding a pipe open.
    with log_path.open("ab", buffering=0) as writer, log_path.open("rb") as reader:
        def message(value):
            writer.write((value + "\n").encode())

        def output():
            for _ in range(16):
                data = reader.read(65536)
                if not data:
                    break
                sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                handlers[sig] = signal.signal(sig, lambda signum, _frame: received.append(signum))
            process = subprocess.Popen(command, stdout=writer, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + timeout
            while True:
                output()
                if received:
                    status = 128 + received[0]
                    message(f"Build received signal {received[0]}; stopping its process group")
                    break
                code = process.poll()
                if code is not None:
                    status = code if code >= 0 else 128 - code
                    break
                if time.monotonic() >= deadline:
                    status = 124
                    message(f"Build exceeded {timeout}s; stopping its process group")
                    break
                time.sleep(0.2)
        finally:
            if process is not None:
                stop_group(process.pid, grace)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    message("Build did not exit after SIGKILL; runner/kernel intervention is required")
            resources(resource_path, f"after build: exit {status}")
            # Bound the final drain even if a host/kernel fault leaves a writer
            # alive after SIGKILL. Its continued writes cannot hold us open.
            remaining = log_path.stat().st_size - reader.tell()
            while remaining > 0:
                data = reader.read(min(65536, remaining))
                if not data:
                    break
                sys.stdout.buffer.write(data)
                remaining -= len(data)
            sys.stdout.buffer.flush()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default="build-logs/nothing-build.log")
    parser.add_argument("--timeout", type=float, default=7200)
    parser.add_argument("--grace", type=float, default=3)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.timeout <= 0 or args.grace < 0:
        parser.error("provide a command, a positive timeout and a nonnegative shutdown grace")
    sys.exit(run(command, args.log, args.timeout, args.grace))


if __name__ == "__main__":
    main()
