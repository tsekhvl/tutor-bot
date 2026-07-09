"""Модели интерактивной контрольной (три формата)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class QuestionType(str, Enum):
    CHOICE = "choice"
    OPEN = "open"
    DEBATE = "debate"


class CheckMethod(str, Enum):
    ALGORITHMIC = "algorithmic"
    GEMINI = "gemini"


class ControlMode(str, Enum):
    CARAVAN = "caravan"
    BOSSES = "bosses"
    MILLIONAIRE = "millionaire"


@dataclass
class QuestionOption:
    id: str
    text: str
    correct: bool = False


@dataclass
class Question:
    id: str
    seminar: str
    type: QuestionType
    prompt: str
    options: list[QuestionOption] = field(default_factory=list)
    rubric: str = ""
    hint: str = ""
    thesis: str = ""
    difficulty: int = 1
    tags: list[str] = field(default_factory=list)

    def correct_option_ids(self) -> set[str]:
        return {o.id for o in self.options if o.correct}

    def option_by_id(self, option_id: str) -> QuestionOption | None:
        for o in self.options:
            if o.id == option_id:
                return o
        return None


@dataclass
class AnswerCheck:
    correct: bool
    score: int
    feedback: str
    method: CheckMethod


@dataclass
class CaravanStop:
    id: str
    city: str
    seminar: str
    question_id: str | None = None
    event_text: str = ""
    fallback_question_id: str | None = None
    question_ids: list[str] = field(default_factory=list)
    fallback_question_ids: list[str] = field(default_factory=list)

    def main_pool(self) -> list[str]:
        ids = list(self.question_ids)
        if self.question_id:
            ids.append(self.question_id)
        return list(dict.fromkeys(ids))

    def fallback_pool(self) -> list[str]:
        ids = list(self.fallback_question_ids)
        if self.fallback_question_id:
            ids.append(self.fallback_question_id)
        return list(dict.fromkeys(ids))


@dataclass
class BossDef:
    id: str
    seminar: str
    name: str
    opponent: str = ""
    debate_ids: list[str] = field(default_factory=list)


@dataclass
class MillionaireStep:
    step: int
    seminar: str
    question_id: str | None = None
    question_ids: list[str] = field(default_factory=list)

    def question_pool(self) -> list[str]:
        ids = list(self.question_ids)
        if self.question_id:
            ids.append(self.question_id)
        return list(dict.fromkeys(ids))


@dataclass
class ControlPool:
    questions: dict[str, Question]
    caravan_stops: list[CaravanStop]
    caravan_pass_min: int
    bosses: list[BossDef]
    boss_hp_start: int
    boss_pass_defeated: int
    millionaire_steps: list[MillionaireStep]
    millionaire_full_score: int


# --- Состояние сессии в context.user_data["control"] ---

ControlPhase = Literal[
    "await_fio",
    "await_mode",
    "playing",
    "await_open",
    "show_result",
    "finished",
]


@dataclass
class PendingQuestion:
    question_id: str
    context_label: str
    allow_retry: bool = False
    retry_used: bool = False
    hidden_option_ids: list[str] = field(default_factory=list)


@dataclass
class CaravanState:
    stop_index: int = 0
    seals: int = 0
    seals_opportunities: int = 0
    awaiting_fallback: bool = False
    roll_seed: int = 0
    picks: dict[str, dict[str, str | None]] = field(default_factory=dict)


@dataclass
class BossesState:
    boss_index: int = 0
    defeated: int = 0
    roll_seed: int = 0
    picks: dict[str, str] = field(default_factory=dict)


@dataclass
class MillionaireState:
    step_index: int = 0
    correct_count: int = 0
    lifeline_5050: bool = True
    lifeline_ulema: bool = True
    lifeline_retry: bool = True
    hidden_option_ids: list[str] = field(default_factory=list)
    roll_seed: int = 0
    picks: dict[str, str] = field(default_factory=dict)


@dataclass
class ControlSession:
    mode: ControlMode | None = None
    phase: ControlPhase = "await_fio"
    fio: str = ""
    pending: PendingQuestion | None = None
    caravan: CaravanState = field(default_factory=CaravanState)
    bosses: BossesState = field(default_factory=BossesState)
    millionaire: MillionaireState = field(default_factory=MillionaireState)
    last_check: AnswerCheck | None = None
    log: list[dict[str, Any]] = field(default_factory=list)
    passed: bool | None = None
    summary: str = ""
    final_grade: int | None = None
    final_grade_max: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value if self.mode else None,
            "phase": self.phase,
            "fio": self.fio,
            "pending": (
                {
                    "question_id": self.pending.question_id,
                    "context_label": self.pending.context_label,
                    "allow_retry": self.pending.allow_retry,
                    "retry_used": self.pending.retry_used,
                    "hidden_option_ids": self.pending.hidden_option_ids,
                }
                if self.pending
                else None
            ),
            "caravan": {
                "stop_index": self.caravan.stop_index,
                "seals": self.caravan.seals,
                "seals_opportunities": self.caravan.seals_opportunities,
                "awaiting_fallback": self.caravan.awaiting_fallback,
                "roll_seed": self.caravan.roll_seed,
                "picks": self.caravan.picks,
            },
            "bosses": {
                "boss_index": self.bosses.boss_index,
                "defeated": self.bosses.defeated,
                "roll_seed": self.bosses.roll_seed,
                "picks": self.bosses.picks,
            },
            "millionaire": {
                "step_index": self.millionaire.step_index,
                "correct_count": self.millionaire.correct_count,
                "lifeline_5050": self.millionaire.lifeline_5050,
                "lifeline_ulema": self.millionaire.lifeline_ulema,
                "lifeline_retry": self.millionaire.lifeline_retry,
                "hidden_option_ids": self.millionaire.hidden_option_ids,
                "roll_seed": self.millionaire.roll_seed,
                "picks": self.millionaire.picks,
            },
            "log": self.log,
            "passed": self.passed,
            "summary": self.summary,
            "final_grade": self.final_grade,
            "final_grade_max": self.final_grade_max,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ControlSession:
        s = cls()
        raw_mode = data.get("mode")
        if raw_mode:
            s.mode = ControlMode(raw_mode)
        s.phase = data.get("phase", "await_fio")
        s.fio = str(data.get("fio", ""))
        pending = data.get("pending")
        if pending:
            s.pending = PendingQuestion(
                question_id=str(pending["question_id"]),
                context_label=str(pending.get("context_label", "")),
                allow_retry=bool(pending.get("allow_retry")),
                retry_used=bool(pending.get("retry_used")),
                hidden_option_ids=list(pending.get("hidden_option_ids") or []),
            )
        c = data.get("caravan") or {}
        picks_raw = c.get("picks") or {}
        picks: dict[str, dict[str, str | None]] = {}
        if isinstance(picks_raw, dict):
            for stop_id, pair in picks_raw.items():
                if isinstance(pair, dict):
                    picks[str(stop_id)] = {
                        "main": pair.get("main"),
                        "fallback": pair.get("fallback"),
                    }
        s.caravan = CaravanState(
            stop_index=int(c.get("stop_index", 0)),
            seals=int(c.get("seals", 0)),
            seals_opportunities=int(c.get("seals_opportunities", 0)),
            awaiting_fallback=bool(c.get("awaiting_fallback")),
            roll_seed=int(c.get("roll_seed", 0)),
            picks=picks,
        )
        b = data.get("bosses") or {}
        picks_boss = b.get("picks") or {}
        s.bosses = BossesState(
            boss_index=int(b.get("boss_index", 0)),
            defeated=int(b.get("defeated", 0)),
            roll_seed=int(b.get("roll_seed", 0)),
            picks={str(k): str(v) for k, v in picks_boss.items()} if isinstance(picks_boss, dict) else {},
        )
        m = data.get("millionaire") or {}
        picks_m = m.get("picks") or {}
        s.millionaire = MillionaireState(
            step_index=int(m.get("step_index", 0)),
            correct_count=int(m.get("correct_count", 0)),
            lifeline_5050=bool(m.get("lifeline_5050", True)),
            lifeline_ulema=bool(m.get("lifeline_ulema", True)),
            lifeline_retry=bool(m.get("lifeline_retry", True)),
            hidden_option_ids=list(m.get("hidden_option_ids") or []),
            roll_seed=int(m.get("roll_seed", 0)),
            picks={str(k): str(v) for k, v in picks_m.items()} if isinstance(picks_m, dict) else {},
        )
        s.log = list(data.get("log") or [])
        passed = data.get("passed")
        s.passed = passed if passed is None else bool(passed)
        s.summary = str(data.get("summary", ""))
        fg = data.get("final_grade")
        s.final_grade = int(fg) if fg is not None else None
        fgm = data.get("final_grade_max")
        s.final_grade_max = int(fgm) if fgm is not None else None
        return s
