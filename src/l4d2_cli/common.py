"""Shared error, output and bounded process handling."""
from __future__ import annotations

import json
import codecs
from collections import deque
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


class CliError(Exception):
    def __init__(self, message: str, code: str = "operation_failed", exit_code: int = 1, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.details = details or {}


class Redactor:
    def __init__(self) -> None:
        self.values: set[str] = set()

    def register(self, value: str) -> None:
        if value:
            self.values.add(value)
            self.values.add(json.dumps(value, ensure_ascii=False)[1:-1])
            self.values.add(json.dumps(value, ensure_ascii=True)[1:-1])

    def clean(self, text: str) -> str:
        for value in sorted(self.values, key=len, reverse=True):
            text = text.replace(value, "REDACTED")
        text = re.sub(r"(https?://)[^/\s@]+@", r"\1REDACTED@", text)
        text = re.sub(
            r'(?i)(\b(?:rcon_password|sv_password|password|passwd|token|secret|sv_setsteamaccount)\b"?(?:\s*[=:]\s*|\s+))(?:"[^"\r\n]*"|[^\s,;}]+)',
            r'\1REDACTED', text,
        )
        return text

    def object(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.object(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.object(item) for item in value]
        return self.clean(value) if isinstance(value, str) else value


REDACTOR = Redactor()


def progress(text: str) -> None:
    print(REDACTOR.clean(text), file=sys.stderr, flush=True)


def result(command: str, data: dict, *, as_json: bool = False,
           warnings: list[str] | None = None, error: CliError | None = None) -> None:
    payload = REDACTOR.object({
        "schema_version": 1, "command": command, "ok": error is None,
        "data": data, "warnings": warnings or [],
        "error": {"code": error.code, "message": str(error)} if error else None,
    })
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        if error:
            print(f"Error [{error.code}]: {payload['error']['message']}", file=sys.stderr)
        if data:
            print(json.dumps(payload["data"], ensure_ascii=False, indent=2))
        for warning in payload["warnings"]:
            progress(f"Warning: {warning}")


def confirm(message: str, yes: bool = False, *, as_json: bool = False) -> None:
    progress(message)
    if yes:
        return
    if as_json or not sys.stdin.isatty():
        raise CliError("Confirmation required; review the plan then use --yes.", "confirmation_required", 5)
    if input("Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
        raise CliError("Operation cancelled.", "declined", 5)


def positive(value: str) -> float:
    import argparse
    import math
    try:
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError
        return number
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive finite number") from None


def deadline_remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise CliError("Operation timed out.", "timeout", 124)
    return left


def _terminate(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        elif process.poll() is None:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=5)
    if os.name == "posix":
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run(args: list[str], *, timeout: float | None = 60, cwd: Path | None = None,
        env: dict | None = None, stream: bool = False, check: bool = True,
        stream_stdout: bool = False) -> subprocess.CompletedProcess:
    """Use no shell; own the process group, redact output and bound its lifetime."""
    try:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=(os.name == "posix"))
    except OSError as exc:
        raise CliError(f"Could not run {Path(args[0]).name}: {exc}", "executable_unavailable") from exc
    chunks: deque[str] = deque()
    read_error: list[Exception] = []

    def reader() -> None:
        try:
            assert process.stdout is not None
            # Bounded reads also handle a child which writes a huge line.
            pending = ""
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            retained = 0
            discarding = False
            while block := process.stdout.read1(4096):
                decoded = decoder.decode(block)
                if discarding:
                    if "\n" not in decoded:
                        continue
                    decoded = decoded.split("\n", 1)[1]
                    discarding = False
                pending += decoded
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    cleaned = REDACTOR.clean(line)
                    # Internal decisions use the original text; only display is redacted.
                    chunks.append(line + "\n")
                    retained += len(line) + 1
                    if stream:
                        print(cleaned, file=sys.stdout if stream_stdout else sys.stderr, flush=True)
                if len(pending) > 65536:
                    # Do not leak an incomplete potentially sensitive record.
                    chunks.append("[oversized output record omitted]\n")
                    retained += 34
                    pending = ""
                    discarding = True
                while retained > 4 * 1024 * 1024 and chunks:
                    retained -= len(chunks.popleft())
            pending += decoder.decode(b"", final=True)
            if pending:
                cleaned = REDACTOR.clean(pending)
                chunks.append(pending)
                if stream:
                    print(cleaned, file=sys.stdout if stream_stdout else sys.stderr, flush=True)
        except Exception as exc:
            read_error.append(exc)

    worker = threading.Thread(target=reader, daemon=True)
    worker.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate(process)
        raise CliError(f"{Path(args[0]).name} timed out.", "timeout", 124) from None
    except BaseException:
        _terminate(process)
        raise
    finally:
        worker.join(timeout=5)
        if worker.is_alive():
            _terminate(process)
            worker.join(timeout=5)
            read_error.append(OSError("Subprocess left its output stream open"))
        if process.stdout and not worker.is_alive():
            process.stdout.close()
    output = "".join(chunks)
    if read_error:
        raise CliError("Unable to read subprocess output.", "output_failed")
    completed = subprocess.CompletedProcess(args, process.returncode, output, "")
    if check and completed.returncode:
        raise CliError(f"{Path(args[0]).name} failed ({completed.returncode}): {output[-1500:]}", "external_command_failed")
    return completed
