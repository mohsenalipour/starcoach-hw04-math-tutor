"""PROJECT_MILO: MATH_TUTOR_IMPLEMENTATION.

All state schemas, nodes, conditional edges and session orchestration live here.
Run cli.py for interactive use. No graph node asks the user through stdin.
"""

from __future__ import annotations

import json
import operator
import re
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, model_validator

from math_tools import ToolRequest, run_calculation
from provider import Provider, TutorError

Level = Literal["beginner", "intermediate", "advanced"]
LevelChoice = Literal["auto", "beginner", "intermediate", "advanced"]
LanguageChoice = Literal["auto", "fa", "en"]
MAX_QUESTION_LENGTH = 4000
MAX_HISTORY_MESSAGES = 12


class StructuredModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestAnalysis(StructuredModel):
    request_kind: Literal["concept", "problem", "out_of_scope"]
    topic: str = Field(min_length=1, max_length=200)
    normalized_question: str = Field(min_length=1, max_length=8000)
    learner_level: Level = "beginner"
    needs_clarification: bool = False
    clarification_question: str = Field(default="", max_length=1000)
    tool_request: ToolRequest | None = None

    @model_validator(mode="after")
    def require_clarification_text(self) -> RequestAnalysis:
        if self.needs_clarification and not self.clarification_question.strip():
            raise ValueError("A clarification question is required.")
        return self


class TeachingPlan(StructuredModel):
    objective: str = Field(min_length=1, max_length=500)
    prerequisites: list[str] = Field(default_factory=list, max_length=5)
    analogy: str = Field(default="", max_length=1000)
    steps: list[str] = Field(min_length=1, max_length=8)


class TeachingAnswer(StructuredModel):
    introduction: str = Field(min_length=1, max_length=3000)
    steps: list[str] = Field(min_length=1, max_length=10)
    example: str = Field(default="", max_length=3000)
    result: str = Field(default="", max_length=400)
    check_question: str = Field(min_length=1, max_length=1000)


class ConceptAnswer(TeachingAnswer):
    # A numeric example belongs in example/steps, not in a general final result.
    result: Literal[""] = ""


class AnswerReview(StructuredModel):
    accepted: bool
    issues: list[str] = Field(default_factory=list, max_length=6)
    needs_clarification: bool = False
    clarification_question: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_review(self) -> AnswerReview:
        if self.needs_clarification and not self.clarification_question.strip():
            raise ValueError("A clarification question is required.")
        if not self.accepted and not self.needs_clarification and not self.issues:
            raise ValueError("A rejected answer needs a concrete issue to repair.")
        return self


class TraceEvent(TypedDict):
    node: str
    updated_fields: list[str]
    elapsed_ms: int


class TutorState(TypedDict):
    raw_request: str
    question: str
    history: list[dict[str, str]]
    level_choice: LevelChoice
    previous_level: str
    language: Literal["fa", "en"]
    analysis: dict
    learner_level: Level
    plan: dict
    draft: dict
    tool_result: dict
    review: dict
    revision_count: int
    final_answer: str
    status: Literal["running", "answered", "needs_clarification", "out_of_scope", "unverified"]
    verification: str
    trace: Annotated[list[TraceEvent], operator.add]


LEVEL_GUIDANCE = {
    "beginner": "Assume no calculus background. Start with intuition and a familiar analogy. Define every symbol. Use one small numeric example before formulas.",
    "intermediate": "Assume school algebra. Explain the method, show the important steps, and connect the intuition to the formula.",
    "advanced": "Use precise definitions, assumptions and domain restrictions. Explain why each inference is valid, with a proof or derivation when useful.",
}


