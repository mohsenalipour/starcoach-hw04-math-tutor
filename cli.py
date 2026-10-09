"""Interactive terminal and single-question entry points for the math tutor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent import MAX_QUESTION_LENGTH, MathTutorAgent, TutorSession
from provider import LLMProvider, TutorError, load_settings
from report import write_html_report
from terminal import TerminalUI

HELP = """Commands:
  /help                         Show this help
  /level auto|beginner|intermediate|advanced
  /language auto|fa|en           Choose the answer language
  /trace [on|off]                Show the last route, or toggle live node progress
  /state                        Show the last turn's complete JSON state
  /save filename.json           Save the last state (contains no API key)
  /file question.txt            Read a UTF-8 question file and send it in this conversation
  /html report.html             Export the last answer as a readable HTML report
  /graph                        Show the actual graph in Mermaid notation
  /new                          Start a fresh conversation; keep level/language
  /exit                         Quit (Ctrl+D/EOF also quits)

Examples:
  Explain derivatives to a beginner using an everyday example
  Differentiate x^3 + 2*x with respect to x, step by step
  Solve 2*x + 3 = 7 step by step
  /file demo/problem-fa.txt
"""


class GraphOnlyProvider:
    def structured(self, *args, **kwargs):
        raise TutorError("Graph-only mode cannot call a model.")


def utf8_terminal() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MILO — Complex Mathematics Tutor (CLI)")
    parser.add_argument("question", nargs="*", help="One question; omit for an interactive conversation")
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("-q", "--question", dest="single_question", help="One question, enclosed in quotes")
    input_group.add_argument("--input-file", type=Path, help="Read one UTF-8 question from a file (supports a BOM)")
    parser.add_argument("--level", choices=["auto", "beginner", "intermediate", "advanced"], default="auto")
    parser.add_argument("--language", choices=["auto", "fa", "en"], default="auto")
    parser.add_argument("--trace", action="store_true", help="Print node progress and verification to stderr")
    parser.add_argument("--json", action="store_true", help="Output full state as JSON (single-question mode)")
    parser.add_argument("--state-file", type=Path, help="Save the last turn's state to a .json file")
    parser.add_argument("--html", type=Path, help="Export the answer to a read-only RTL/LTR .html report")
    parser.add_argument("--env-file", type=Path, help="Read settings from this .env file")
    parser.add_argument("--graph", action="store_true", help="Print the graph and exit; no API key required")
    return parser


def read_question_file(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            raise TutorError("The question file is too large (maximum 64 KiB).")
        text = data.decode("utf-8-sig").strip()
    except UnicodeDecodeError:
        raise TutorError("Save the question file as UTF-8 in your text editor.") from None
    except OSError:
        raise TutorError("Cannot read the question file. Check its path and permissions.") from None
    if not text or len(text) > MAX_QUESTION_LENGTH:
        raise TutorError(f"The file must contain 1 to {MAX_QUESTION_LENGTH} characters.")
    return text


def save_state(session: TutorSession, path: Path) -> None:
    if path.suffix.lower() != ".json":
        raise TutorError("State files must have a .json extension.")
    try:
        session.save_state(path)
    except OSError:
        raise TutorError("Cannot write the state file. Check the path and permissions.") from None


def show_result(state: dict, *, as_json: bool, trace: bool, ui: TerminalUI) -> None:
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        ui.answer(state)
    if trace:
        ui.trace(state, stderr=True)


def main(argv: list[str] | None = None) -> int:
    utf8_terminal()
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.single_question is not None or args.input_file) and args.question:
        parser.error("Use one question source: positional text, --question, or --input-file.")
    question = args.single_question if args.single_question is not None else " ".join(args.question)
    if args.graph:
        print(MathTutorAgent(GraphOnlyProvider()).mermaid())
        return 0
    if args.json and not (question or args.input_file):
        parser.error("--json requires a question.")
    if args.state_file and args.state_file.suffix.lower() != ".json":
        parser.error("--state-file must have a .json extension.")
    if args.html and args.html.suffix.lower() not in {".html", ".htm"}:
        parser.error("--html must have a .html or .htm extension.")

    provider = None
    trace_enabled = args.trace
    ui = TerminalUI()

    def progress(name: str) -> None:
        if trace_enabled:
            ui.message(f"[node] {name}", "assistant", stderr=True)
            ui.errors.flush()

    try:
        if args.input_file:
            question = read_question_file(args.input_file)
        if args.single_question is not None and not question.strip():
            raise TutorError("The question cannot be empty.")
        provider = LLMProvider(load_settings(args.env_file))
        agent = MathTutorAgent(provider, on_node=progress)
        session = TutorSession(agent, level=args.level, language=args.language)
        def respond(text: str, *, as_json: bool = False, echo_question: bool = False) -> dict:
            if echo_question and not as_json:
                ui.question(text)
            state = session.send(text)
            if args.state_file:
                save_state(session, args.state_file)
            show_result(state, as_json=as_json, trace=trace_enabled, ui=ui)
            if args.html:
                write_html_report(state, args.html)
                ui.message("Report: " + str(args.html.resolve()), "success", stderr=True)
            return state

        if question:
            state = respond(question, as_json=args.json, echo_question=True)
            return 2 if state["status"] == "unverified" else 0

        ui.welcome()
        while True:
            try:
                text = ui.read_question()
            except (EOFError, KeyboardInterrupt):
                ui.message("\nGoodbye.")
                return 0
            if not text:
                continue
            try:
                if text.startswith("/"):
                    command, _, value = text.partition(" ")
                    value = value.strip()
                    if command in {"/exit", "/quit"}:
                        ui.message("Goodbye.")
                        return 0
                    if command == "/help":
                        ui.message(HELP, "heading")
                    elif command == "/level":
                        if value not in {"auto", "beginner", "intermediate", "advanced"}:
                            raise TutorError("Use /level auto|beginner|intermediate|advanced.")
                        session.level = value
                        ui.message("Level: " + value, "success")
                    elif command == "/language":
                        if value not in {"auto", "fa", "en"}:
                            raise TutorError("Use /language auto|fa|en.")
                        session.language = value
                        ui.message("Language: " + value, "success")
                    elif command == "/new":
                        session.clear()
                        ui.message("A new conversation is ready.", "success")
                    elif command == "/state":
                        print(json.dumps(session.last_state, ensure_ascii=False, indent=2))
                    elif command == "/trace":
                        if value in {"on", "off"}:
                            trace_enabled = value == "on"
                            ui.message("Live trace: " + value, "success")
                        elif value:
                            raise TutorError("Use /trace, /trace on, or /trace off.")
                        elif session.last_state:
                            ui.trace(session.last_state)
                        else:
                            ui.message("No completed turn yet.", "heading")
                    elif command == "/save":
                        if not value:
                            raise TutorError("Use /save filename.json.")
                        path = Path(value.strip('"'))
                        save_state(session, path)
                        ui.message("Saved: " + str(path.resolve()), "success")
                    elif command == "/file":
                        if not value:
                            raise TutorError("Use /file question.txt.")
                        respond(read_question_file(Path(value.strip('"'))), echo_question=True)
                    elif command == "/html":
                        if not value:
                            raise TutorError("Use /html report.html.")
                        if session.last_state is None:
                            raise TutorError("Ask a question before exporting a report.")
                        path = Path(value.strip('"'))
                        write_html_report(session.last_state, path)
                        ui.message("Report: " + str(path.resolve()), "success")
                    elif command == "/graph":
                        print(agent.mermaid())
                    else:
                        raise TutorError("Unknown command. Type /help.")
                    continue
                respond(text)
            except TutorError as error:
                ui.message("Error: " + str(error), "error", stderr=True)
            except KeyboardInterrupt:
                ui.message("\nRequest cancelled. You can ask again.", "heading")
    except TutorError as error:
        ui.message("Error: " + str(error), "error", stderr=True)
        return 1
    except KeyboardInterrupt:
        ui.message("\nCancelled.", "heading", stderr=True)
        return 130
    finally:
        if provider:
            provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
