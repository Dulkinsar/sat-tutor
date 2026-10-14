"""Scores the crisis screen on data/safety_eval.jsonl. Add --moderation to include OpenAI
moderation (needs OPENAI_API_KEY).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import safety  # noqa: E402


def score(rows: list[dict], flags: list[bool]) -> dict:
    crisis = [f for r, f in zip(rows, flags, strict=True) if r["label"] == "crisis"]
    benign = [f for r, f in zip(rows, flags, strict=True) if r["label"] == "benign"]
    return {
        "recall": round(sum(crisis) / len(crisis), 3),
        "false_alarm_rate": round(sum(benign) / len(benign), 3),
        "missed": [r["text"] for r, f in zip(rows, flags, strict=True) if r["label"] == "crisis" and not f],
        "false_alarms": [r["text"] for r, f in zip(rows, flags, strict=True) if r["label"] == "benign" and f],
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--moderation", action="store_true")
    p.add_argument("--out", type=Path, default=ROOT / "eval" / "results")
    args = p.parse_args()

    rows = [json.loads(line) for line in (ROOT / "data/safety_eval.jsonl").read_text(encoding="utf-8").splitlines()]
    results = {
        "items": {
            "crisis": sum(r["label"] == "crisis" for r in rows),
            "benign": sum(r["label"] == "benign" for r in rows),
        }
    }
    local = [safety.local_screen(r["text"]).flagged for r in rows]
    results["local_patterns"] = score(rows, local)

    if args.moderation:
        from openai import OpenAI

        client = OpenAI()
        moderation = [safety.moderation_screen(r["text"], client).flagged for r in rows]
        results["moderation_only"] = score(rows, moderation)
        results["combined"] = score(rows, [a or b for a, b in zip(local, moderation, strict=True)])

    args.out.mkdir(parents=True, exist_ok=True)
    name = "safety_combined" if args.moderation else "safety_local"
    (args.out / f"{name}.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    for layer, r in results.items():
        if layer != "items":
            print(f"{layer:16} recall {r['recall']:.3f}   false alarms {r['false_alarm_rate']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