class MathTutorAgent:
    def __init__(
        self, provider: Provider, *, max_revisions: int = 1,
        on_node: Callable[[str], None] | None = None,
    ) -> None:
        if type(max_revisions) is not int or not 0 <= max_revisions <= 2:
            raise ValueError("max_revisions must be 0, 1 or 2.")
        self.provider = provider
        self.max_revisions = max_revisions
        self.on_node = on_node
        self.graph = self._build_graph()

    def _node(self, name: str, function: Callable) -> Callable:
        def execute(state: TutorState) -> dict:
            if self.on_node:
                self.on_node(name)
            started = time.perf_counter()
            updates = function(state)
            return {
                **updates,
                "trace": [{
                    "node": name, "updated_fields": sorted(updates),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }],
            }
        return execute

    def _build_graph(self):
        graph = StateGraph(TutorState)
        nodes = {
            "analyze_request": self.analyze_request,
            "ask_clarification": self.ask_clarification,
            "assess_level": self.assess_level,
            "plan_lesson": self.plan_lesson,
            "explain_concept": self.explain_concept,
            "solve_problem": self.solve_problem,
            "calculate": self.calculate,
            "review_answer": self.review_answer,
            "revise_answer": self.revise_answer,
            "finalize": self.finalize,
        }
        for name, function in nodes.items():
            graph.add_node(name, self._node(name, function))
        graph.add_edge(START, "analyze_request")
        graph.add_conditional_edges("analyze_request", self.route_analysis, {
            "clarify": "ask_clarification", "teach": "assess_level", "finish": "finalize",
        })
        graph.add_edge("ask_clarification", "finalize")
        graph.add_edge("assess_level", "plan_lesson")
        graph.add_conditional_edges("plan_lesson", self.route_lesson, {
            "concept": "explain_concept", "problem": "solve_problem",
        })
        graph.add_edge("explain_concept", "review_answer")
        graph.add_edge("solve_problem", "calculate")
        graph.add_edge("calculate", "review_answer")
        graph.add_conditional_edges("review_answer", self.route_review, {
            "finish": "finalize", "clarify": "ask_clarification", "revise": "revise_answer",
        })
        graph.add_conditional_edges("revise_answer", self.route_after_revision, {
            "concept": "review_answer", "problem": "calculate",
        })
        graph.add_edge("finalize", END)
        return graph.compile()

    def analyze_request(self, state: TutorState) -> dict:
        analysis = self.provider.structured(
            "analyze_request",
            "Classify the user's current request as concept, problem or out_of_scope. "
            "Math applications (probability, optimization, data science) count as mathematics. "
            "Resolve short follow-ups using history. Do not change the original numbers. "
            "Distinguish 'what is a derivative?' from 'differentiate x^2'. A mixed request "
            "with a concrete calculation is a problem; still explain the intuition. "
            "Infer learner_level only from explicit statements or context; default beginner. "
            "Clarify only when missing data makes a meaningful answer impossible. "
            "For 'solve it' without a referenced problem, ask for the problem. "
            "For a concept question, unknown learner level alone does not require clarification. "
            "For supported problems set tool_request with explicit * and ** notation, "
            "exact expression, variable and optional constant integral bounds. "
            "Operations: differentiate, integrate, solve (one polynomial equation over reals), "
            "simplify, determinant (numeric square matrix). Otherwise tool_request is null. "
            "Do not include computation instructions in an expression. "
            "Write normalized_question and clarification_question in the selected language.",
            {"question": state["question"], "history": state["history"],
             "language": state["language"], "previous_level": state["previous_level"]},
            RequestAnalysis,
        )
        return {"analysis": analysis.model_dump(), "question": analysis.normalized_question}

    @staticmethod
    def route_analysis(state: TutorState) -> str:
        if state["analysis"]["request_kind"] == "out_of_scope":
            return "finish"
        return "clarify" if state["analysis"]["needs_clarification"] else "teach"

    def ask_clarification(self, state: TutorState) -> dict:
        review = state["review"]
        question = (review.get("clarification_question") if review.get("needs_clarification")
                    else state["analysis"]["clarification_question"])
        return {"final_answer": question, "status": "needs_clarification", "verification": "not_applicable"}

    @staticmethod
    def assess_level(state: TutorState) -> dict:
        choice = state["level_choice"]
        return {"learner_level": state["analysis"]["learner_level"] if choice == "auto" else choice}

    def _context(self, state: TutorState) -> dict:
        return {
            "question": state["question"], "raw_request": state["raw_request"],
            "history": state["history"], "analysis": state["analysis"],
            "learner_level": state["learner_level"],
            "level_guidance": LEVEL_GUIDANCE[state["learner_level"]],
            "language": state["language"], "plan": state["plan"],
        }

    def plan_lesson(self, state: TutorState) -> dict:
        plan = self.provider.structured(
            "plan_lesson",
            "Build a short teaching plan for this learner. Specify prerequisites, "
            "an accurate analogy when helpful, and ordered instructional steps. "
            "For problems, include the solution strategy and how to check it. "
            "For concepts, move from intuition to one concrete example to the formal rule. "
            "Use plausible everyday quantities and correct units in every calculation. "
            "For integration, prefer accumulation of distance from speed times small "
            "time intervals. Separate a definite integral (a value) from an indefinite "
            "integral (antiderivatives). A definite integral has bounds and no + C. "
            "Use the selected language: fa = Persian, en = English.",
            self._context(state), TeachingPlan,
        )
        return {"plan": plan.model_dump()}

    @staticmethod
    def route_lesson(state: TutorState) -> str:
        return state["analysis"]["request_kind"]

    def explain_concept(self, state: TutorState) -> dict:
        answer = self.provider.structured(
            "explain_concept",
            "Teach the concept using the plan. Start with the intuition, explain all "
            "symbols before using them, and include one worked everyday or numeric example. "
            "Give a short check_question to test understanding. Advanced learners may need "
            "a precise definition and derivation. Explain limitations of analogies if needed. "
            "Avoid dense unexplained formulas. Use the selected language. "
            "Write steps as plain sentences, without numbering; the CLI numbers them. "
            "For integration, teach accumulation with quantities and units, such as "
            "speed multiplied by small time intervals giving distance. Never confuse a "
            "speed's units with distance: (60 metres/minute) * (1 minute) = 60 metres. "
            "Use plausible speeds for walking or cycling examples. Never confuse a "
            "curved walking path with area under a function. A definite integral is signed "
            "accumulation; ordinary geometric area coincides with it for nonnegative functions. "
            "Distinguish a definite integral's value from an indefinite integral's family "
            "of antiderivatives. Use a concrete numeric example with fixed bounds. "
            "result MUST be the empty string for concept teaching. Put numeric results "
            "and formulas only in the worked example or explanatory steps.",
            self._context(state), ConceptAnswer,
        )
        return {"draft": answer.model_dump()}

    def solve_problem(self, state: TutorState) -> dict:
        answer = self.provider.structured(
            "solve_problem",
            "Solve exactly the requested problem using the plan. Explain WHY each "
            "visible solution step is valid, and state assumptions and domain restrictions. "
            "Do not invent missing numbers. Use the selected language. "
            "result should be only the final mathematical value/expression, in explicit "
            "ASCII notation: 2*x, x**2, or -2, 2 for a solution set. For an indefinite "
            "integral include + C. Put prose and units in the steps, not in result. "
            "End with a short check_question appropriate to the learner.",
            self._context(state), TeachingAnswer,
        )
        return {"draft": answer.model_dump()}

    @staticmethod
    def calculate(state: TutorState) -> dict:
        data = state["analysis"].get("tool_request")
        request = ToolRequest.model_validate(data) if data else None
        return {"tool_result": run_calculation(request, state["draft"].get("result", ""))}

    def review_answer(self, state: TutorState) -> dict:
        context = {**self._context(state), "draft": state["draft"], "tool_result": state["tool_result"]}
        review = self.provider.structured(
            "review_answer",
            "Independently review the draft for mathematical correctness, relevance, "
            "clear explanation, learner-level fit and selected language. Compare the "
            "normalized question AND any tool expression/bounds with the original request "
            "and history. A correct calculation of a different problem must be rejected. "
            "When tool_result exists, check every step against it; the model's result may "
            "be algebraically equivalent. Reject a missing + C for an indefinite integral. "
            "IMPORTANT: a DEFINITE integral with lower/upper bounds returns a number "
            "and MUST NOT have + C. Never reject a correct definite integral for omitting "
            "+ C. A beginner concept answer need not cover both integral types unless "
            "it introduces both. Judge only the scope the user asked for. "
            "Check analogies carefully: accumulated distance is speed integrated over time, "
            "not the geometric area under a curved walking route. A definite integral "
            "is signed accumulation; claiming it is always nonnegative area is inaccurate. "
            "Check units and that example bounds/numbers are fixed consistently. "
            "Do not claim independent symbolic verification for unsupported operations. "
            "accepted is true only if all criteria pass. Provide specific repairable issues "
            "when false, quoting the specific draft statement and explaining the error. "
            "Do not invent missing requirements or reject mathematically correct simplified "
            "examples for omitting unrelated advanced topics. "
            "If essential user information is missing, set needs_clarification "
            "and write one focused question. Use the selected language for issues/questions.",
            context, AnswerReview,
        ).model_dump()
        if state["tool_result"].get("candidate_matches") is False:
            review["accepted"] = False
            review["issues"] = (review["issues"] + [
                "The final result disagrees with the independently computed symbolic result."
            ])[:6]
        return {"review": review}

    def route_review(self, state: TutorState) -> str:
        review = state["review"]
        if review.get("needs_clarification"):
            return "clarify"
        if review.get("accepted") or state["revision_count"] >= self.max_revisions:
            return "finish"
        return "revise"

    def revise_answer(self, state: TutorState) -> dict:
        answer = self.provider.structured(
            "revise_answer",
            "Rewrite the teaching answer and fix every review issue. Preserve the user's "
            "original problem and level. Use the independent symbolic result when available. "
            "Correct both the explanation steps and final result, then add a check_question. "
            "Use explicit ASCII arithmetic in result. For concept teaching result MUST "
            "be empty; keep computed example values inside example/steps. "
            "A definite integral has bounds and no + C; an indefinite antiderivative "
            "requires + C. Use the selected language for prose.",
            {**self._context(state), "draft": state["draft"],
             "tool_result": state["tool_result"], "issues": state["review"]["issues"]},
            ConceptAnswer if state["analysis"]["request_kind"] == "concept" else TeachingAnswer,
        )
        return {"draft": answer.model_dump(), "revision_count": state["revision_count"] + 1}

    @staticmethod
    def route_after_revision(state: TutorState) -> str:
        return state["analysis"]["request_kind"]

    @staticmethod
    def finalize(state: TutorState) -> dict:
        fa = state["language"] == "fa"
        if state["status"] == "needs_clarification":
            return {"final_answer": state["final_answer"]}
        if state["analysis"]["request_kind"] == "out_of_scope":
            return {
                "status": "out_of_scope", "verification": "not_applicable",
                "final_answer": "من مدرس ریاضی هستم. یک سؤال، مفهوم یا مسئلهٔ ریاضی بپرس." if fa
                else "I am a mathematics tutor. Ask me about a math concept or problem.",
            }
        if not state["review"].get("accepted"):
            return {
                "status": "unverified", "verification": "failed_review",
                "final_answer": "هنوز نتوانستم پاسخ قابل‌اعتمادی تأیید کنم. مسئله را با جزئیات بیشتر یا در بخش‌های کوچک‌تر بپرس." if fa
                else "I could not confirm a reliable answer. Add details or ask about a smaller part of the problem.",
            }
        draft = state["draft"]
        clean_steps = [re.sub(r"^(?:\s*\d+[.)]\s+)+", "", step).strip() for step in draft["steps"]]
        parts = [draft["introduction"], "\n".join(f"{i}. {s}" for i, s in enumerate(clean_steps, 1))]
        if draft["example"]:
            parts.append(("مثال: " if fa else "Example: ") + draft["example"])
        if draft["result"]:
            parts.append(("پاسخ: " if fa else "Result: ") + draft["result"])
        parts.append(("برای بررسی یادگیری: " if fa else "Check your understanding: ") + draft["check_question"])
        tool = state["tool_result"]
        verification = "model_reviewed"
        if tool.get("status") == "computed":
            verification = "symbolically_checked" if tool.get("candidate_matches") is True else "reviewed_with_tool"
        return {"final_answer": "\n\n".join(parts), "status": "answered", "verification": verification}

    def run(
        self, question: str, *, history: list[dict[str, str]] | None = None,
        level: LevelChoice = "auto", language: LanguageChoice = "auto",
        pending_request: str = "", previous_level: str = "",
    ) -> TutorState:
        clean = question.strip()
        if not clean or len(clean) > MAX_QUESTION_LENGTH:
            raise TutorError(f"Enter a question between 1 and {MAX_QUESTION_LENGTH} characters.")
        if level not in {"auto", *LEVEL_GUIDANCE} or language not in {"auto", "fa", "en"}:
            raise TutorError("Invalid learner level or language.")
        selected_language = language
        if language == "auto":
            selected_language = "fa" if re.search(r"[\u0600-\u06ff]", clean + pending_request) else "en"
        full_question = clean
        if pending_request:
            full_question = f"Previous incomplete request: {pending_request}\nUser clarification: {clean}"
        initial: TutorState = {
            "raw_request": clean, "question": full_question,
            "history": deepcopy((history or [])[-MAX_HISTORY_MESSAGES:]),
            "level_choice": level, "previous_level": previous_level,
            "language": selected_language, "analysis": {}, "learner_level": "beginner",
            "plan": {}, "draft": {}, "tool_result": {}, "review": {}, "revision_count": 0,
            "final_answer": "", "status": "running", "verification": "not_applicable", "trace": [],
        }
        return self.graph.invoke(initial, config={"recursion_limit": 24})

    def mermaid(self) -> str:
        return self.graph.get_graph().draw_mermaid()


