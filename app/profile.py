"""Structured student profile. Every field changes something the tutor does, and all of them
except recurring_errors are enumerated values, so the prompt gets fixed sentences instead of
free text.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Level = Literal["beginner", "developing", "solid", "advanced"]
LANGUAGES = {"en": "English", "ru": "Russian", "kk": "Kazakh", "tr": "Turkish", "it": "Italian"}


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_score: Optional[int] = Field(None, ge=400, le=1600)
    current_score: Optional[int] = Field(None, ge=400, le=1600)
    test_date: Optional[date] = None
    level_reading_writing: Optional[Level] = None
    level_math: Optional[Level] = None
    recurring_errors: list[str] = Field(default_factory=list, max_length=3)
    motivation: Optional[Literal["own_goal", "parents", "admissions", "unsure"]] = None
    confidence: Optional[int] = Field(None, ge=1, le=5)
    test_anxiety: Optional[int] = Field(None, ge=1, le=5)
    study_hours_per_week: Optional[int] = Field(None, ge=0, le=40)
    planning: Optional[Literal["plans_ahead", "last_minute"]] = None
    feedback_style: Optional[Literal["hint_first", "worked_example", "numbered_steps"]] = None
    reply_language: Optional[Literal["en", "ru", "kk", "tr", "it"]] = None
    english_level: Optional[Literal["basic", "intermediate", "fluent"]] = None

    @field_validator("recurring_errors")
    @classmethod
    def clean_errors(cls, v: list[str]) -> list[str]:
        out = []
        for item in v:
            item = " ".join(item.replace("<", "").replace(">", "").split())
            if len(item) > 60:
                raise ValueError("each recurring error must be at most 60 characters")
            if item:
                out.append(item)
        return out


LEVEL_RULES = {
    "beginner": "start from fundamentals, define terms, and teach one idea at a time",
    "developing": "explain the method briefly and let the student do the next step",
    "solid": "skip the basics and focus on traps and speed",
    "advanced": "use harder variants and ask the student to explain their reasoning",
}
MOTIVATION_RULES = {
    "own_goal": "connect practice to the student's own goal",
    "parents": "frame effort around the student's own reasons and avoid pressure language",
    "admissions": "tie practice to the score the application needs",
    "unsure": "keep sessions short and show quick progress to build momentum",
}
FEEDBACK_RULES = {
    "hint_first": "give a hint first and reveal the full solution only after the student tries",
    "worked_example": "show a short worked example, then give a similar problem",
    "numbered_steps": "answer in short numbered steps",
}


def _bounds(p: Profile, today: date) -> list[str]:
    lines = []
    if p.target_score and p.current_score:
        gap = p.target_score - p.current_score
        if gap > 0:
            lines.append(f"Target {p.target_score}, now {p.current_score} (gap {gap}): prioritize high-yield skills.")
        else:
            lines.append(f"Target {p.target_score} already reached at {p.current_score}: raise difficulty.")
    elif p.target_score:
        lines.append(f"Target score {p.target_score}.")
    if p.test_date:
        days = (p.test_date - today).days
        if 0 <= days <= 30:
            lines.append(f"The test is in {days} days: favour test strategy and timed practice over new topics.")
        elif days > 30:
            lines.append(f"The test is in {days} days: build skills steadily.")
    return lines


def instructions(p: Profile, today: date | None = None) -> list[str]:
    """Behaviour rules derived from the profile, as fixed sentences."""
    today = today or date.today()
    lines = _bounds(p, today)
    for label, level in (("Reading and Writing", p.level_reading_writing), ("Math", p.level_math)):
        if level:
            lines.append(f"{label} level {level}: {LEVEL_RULES[level]}.")
    if p.recurring_errors:
        slips = "; ".join(p.recurring_errors)
        lines.append(f"Recurring slips (when the question touches one, name it in one sentence): {slips}.")
    if p.motivation:
        lines.append(f"Motivation: {MOTIVATION_RULES[p.motivation]}.")
    if p.test_anxiety and p.test_anxiety >= 4:
        lines.append(
            "High test anxiety: open with one calming sentence, go one step at a time, "
            "normalize mistakes, no time pressure."
        )
    elif p.test_anxiety and p.test_anxiety <= 2:
        lines.append("Low test anxiety: timed practice is fine.")
    if p.confidence and p.confidence <= 2:
        lines.append("Low confidence: begin with one encouraging sentence and start from an easier variant.")
    elif p.confidence and p.confidence >= 4:
        lines.append("High confidence: finish with a harder follow-up.")
    if p.study_hours_per_week is not None:
        if p.study_hours_per_week <= 3:
            lines.append("Little study time: suggest one focus skill per session.")
        elif p.study_hours_per_week < 10:
            lines.append(
                f"About {p.study_hours_per_week} study hours a week: keep any plan within that time, "
                "with one or two focus skills."
            )
        elif p.study_hours_per_week >= 10:
            lines.append("Plenty of study time: multi-skill plans are fine.")
    if p.planning == "last_minute":
        lines.append("Tends to plan late: keep plans short and concrete.")
    elif p.planning == "plans_ahead":
        lines.append("Plans ahead: offer a weekly plan when asked what to study.")
    if p.feedback_style:
        lines.append(f"Feedback style: {FEEDBACK_RULES[p.feedback_style]}.")
    if p.english_level == "basic":
        lines.append("Basic English: short sentences and simple vocabulary.")
    elif p.english_level == "intermediate":
        lines.append("Intermediate English: avoid idioms.")
    return lines


def language_line(p: Profile) -> str:
    """Closing instruction for the reply language, placed last in the system prompt."""
    if p.reply_language and p.reply_language != "en":
        return f"Write the whole reply in {LANGUAGES[p.reply_language]}, keeping SAT terms in English."
    return ""
