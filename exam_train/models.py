"""Модели тренажёра к экзамену."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

ExamPhase = Literal[
    "await_block",
    "await_task_type",
    "await_main_answer",
    "await_followup_answer",
    "finished",
]


class TaskKind(str, Enum):
    # Блок 1
    TOPIC = "topic"
    TERMS = "terms"
    ESSAY = "essay"
    # Блок 3
    DATE = "date"
    PERSONALITY = "personality"
    TERM = "term"
    PERIOD = "period"


BLOCK1_TASKS = (TaskKind.TOPIC, TaskKind.TERMS, TaskKind.ESSAY)
BLOCK3_TASKS = (TaskKind.DATE, TaskKind.PERSONALITY, TaskKind.TERM, TaskKind.PERIOD)


@dataclass
class ExamQuestion:
    task_type: TaskKind
    prompt: str
    block: str = "1"
    context_text: str = ""
    terms: list[str] = field(default_factory=list)
    item_index: int | None = None


@dataclass
class ExamTrainPool:
    block: str
    title: str
    student_hint: str = ""
    task_types: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    essays: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    personalities: list[str] = field(default_factory=list)
    periods: list[str] = field(default_factory=list)

    def available_task_kinds(self) -> list[TaskKind]:
        if self.task_types:
            return [TaskKind(t) for t in self.task_types]
        if self.block == "3":
            return list(BLOCK3_TASKS)
        return list(BLOCK1_TASKS)


@dataclass
class ExamTrainSession:
    phase: ExamPhase = "await_block"
    block: str = ""
    task_type: TaskKind | None = None
    question: ExamQuestion | None = None
    main_answer: str = ""
    main_answer_source: str = "text"
    followup_question: str = ""
    followup_answer: str = ""
    followup_answer_source: str = "text"
    log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "block": self.block,
            "task_type": self.task_type.value if self.task_type else None,
            "question": (
                {
                    "task_type": self.question.task_type.value,
                    "prompt": self.question.prompt,
                    "block": self.question.block,
                    "context_text": self.question.context_text,
                    "terms": self.question.terms,
                    "item_index": self.question.item_index,
                }
                if self.question
                else None
            ),
            "main_answer": self.main_answer,
            "main_answer_source": self.main_answer_source,
            "followup_question": self.followup_question,
            "followup_answer": self.followup_answer,
            "followup_answer_source": self.followup_answer_source,
            "log": self.log,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExamTrainSession:
        s = cls()
        s.phase = data.get("phase", "await_block")
        s.block = str(data.get("block", ""))
        raw_type = data.get("task_type")
        if raw_type:
            s.task_type = TaskKind(raw_type)
        q = data.get("question")
        if q:
            s.question = ExamQuestion(
                task_type=TaskKind(q["task_type"]),
                prompt=str(q.get("prompt", "")),
                block=str(q.get("block", s.block or "1")),
                context_text=str(q.get("context_text", "")),
                terms=list(q.get("terms") or []),
                item_index=q.get("item_index", q.get("topic_index")),
            )
        s.main_answer = str(data.get("main_answer", ""))
        s.main_answer_source = str(data.get("main_answer_source", "text"))
        s.followup_question = str(data.get("followup_question", ""))
        s.followup_answer = str(data.get("followup_answer", ""))
        s.followup_answer_source = str(data.get("followup_answer_source", "text"))
        s.log = list(data.get("log") or [])
        return s
