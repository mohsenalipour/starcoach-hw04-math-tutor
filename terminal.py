"""Small, dependency-free terminal presentation; never puts colors in agent state."""

from __future__ import annotations

import os
import re
import shutil
import sys

RESET = "\033[0m"
STYLES = {
    "question": "1;36", "assistant": "1;35", "answer": "32",
    "heading": "1;33", "result": "1;4;32", "math": "1;33",
    "muted": "90", "error": "1;31", "success": "1;32",
}
INLINE = re.compile(r"((?<![\w*])\*\*[^*\n]+\*\*(?![\w*])|`[^`\n]+`)")
ESCAPES = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")


def clean_text(text: str) -> str:
    # Only our renderer may emit terminal control sequences.
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", ESCAPES.sub("", text))


def enable_windows_colors() -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetConsoleMode.restype = wintypes.BOOL
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.SetConsoleMode.restype = wintypes.BOOL
        for code in (-11, -12):  # Standard output / standard error.
            handle = kernel.GetStdHandle(code & 0xFFFFFFFF)
            mode = wintypes.DWORD()
            if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel.SetConsoleMode(handle, mode.value | 0x0004)
    except (AttributeError, OSError):
        pass


class TerminalUI:
    def __init__(self) -> None:
        self.output, self.errors = sys.stdout, sys.stderr
        permitted = "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
        self.color = permitted and self.output.isatty()
        self.error_color = permitted and self.errors.isatty()
        if self.color or self.error_color:
            enable_windows_colors()

    def paint(self, text: str, role: str, *, stderr: bool = False) -> str:
        text = clean_text(text)
        enabled = self.error_color if stderr else self.color
        return f"\033[{STYLES[role]}m{text}{RESET}" if enabled else text

    def message(self, text: str, role: str = "muted", *, stderr: bool = False) -> None:
        print(self.paint(text, role, stderr=stderr), file=self.errors if stderr else self.output)

    def welcome(self) -> None:
        self.message("MILO -- Mathematics Tutor", "assistant")
        self.message("English / Persian | /help: commands | /exit: quit\n")

    def read_question(self) -> str:
        prompt = self.paint("You>", "question") + " "
        if self.color:
            prompt += "\033[36m"  # Keep the typed question cyan as the terminal echoes it.
        try:
            return input(prompt).strip()
        finally:
            if self.color:
                self.output.write(RESET)
                self.output.flush()

    def question(self, text: str) -> None:
        self.message("\nYou> " + text, "question")

    def inline(self, text: str, role: str) -> str:
        parts = INLINE.split(clean_text(text))
        rendered = []
        for part in parts:
            if INLINE.fullmatch(part):
                content = part[2:-2] if part.startswith("**") else part[1:-1]
                rendered.append(self.paint(content, "math"))
            else:
                rendered.append(self.paint(part, role))
        return "".join(rendered)

    def answer(self, state: dict) -> None:
        role = {"unverified": "error", "needs_clarification": "heading",
                "out_of_scope": "heading"}.get(state["status"], "answer")
        self.message("\nMilo>", "assistant")
        width = max(20, min(72, shutil.get_terminal_size((80, 24)).columns - 1))
        self.message("-" * width)
        for line in state["final_answer"].splitlines():
            if line.startswith(("Result: ", "پاسخ: ")):
                rendered = self.paint(line, "result" if role == "answer" else role)
            else:
                step = re.match(r"^(\d+[.)])(\s+)(.*)$", line)
                heading = re.match(r"^(Example:|مثال:|Check your understanding:|برای بررسی یادگیری:)(\s*)(.*)$", line)
                if step:
                    rendered = self.paint(step[1], "question") + step[2] + self.inline(step[3], role)
                elif heading:
                    rendered = self.paint(heading[1], "heading") + heading[2] + self.inline(heading[3], role)
                else:
                    rendered = self.inline(line, role)
            print(rendered, file=self.output)
        print(file=self.output)

    def trace(self, state: dict, *, stderr: bool = False) -> None:
        self.message("Route: " + " -> ".join(event["node"] for event in state["trace"]),
                     "question", stderr=stderr)
        role = "success" if state["status"] == "answered" else "error" if state["status"] == "unverified" else "heading"
        self.message(f"Status: {state['status']} | Verification: {state['verification']}", role, stderr=stderr)
