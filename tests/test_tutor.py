"""Behavior tests for branching, mathematical checks, state and terminal interaction."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent import ConceptAnswer, MathTutorAgent, TutorSession
from pydantic import ValidationError
from cli import main
from math_tools import ToolRequest, calculate, parse_expression, run_calculation
from provider import LLMProvider, Settings, TutorError
from report import write_html_report

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def workspace_temp():
    # Sandbox TEMP locations on Windows may disallow creating files inside them.
    # Verify the cleanup target before TemporaryDirectory recursively removes it.
    with tempfile.TemporaryDirectory(dir=PROJECT_ROOT, prefix=".test-") as folder:
        if not Path(folder).resolve().is_relative_to(PROJECT_ROOT):
            raise AssertionError("Temporary directory escaped the project.")
        yield folder


def analysis(kind="concept", **changes):
    return {
        "request_kind": kind, "topic": "derivative", "normalized_question": "Explain derivatives",
        "learner_level": "beginner", **changes,
    }


def answer(result="", **changes):
    return {
        "introduction": "A derivative measures how quickly a value changes.",
        "steps": ["Start with the slope between two points.", "Move the points closer together."],
        "example": "Speed is the rate of change of distance.",
        "result": result, "check_question": "What would zero speed mean?", **changes,
    }


PLAN = {"objective": "Understand rate of change", "analogy": "speed", "steps": ["Intuition", "Example"]}
PASS = {"accepted": True, "issues": []}


class ScriptedProvider:
    def __init__(self, outputs):
        self.outputs = {stage: list(values) for stage, values in outputs.items()}
        self.calls = []

    def structured(self, stage, instructions, context, schema):
        self.calls.append((stage, context))
        value = self.outputs[stage].pop(0)
        if isinstance(value, Exception):
            raise value
        return schema.model_validate(value)

    def close(self):
        pass


def concept_provider(**changes):
    outputs = {
        "analyze_request": [analysis()], "plan_lesson": [PLAN],
        "explain_concept": [answer()], "review_answer": [PASS],
    }
    outputs.update(changes)
    return ScriptedProvider(outputs)


class GraphTests(unittest.TestCase):
    def test_cli_numbering_preserves_decimal_values_and_removes_duplicate_prefixes(self):
        provider = concept_provider(explain_concept=[answer(steps=["1. Start with a slope.", "2.5 metres is a distance."])])
        state = MathTutorAgent(provider).run("Explain derivatives")
        self.assertIn("1. Start with a slope.", state["final_answer"])
        self.assertIn("2. 2.5 metres is a distance.", state["final_answer"])
        self.assertNotIn("1. 1. Start", state["final_answer"])

    def test_concept_schema_keeps_numeric_results_inside_the_example(self):
        with self.assertRaises(ValidationError):
            ConceptAnswer.model_validate(answer("Area is always 4"))

    def test_concept_route_and_explicit_level(self):
        provider = concept_provider()
        state = MathTutorAgent(provider).run("Explain derivatives", level="advanced")
        self.assertEqual(state["learner_level"], "advanced")
        self.assertEqual(state["status"], "answered")
        self.assertEqual([e["node"] for e in state["trace"]], [
            "analyze_request", "assess_level", "plan_lesson", "explain_concept", "review_answer", "finalize",
        ])
        self.assertEqual(state["tool_result"], {})
        self.assertIn("advanced", provider.calls[1][1]["learner_level"])
        self.assertTrue(all(e["updated_fields"] for e in state["trace"]))

    def test_problem_route_checks_math(self):
        request = ToolRequest(operation="differentiate", expression="x**3 + 2*x").model_dump()
        provider = ScriptedProvider({
            "analyze_request": [analysis("problem", normalized_question="Differentiate x**3 + 2*x", tool_request=request)],
            "plan_lesson": [PLAN], "solve_problem": [answer("3*x**2 + 2")], "review_answer": [PASS],
        })
        state = MathTutorAgent(provider).run("Differentiate x**3 + 2*x")
        self.assertEqual(state["tool_result"]["result"], "3*x**2 + 2")
        self.assertIs(state["tool_result"]["candidate_matches"], True)
        self.assertEqual(state["verification"], "symbolically_checked")
        self.assertIn("calculate", [e["node"] for e in state["trace"]])

    def test_wrong_result_forces_revision_even_if_reviewer_accepts(self):
        request = ToolRequest(operation="differentiate", expression="x**2").model_dump()
        provider = ScriptedProvider({
            "analyze_request": [analysis("problem", normalized_question="Differentiate x**2", tool_request=request)],
            "plan_lesson": [PLAN], "solve_problem": [answer("3*x")],
            "review_answer": [PASS, PASS], "revise_answer": [answer("2*x")],
        })
        state = MathTutorAgent(provider).run("Differentiate x**2")
        self.assertEqual(state["revision_count"], 1)
        self.assertEqual(state["draft"]["result"], "2*x")
        self.assertIs(state["tool_result"]["candidate_matches"], True)
        self.assertEqual([e["node"] for e in state["trace"]].count("calculate"), 2)

    def test_repeated_rejection_stops_and_does_not_publish_bad_draft(self):
        reject = {"accepted": False, "issues": ["Incorrect definition"]}
        provider = concept_provider(review_answer=[reject, reject], revise_answer=[answer()])
        state = MathTutorAgent(provider).run("Explain derivatives")
        self.assertEqual(state["status"], "unverified")
        self.assertEqual(state["revision_count"], 1)
        self.assertNotIn("A derivative measures", state["final_answer"])

    def test_clarification_returns_without_teaching_or_solving(self):
        provider = ScriptedProvider({"analyze_request": [analysis(
            "problem", needs_clarification=True, clarification_question="Which equation?",
            normalized_question="Solve the equation",
        )]})
        state = MathTutorAgent(provider).run("Solve the equation")
        self.assertEqual(state["status"], "needs_clarification")
        self.assertEqual(state["final_answer"], "Which equation?")
        self.assertEqual(len(provider.calls), 1)

    def test_out_of_scope_finishes_without_solver(self):
        provider = ScriptedProvider({"analyze_request": [analysis("out_of_scope")]})
        state = MathTutorAgent(provider).run("Recommend a movie")
        self.assertEqual(state["status"], "out_of_scope")
        self.assertEqual([e["node"] for e in state["trace"]], ["analyze_request", "finalize"])

    def test_review_can_request_missing_information(self):
        review = {"accepted": False, "needs_clarification": True,
                  "clarification_question": "What is the domain?"}
        provider = concept_provider(review_answer=[review])
        state = MathTutorAgent(provider).run("Explain this function")
        self.assertEqual(state["status"], "needs_clarification")
        self.assertIn("ask_clarification", [e["node"] for e in state["trace"]])

    def test_empty_or_oversized_question_never_calls_model(self):
        provider = ScriptedProvider({})
        tutor = MathTutorAgent(provider)
        for question in ("   ", "x" * 4001):
            with self.assertRaises(TutorError):
                tutor.run(question)
        self.assertFalse(provider.calls)

    def test_fa_detection(self):
        state = MathTutorAgent(concept_provider()).run("مشتق چیست؟")
        self.assertEqual(state["language"], "fa")

    def test_actual_graph_has_loop_and_conditional_edges(self):
        mermaid = MathTutorAgent(ScriptedProvider({})).mermaid()
        self.assertIn("revise_answer", mermaid)
        self.assertIn("ask_clarification", mermaid)
        self.assertIn("calculate", mermaid)


class SessionTests(unittest.TestCase):
    def test_clarification_is_used_in_next_turn(self):
        provider = concept_provider(analyze_request=[analysis(
            needs_clarification=True, clarification_question="Which function?",
            normalized_question="Explain the derivative of my function",
        ), analysis()])
        session = TutorSession(MathTutorAgent(provider))
        session.send("Explain the derivative of my function")
        session.send("x**2")
        self.assertIn("Explain the derivative of my function", provider.calls[1][1]["question"])
        self.assertIn("x**2", provider.calls[1][1]["question"])
        self.assertEqual(session.pending_request, "")

    def test_followup_retains_history_but_resets_transient_state(self):
        provider = concept_provider(
            analyze_request=[analysis(), analysis()], plan_lesson=[PLAN, PLAN],
            explain_concept=[answer(), answer()], review_answer=[PASS, PASS],
        )
        session = TutorSession(MathTutorAgent(provider))
        first = session.send("Explain derivatives")
        second = session.send("Give another example")
        self.assertEqual(len(second["trace"]), len(first["trace"]))
        self.assertEqual(len(second["history"]), 2)
        self.assertEqual(second["revision_count"], 0)
        self.assertEqual(len(session.history), 4)
        self.assertEqual(second["previous_level"], "beginner")

    def test_failed_turn_does_not_mutate_session(self):
        provider = concept_provider(analyze_request=[analysis(), TutorError("Network down")])
        session = TutorSession(MathTutorAgent(provider))
        session.send("Explain derivatives")
        before = list(session.history)
        last = session.last_state
        with self.assertRaises(TutorError):
            session.send("Another question")
        self.assertEqual(session.history, before)
        self.assertIs(session.last_state, last)

    def test_new_clears_state_and_keeps_preferences(self):
        session = TutorSession(MathTutorAgent(concept_provider()), level="advanced", language="fa")
        session.send("مشتق چیست؟")
        session.clear()
        self.assertEqual(session.history, [])
        self.assertEqual(session.pending_request, "")
        self.assertIsNone(session.last_state)
        self.assertEqual(session.level, "advanced")
        self.assertEqual(session.language, "fa")

    def test_state_export_is_valid_json_without_credentials(self):
        session = TutorSession(MathTutorAgent(concept_provider()))
        session.send("Explain derivatives")
        with workspace_temp() as folder:
            path = Path(folder) / "state.json"
            session.save_state(path)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "answered")
        self.assertNotIn("api_key", json.dumps(saved))


class MathToolTests(unittest.TestCase):
    def test_supported_operations(self):
        cases = [
            (ToolRequest(operation="differentiate", expression="x^3 + 2*x"), "3*x**2 + 2"),
            (ToolRequest(operation="integrate", expression="x", lower_bound="0", upper_bound="2"), "2"),
            (ToolRequest(operation="integrate", expression="2*x"), "x**2"),
            (ToolRequest(operation="solve", expression="2*x + 3 = 7"), "{2}"),
            (ToolRequest(operation="solve", expression="x**2 = 4"), "{-2, 2}"),
            (ToolRequest(operation="determinant", expression="[[1,2],[3,4]]"), "-2"),
            (ToolRequest(operation="simplify", expression="(x+1)**2-x**2"), "2*x + 1"),
        ]
        for request, expected in cases:
            with self.subTest(operation=request.operation, expression=request.expression):
                self.assertEqual(calculate(request)["result"], expected)

    def test_indefinite_integral_allows_additive_constant(self):
        request = ToolRequest(operation="integrate", expression="2*x")
        self.assertIs(calculate(request, "x**2 + C")["candidate_matches"], True)
        self.assertIs(calculate(request, "x**2 + 4")["candidate_matches"], True)
        self.assertIs(calculate(request, "x**3 + C")["candidate_matches"], False)

    def test_solution_sets_are_complete(self):
        request = ToolRequest(operation="solve", expression="x**2=4")
        self.assertIs(calculate(request, "2")["candidate_matches"], False)
        self.assertIs(calculate(request, "2, -2")["candidate_matches"], True)

    def test_persian_digits(self):
        self.assertEqual(str(parse_expression("۲*x + ۳")), "2*x + 3")

    def test_expressions_cannot_execute_python_or_allocate_unbounded_powers(self):
        for expression in ["__import__('os').system('echo x')", "x.__class__", "[x for x in [1]]",
                           "open('test.txt')", "2**1000000", "1/0", "sqrt(-1)/0", "True", "x**x"]:
            with self.subTest(expression=expression), self.assertRaises((ValueError, SyntaxError)):
                parse_expression(expression)

    def test_matrix_limits_and_invalid_integral_bounds(self):
        for request in [ToolRequest(operation="determinant", expression="[[1,2,3],[3,4]]"),
                        ToolRequest(operation="integrate", expression="x", lower_bound="0")]:
            with self.assertRaises(ValueError):
                calculate(request)

    def test_worker_timeout_and_unsupported_input_are_explicit(self):
        request = ToolRequest(operation="differentiate", expression="x**2")
        import subprocess
        with patch("math_tools.subprocess.run", side_effect=subprocess.TimeoutExpired("worker", 1)):
            self.assertEqual(run_calculation(request)["status"], "unavailable")
        self.assertEqual(run_calculation(None)["status"], "unavailable")


class ProviderTests(unittest.TestCase):
    def test_malformed_json_is_repaired_once(self):
        from agent import AnswerReview
        provider = LLMProvider(Settings("test-key", "test-model"))
        responses = [SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=value))])
                     for value in ["not JSON", json.dumps(PASS)]]
        try:
            with patch.object(provider.client.chat.completions, "create", side_effect=responses) as create:
                result = provider.structured("review", "Review", {}, AnswerReview)
            self.assertTrue(result.accepted)
            self.assertEqual(create.call_count, 2)
        finally:
            provider.close()

    def test_invalid_schema_stops_after_second_response(self):
        from agent import AnswerReview
        provider = LLMProvider(Settings("test-key", "test-model"))
        bad = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"accepted":"maybe"}'))])
        try:
            with patch.object(provider.client.chat.completions, "create", return_value=bad) as create:
                with self.assertRaises(TutorError):
                    provider.structured("review", "Review", {}, AnswerReview)
            self.assertEqual(create.call_count, 2)
        finally:
            provider.close()


class CLITests(unittest.TestCase):
    def test_graph_mode_does_not_load_credentials(self):
        with patch("cli.load_settings", side_effect=AssertionError), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["--graph"]), 0)
        self.assertIn("analyze_request", out.getvalue())

    def test_single_question_json_and_state_file(self):
        with workspace_temp() as folder:
            path = Path(folder) / "state.json"
            provider = concept_provider()
            with patch("cli.load_settings"), patch("cli.LLMProvider", return_value=provider), \
                    redirect_stdout(io.StringIO()) as out:
                code = main(["-q", "Explain derivatives", "--json", "--state-file", str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["status"], "answered")
            self.assertTrue(path.exists())

    def test_utf8_bom_file_preserves_persian_and_exports_html_with_json(self):
        question = "مشتق چیست؟"
        provider = concept_provider()
        with workspace_temp() as folder:
            root = Path(folder)
            question_file = root / "question.txt"
            report_file = root / "report.html"
            question_file.write_text(question, encoding="utf-8-sig")
            with patch("cli.load_settings"), patch("cli.LLMProvider", return_value=provider), \
                    redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                code = main(["--input-file", str(question_file), "--json", "--html", str(report_file)])
            self.assertEqual(code, 0)
            state = json.loads(out.getvalue())
            self.assertEqual(state["raw_request"], question)
            self.assertEqual(state["language"], "fa")
            self.assertEqual(provider.calls[0][1]["question"], question)
            self.assertIn('lang="fa" dir="rtl"', report_file.read_text(encoding="utf-8"))
            self.assertIn("Report:", err.getvalue())

    def test_bad_question_files_fail_before_loading_api_settings(self):
        with workspace_temp() as folder:
            root = Path(folder)
            cases = {"invalid.txt": b"\xff", "empty.txt": b" \n", "long.txt": b"x" * 4001,
                     "large.txt": b"x" * 65537}
            for name, data in cases.items():
                (root / name).write_bytes(data)
            for name in [*cases, "missing.txt"]:
                with self.subTest(file=name), patch("cli.load_settings") as settings, \
                        redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(main(["--input-file", str(root / name)]), 1)
                    settings.assert_not_called()
                    self.assertIn("Error:", err.getvalue())
                    self.assertNotIn("Traceback", err.getvalue())

    def test_interactive_file_clarification_preserves_context_and_exports_last_turn(self):
        provider = ScriptedProvider({
            "analyze_request": [analysis("problem", needs_clarification=True,
                clarification_question="Which equation?", normalized_question="Solve the equation"),
                analysis("problem", normalized_question="Solve 2*x + 3 = 7",
                         tool_request=ToolRequest(operation="solve", expression="2*x + 3 = 7").model_dump())],
            "plan_lesson": [PLAN], "solve_problem": [answer("2")], "review_answer": [PASS],
        })
        with workspace_temp() as folder:
            root = Path(folder)
            question = root / "question.txt"
            equation = root / "equation.txt"
            report = root / "last.html"
            question.write_text("این معادله را حل کن.", encoding="utf-8")
            equation.write_text("2*x + 3 = 7", encoding="utf-8")
            inputs = iter([f'/file "{question}"', f'/file "{equation}"', f'/html "{report}"', "/exit"])
            with patch("cli.load_settings"), patch("cli.LLMProvider", return_value=provider), \
                    patch("builtins.input", side_effect=lambda _: next(inputs)), \
                    redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["--language", "fa"]), 0)
            self.assertIn("Which equation?", out.getvalue())
            self.assertEqual(provider.calls[0][1]["question"], "این معادله را حل کن.")
            self.assertIn("Solve the equation", provider.calls[1][1]["question"])
            self.assertIn("2*x + 3 = 7", provider.calls[1][1]["question"])
            self.assertIn("symbolically_checked", report.read_text(encoding="utf-8"))

    def test_interactive_commands_and_followup(self):
        provider = concept_provider()
        inputs = iter(["", "/level advanced", "/language en", "Explain derivatives", "/trace", "/new", "/exit"])
        with patch("cli.load_settings"), patch("cli.LLMProvider", return_value=provider), \
                patch("builtins.input", side_effect=lambda _: next(inputs)), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main([]), 0)
        self.assertIn("Level: advanced", out.getvalue())
        self.assertIn("explain_concept", out.getvalue())
        self.assertIn("Goodbye", out.getvalue())
        self.assertEqual(provider.calls[1][1]["learner_level"], "advanced")

    def test_configuration_error_has_nonzero_exit_and_no_traceback(self):
        with patch("cli.load_settings", side_effect=TutorError("Missing API settings")), \
                redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["-q", "Test"]), 1)
        self.assertIn("Missing API settings", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


class ReportTests(unittest.TestCase):
    def test_persian_report_escapes_user_and_model_markup_and_isolates_formulas(self):
        state = MathTutorAgent(concept_provider()).run("مشتق چیست؟", language="fa")
        state["raw_request"] = '<script>alert("question")</script>'
        state["final_answer"] = '<img src=x onerror="alert(1)">\nاین x یک متغیر است.\nپاسخ: 3*x^2 + 2'
        with workspace_temp() as folder:
            path = Path(folder) / "answer.html"
            write_html_report(state, path)
            markup = path.read_text(encoding="utf-8")
        self.assertIn('lang="fa" dir="rtl"', markup)
        self.assertIn('<bdi dir="ltr">3*x^2 + 2</bdi>', markup)
        self.assertIn('<bdi dir="ltr">x</bdi> یک', markup)
        self.assertNotIn("<script", markup)
        self.assertNotIn("<img", markup)
        self.assertIn("&lt;script&gt;", markup)
        self.assertIn("explain_concept", markup)

    def test_english_report_uses_ltr_and_reports_output_errors_without_a_traceback(self):
        state = MathTutorAgent(concept_provider()).run("Explain derivatives", language="en")
        with workspace_temp() as folder:
            path = Path(folder) / "answer.html"
            write_html_report(state, path)
            self.assertIn('lang="en" dir="ltr"', path.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(TutorError, "output folder"):
                write_html_report(state, Path(folder) / "missing-folder" / "answer.html")

if __name__ == "__main__":
    unittest.main()
