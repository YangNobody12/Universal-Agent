"""
Run AI coding agents ("harnesses") non-interactively: Claude Code, Codex CLI, OpenCode.

Each run sends the task on stdin (never on the command line, so Windows' cmd.exe never parses it), streams
the agent's output line by line, and reports the final answer. Two modes:
  ask   read-only: the agent may read files and answer, but not change anything
  edit  the agent may edit files inside the working directory

    run = HarnessRun("claude", "explain what main.py does", cwd=r"D:\\project",
                     on_line=print, on_done=lambda ok, answer, secs: print(answer))
    run.start(); ...; run.cancel()
"""

import glob
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
CREATE_NO_WINDOW = 0x08000000


def _resolve(name: str):
    """Path of a CLI's real executable. npm installs .cmd shims that only forward to an .exe; we call the
    .exe directly so nothing goes through cmd.exe."""
    path = shutil.which(name)
    if not path:
        return None
    if path.lower().endswith((".cmd", ".bat")):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                m = re.search(r'"%dp0%\\([^"]+?\.exe)"', f.read())
        except OSError:
            return None
        exe = os.path.join(os.path.dirname(path), m.group(1)) if m else None
        return exe if exe and os.path.exists(exe) else None
    return path


def _find_codex():
    found = _resolve("codex")
    if found:
        return found
    # The ChatGPT / Codex VS Code extension ships its own codex.exe
    hits = sorted(glob.glob(os.path.expanduser(r"~\.vscode\extensions\openai.chatgpt-*\bin\windows-x86_64\codex.exe")))
    return hits[-1] if hits else None


HARNESSES = {
    "claude": {
        "label": "Claude Code",
        "find": lambda: _resolve("claude"),
        # -p: one task, print the answer, exit. Default permissions in -p deny edits and shell commands.
        "args": lambda mode, cwd, model, out: ["-p", "--output-format", "text"]
        + (["--permission-mode", "acceptEdits"] if mode == "edit" else [])
        + (["--model", model] if model else []),
    },
    "codex": {
        "label": "Codex CLI",
        "find": _find_codex,
        "args": lambda mode, cwd, model, out: [
            "exec", "--skip-git-repo-check", "--ephemeral", "-C", cwd, "-o", out,
            "-s", "workspace-write" if mode == "edit" else "read-only",
        ] + (["-m", model] if model else []) + ["-"],
        "answer_file": True,  # -o writes just the final message; stdout also carries the agent's progress
    },
    "opencode": {
        "label": "OpenCode",
        "find": lambda: _resolve("opencode"),
        # built-in agents: "plan" can't edit, "build" can
        "args": lambda mode, cwd, model, out: ["run", "--dir", cwd, "--agent", "build" if mode == "edit" else "plan"]
        + (["-m", model] if model else []),
    },
}


def available() -> dict:
    """{name: executable} for the harnesses installed on this machine."""
    found = {}
    for name, h in HARNESSES.items():
        exe = h["find"]()
        if exe:
            found[name] = exe
    return found


class HarnessRun:
    def __init__(self, name: str, task: str, cwd: str, mode: str = "ask", model: str = None,
                 on_line=None, on_done=None, timeout: float = 900):
        if name not in HARNESSES:
            raise ValueError(f"unknown harness {name!r}")
        if mode not in ("ask", "edit"):
            raise ValueError("mode must be 'ask' or 'edit'")
        self.name, self.task, self.cwd, self.mode, self.model = name, task, cwd, mode, model
        self.on_line = on_line or (lambda line: None)
        self.on_done = on_done or (lambda ok, answer, secs: None)
        self.timeout = timeout
        self.proc = None
        self.cancelled = False
        self.timed_out = False

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            # the agents spawn their own children (shells, tools): end the whole tree
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)

    def _time_out(self):
        self.timed_out = True
        self.cancel()

    def _run(self):
        h = HARNESSES[self.name]
        t0 = time.perf_counter()
        exe = h["find"]()
        if not exe:
            self.on_done(False, f"{h['label']} is not installed (or not on PATH).", 0.0)
            return
        if not os.path.isdir(self.cwd):
            self.on_done(False, f"Working directory not found: {self.cwd}", 0.0)
            return
        out_file = os.path.join(tempfile.gettempdir(), f"harness_{self.name}_{os.getpid()}_{id(self)}.txt")
        cmd = [exe] + h["args"](self.mode, self.cwd, self.model, out_file)
        env = dict(os.environ, NO_COLOR="1", FORCE_COLOR="0", TERM="dumb")
        lines = []
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env, creationflags=CREATE_NO_WINDOW)
            self.proc.stdin.write(self.task)
            self.proc.stdin.close()
            timer = threading.Timer(self.timeout, self._time_out)
            timer.start()
            try:
                for raw in self.proc.stdout:
                    line = ANSI_RE.sub("", raw).rstrip()
                    lines.append(line)
                    if line.strip():
                        self.on_line(line)
                code = self.proc.wait()
            finally:
                timer.cancel()
        except Exception as e:
            self.on_done(False, f"Could not run {h['label']}: {e}", time.perf_counter() - t0)
            return

        answer = ""
        if h.get("answer_file") and os.path.exists(out_file):
            with open(out_file, encoding="utf-8", errors="replace") as f:
                answer = f.read().strip()
        try:
            os.remove(out_file)
        except OSError:
            pass
        if not answer:
            answer = "\n".join(lines).strip()
        secs = time.perf_counter() - t0
        if self.cancelled:
            self.on_done(False, f"Timed out after {self.timeout:.0f} s." if self.timed_out else "Stopped.", secs)
        else:
            self.on_done(code == 0, answer or f"(no output, exit code {code})", secs)
