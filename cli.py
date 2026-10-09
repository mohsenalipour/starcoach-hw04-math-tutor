"""Interactive terminal and single-question entry points for the math tutor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent import MathTutorAgent, TutorSession
from provider import LLMProvider, TutorError, load_settings

HELP = """Commands:
  /help                         Show this help
  /level auto|beginner|intermediate|advanced
  /language auto|fa|en           Choose the answer language
  /trace [on|off]                Show the last route, or toggle live node progress
  /state                        Show the last turn's complete JSON state
  /save filename.json           Save the last state (contains no API key)
  /graph                        Show the actual graph in Mermaid notation
  /new                          Start a fresh conversation; keep level/language
  /exit                         Quit (Ctrl+D/EOF also quits)

Examples:
  مشتق را با یک مثال ساده توضیح بده
  مشتق x^3 + 2*x را نسبت به x حساب کن
  Solve 2*x + 3 = 7 step by step
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
    parser.add_argument("-q", "--question", dest="single_question", help="One question, enclosed in quotes")
    parser.add_argument("--level", choices=["auto", "beginner", "intermediate", "advanced"], default="auto")
    parser.add_argument("--language", choices=["auto", "fa", "en"], default="auto")
    parser.add_argument("--trace", action="store_true", help="Print node progress and verification to stderr")
    parser.add_argument("--json", action="store_true", help="Output full state as JSON (single-question mode)")
    parser.add_argument("--state-file", type=Path, help="Save the last turn's state to a .json file")
    parser.add_argument("--env-file", type=Path, help="Read settings from this .env file")
    parser.add_argument("--graph", action="store_true", help="Print the graph and exit; no API key required")
    return parser


def save_state(session: TutorSession, path: Path) -> None:
    if path.suffix.lower() != ".json":
        raise TutorError("State files must have a .json extension.")
    try:
        session.save_state(path)
    except OSError:
        raise TutorError("Cannot write the state file. Check the path and permissions.") from None


def show_result(state: dict, *, as_json: bool, trace: bool) -> None:
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print("\n" + state["final_answer"] + "\n")
    if trace:
        print("Route: " + " -> ".join(event["node"] for event in state["trace"]), file=sys.stderr)
        print(f"Status: {state['status']} | Verification: {state['verification']}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    utf8_terminal()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.single_question is not None and args.question:
        parser.error("Use either a positional question or --question.")
    question = args.single_question if args.single_question is not None else " ".join(args.question)
    if args.graph:
        print(MathTutorAgent(GraphOnlyProvider()).mermaid())
        return 0
    if args.json and not question:
        parser.error("--json requires a question.")
    if args.state_file and args.state_file.suffix.lower() != ".json":
        parser.error("--state-file must have a .json extension.")

    provider = None
    trace_enabled = args.trace

    def progress(name: str) -> None:
        if trace_enabled:
            print(f"[node] {name}", file=sys.stderr, flush=True)

    try:
        provider = LLMProvider(load_settings(args.env_file))
        agent = MathTutorAgent(provider, on_node=progress)
        session = TutorSession(agent, level=args.level, language=args.language)
        if question:
            state = session.send(question)
            if args.state_file:
                save_state(session, args.state_file)
            show_result(state, as_json=args.json, trace=trace_enabled)
            return 2 if state["status"] == "unverified" else 0

        print("MILO — Mathematics Tutor\nفارسی / English | /help: راهنما | /exit: خروج\n")
        while True:
            try:
                text = input("You> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                return 0
            if not text:
                continue
            try:
                if text.startswith("/"):
                    command, _, value = text.partition(" ")
                    value = value.strip()
                    if command in {"/exit", "/quit"}:
                        print("Goodbye.")
                        return 0
                    if command == "/help":
                        print(HELP)
                    elif command == "/level":
                        if value not in {"auto", "beginner", "intermediate", "advanced"}:
                            raise TutorError("Use /level auto|beginner|intermediate|advanced.")
                        session.level = value
                        print("Level: " + value)
                    elif command == "/language":
                        if value not in {"auto", "fa", "en"}:
                            raise TutorError("Use /language auto|fa|en.")
                        session.language = value
                        print("Language: " + value)
                    elif command == "/new":
                        session.clear()
                        print("A new conversation is ready.")
                    elif command == "/state":
                        print(json.dumps(session.last_state, ensure_ascii=False, indent=2))
                    elif command == "/trace":
                        if value in {"on", "off"}:
                            trace_enabled = value == "on"
                            print("Live trace: " + value)
                        elif value:
                            raise TutorError("Use /trace, /trace on, or /trace off.")
                        elif session.last_state:
                            print(" -> ".join(event["node"] for event in session.last_state["trace"]))
                            print("Verification: " + session.last_state["verification"])
                        else:
                            print("No completed turn yet.")
                    elif command == "/save":
                        if not value:
                            raise TutorError("Use /save filename.json.")
                        path = Path(value.strip('"'))
                        save_state(session, path)
                        print("Saved: " + str(path.resolve()))
                    elif command == "/graph":
                        print(agent.mermaid())
                    else:
                        raise TutorError("Unknown command. Type /help.")
                    continue
                state = session.send(text)
                if args.state_file:
                    save_state(session, args.state_file)
                show_result(state, as_json=False, trace=trace_enabled)
            except TutorError as error:
                print("Error: " + str(error), file=sys.stderr)
            except KeyboardInterrupt:
                print("\nRequest cancelled. You can ask again.")
    except TutorError as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    finally:
        if provider:
            provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