class TutorSession:
    """Conversation state survives successful turns; transient graph fields do not."""

    def __init__(
        self, agent: MathTutorAgent, *, level: LevelChoice = "auto",
        language: LanguageChoice = "auto",
    ) -> None:
        self.agent = agent
        self.level = level
        self.language = language
        self.history: list[dict[str, str]] = []
        self.pending_request = ""
        self.previous_level = ""
        self.last_state: TutorState | None = None

    def send(self, question: str) -> TutorState:
        state = self.agent.run(
            question, history=self.history, level=self.level, language=self.language,
            pending_request=self.pending_request, previous_level=self.previous_level,
        )
        # Commit only after the full graph succeeds. API errors preserve session context.
        self.last_state = deepcopy(state)
        self.history.extend([
            {"role": "user", "content": question.strip()},
            {"role": "assistant", "content": state["final_answer"][:6000]},
        ])
        self.history = self.history[-MAX_HISTORY_MESSAGES:]
        self.pending_request = state["question"][:4000] if state["status"] == "needs_clarification" else ""
        if state["analysis"]["request_kind"] != "out_of_scope" and state["status"] != "needs_clarification":
            self.previous_level = state["learner_level"]
        return state

    def clear(self) -> None:
        self.history = []
        self.pending_request = ""
        self.previous_level = ""
        self.last_state = None

    def save_state(self, path: Path) -> None:
        if self.last_state is None:
            raise TutorError("There is no completed turn to save.")
        path.write_text(json.dumps(self.last_state, ensure_ascii=False, indent=2), encoding="utf-8")
