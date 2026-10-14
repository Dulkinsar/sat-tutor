"""Answers each item in data/tutor_eval.jsonl with and without a structured profile, then has a
judge model score both. Needs OPENAI_API_KEY; a claude-* judge also needs ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import tutor  # noqa: E402

JUDGE_PROMPT = """You are grading an SAT tutor's reply.

Student profile (JSON): {profile}
Student interests: {interests}
Student message: {message}
Reference answer: {reference}

Tutor reply:
<reply>
{reply}
</reply>

Grade with this rubric and return JSON only:
  "correct": 1 if the reply's content agrees with the reference, else 0. Judge facts only; fit to the
             profile belongs under "personalized". When the profile asks for hints first, a hint that
             leads to the reference answer without contradicting it counts as correct
  "personalized": 0 if it ignores the profile, 1 for a generic nod to it,
                  2 if it visibly applies specific profile fields (difficulty, tone, pacing,
                  answer format, language, recurring slips)
  "teaching": 0 if it only states an answer, 1 if it explains,
              2 if it explains step by step and ends with one guiding question
  "note": one short sentence"""


def judge(client, model: str, item: dict, reply: str) -> dict:
    prompt = JUDGE_PROMPT.format(
        profile=json.dumps(item["profile"]),
        interests=", ".join(item["interests"]) or "none",
        message=item["message"],
        reference=item["reference"],
        reply=reply,
    )
    if model.startswith("claude"):
        import anthropic  # pip install anthropic; reads ANTHROPIC_API_KEY

        msg = anthropic.Anthropic().messages.create(
            model=model, max_tokens=2000, messages=[{"role": "user", "content": prompt}]
        )
        # the reply can open with a thinking block, so join only the text blocks
        text = "".join(b.text for b in msg.content if b.type == "text")
    else:
        text = client.responses.create(model=model, input=prompt, max_output_tokens=800).output_text
    return json.loads(text[text.find("{") : text.rfind("}") + 1])


def format_checks(profile: dict, reply: str) -> dict[str, bool]:
    """Checks that code can verify, for the profile fields that change how a reply looks."""
    out = {}
    if profile.get("reply_language") in ("ru", "kk"):
        letters = [c for c in reply if c.isalpha()]
        out["language"] = bool(letters) and sum("\u0400" <= c <= "\u04ff" for c in letters) / len(letters) >= 0.3
    if profile.get("feedback_style") == "numbered_steps":
        out["numbered_steps"] = bool(re.search(r"^\s*1[.)]", reply, re.M))
    if profile.get("english_level") == "basic":
        sentences = [x for x in re.split(r"[.!?]+", reply) if x.strip()]
        out["short_sentences"] = bool(sentences) and len(reply.split()) / len(sentences) <= 14
    return out


def run(items: list[dict], judge_model: str) -> tuple[dict, list[dict]]:
    client = tutor.client()
    rows = []
    for item in items:
        for condition, profile, interests in (
            ("with_profile", item["profile"], item["interests"]),
            ("no_profile", {}, []),
        ):
            print(f"{item['id']} {condition}", file=sys.stderr, flush=True)
            reply, usage = tutor.reply(tutor.build_messages("Student", interests, [], item["message"], profile))
            grade = judge(client, judge_model, item, reply)
            rows.append(
                {
                    "id": item["id"],
                    "condition": condition,
                    "reply": reply,
                    "words": len(reply.split()),
                    "usd": usage.usd,
                    "checks": format_checks(item["profile"], reply),
                    **{k: grade.get(k) for k in ("correct", "personalized", "teaching", "note")},
                }
            )
    summary = {}
    for condition in ("with_profile", "no_profile"):
        part = [r for r in rows if r["condition"] == condition]
        summary[condition] = {
            "correct": round(statistics.mean(r["correct"] for r in part), 3),
            "personalized": round(statistics.mean(r["personalized"] for r in part), 3),
            "teaching": round(statistics.mean(r["teaching"] for r in part), 3),
            "within_180_words": round(statistics.mean(r["words"] <= 180 for r in part), 3),
            "usd_per_reply": round(statistics.mean(r["usd"] for r in part), 6),
        }
        checks = [ok for r in part for ok in r["checks"].values()]
        summary[condition]["format_checks_passed"] = f"{sum(checks)} of {len(checks)}"
    return summary, rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--judge-model", default="gpt-6-luna")
    p.add_argument("--limit", type=int)
    p.add_argument("--out", type=Path, default=ROOT / "eval" / "results")
    args = p.parse_args()
    if tutor.demo_mode():
        print("OPENAI_API_KEY is not set; this evaluation needs the real model.")
        return 1
    items = [json.loads(x) for x in (ROOT / "data/tutor_eval.jsonl").read_text(encoding="utf-8").splitlines()]
    summary, rows = run(items[: args.limit], args.judge_model)
    summary["models"] = {"tutor": tutor.model(), "judge": args.judge_model}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "tutor_quality.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (args.out / "tutor_quality.items.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
