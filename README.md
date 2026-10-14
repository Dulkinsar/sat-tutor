---
title: SAT Tutor
emoji: 📚
colorFrom: green
colorTo: blue
sdk: docker
app_port: 8000
pinned: false
---

# sat-tutor

[![ci](https://github.com/Dulkinsar/sat-tutor/actions/workflows/ci.yml/badge.svg)](https://github.com/Dulkinsar/sat-tutor/actions/workflows/ci.yml)

A FastAPI service for a personalized LLM SAT tutor. Each student has a short structured profile that
shapes the system prompt, and recent conversation history is replayed on every follow-up. A
crisis-language screen runs before the model sees any message. (The original used 7-10 free-text tags
written by mentors; this version replaces them with the fields below.)

**Live demo:** https://dulkinsar-sat-tutor.hf.space

> **About this repo.** It rebuilds the core of AIristotle, a tutor I built in 2023–2024. In its three most
> active months around 120 students used it, more than half of them daily. The original code belongs to the
> company. This rebuild was written on 13-14 October 2026 with Claude Code (AI-assisted coding), and adds
> the safety engineering the original lacked. The history is squashed into one commit.

## Request path

```
POST /students/{id}/chat
  -> session token check        bearer token issued at session creation; only its hash is stored
  -> rate limits                per visitor per hour, global per day (proxy-aware visitor identity)
  -> care mode                  for 30 minutes after a crisis flag, every message gets the help reply
  -> crisis screen              local patterns + OpenAI moderation; a hit returns a fixed help message,
                                skips the model, stores nothing, starts care mode and alerts a mentor
  -> history                    latest 40 rows by SQL LIMIT, trimmed to a token budget on whole turns
  -> budget reservation         worst-case cost of this call reserved against DAILY_BUDGET_USD
  -> model                      OpenAI Responses API (gpt-6-luna), streamed or plain
  -> store                      user and assistant turn in one transaction, plus tokens and cost
                                (an abandoned stream still logs its estimated cost)
```

## What I learned from the original

AIristotle personalized every reply with 7–10 mentor-written profile tags and the full conversation
history. Use started slowly. The company encouraged students to try it and presented the personalization
as its edge over plain ChatGPT. It took off in late October 2023 and became heavy around February 2024,
once students got hooked. The first sign that usage had outgrown the design was the API bill, which
grew well past forecast. I traced the cost through the chat logs and found students,
aged 14 to 17, confiding personal matters well beyond SAT help, including mental-health crises. The
system had no way to notice: cost was its only alarm.

The original ran on GPT-4, which was warm by default, and students confided in it the way they would in a
friend. In May 2024 the tutor moved to GPT-4o, a model later widely criticized for encouraging emotional
attachment. Current models are tuned to sound less affectionate, so a tutor built on one today gets less of that
warmth for free, and the profile (anxiety, confidence, motivation) carries more of the personalization.

I reported this to my manager and to the founder, and later stepped away from the project. A tutor that
knows a student's interests and history invites the kind of trust a teenager gives a friend, so the
safety work has to ship with the first version. This rebuild adds it:

- **Screening before the model.** `app/safety.py` checks every message first. A hit returns a fixed reply
  pointing to real help; the message is never sent to the model or stored.
- **Follow-ups stay covered.** A flag starts 30 minutes of care mode, because "I mean it, tonight" looks
  harmless on its own.
- **A person finds out.** With `SAFETY_WEBHOOK_URL` set, a mentor channel gets the session id and time
  (never the text). Daily event counts appear on `/admin/usage`.
- **Cost alarms that hold.** Every call logs tokens and USD. Each call reserves its worst-case cost
  before it starts, so concurrent requests cannot overshoot the daily budget, and a model with no
  known price is refused.
- **Bounded personal data.** A validated profile (enumerated fields, no free text except 3 short error notes) and up to 10 short interest tags, all fenced as data; history
  trimmed to a token budget; students can delete their session and history from the UI.

Out of scope here, and needed in production: a named person who reviews flagged sessions, coverage of
abuse and eating-disorder disclosures (the moderation model has no category for them), consent from
parents or guardians, redaction of personal details before text reaches a third-party API, and a
retention limit on logs.

## Student profile

The first version used free-text tags ("likes chess", "targets 1400"). Measured on 20 items, they raised the
judge's personalization score by only 0.2 points out of 2, since most tags give a tutor nothing concrete
to change. The profile now holds fields that each map to a tutoring behaviour (`app/profile.py`):

| Field | What the tutor does with it |
|---|---|
| target score, current score, test date | prioritizes high-yield skills; favours test strategy when the test is within 30 days |
| level in Reading and Writing, and in Math | sets difficulty and how much to scaffold |
| recurring errors (up to 3) | checks the student's work for those slips |
| motivation (own goal, parents, application, unsure) | frames effort accordingly |
| test anxiety and confidence (1-5) | tone, step size, hints, easier or harder follow-ups |
| weekly study hours, planning habit | size of the study plan |
| feedback style (hint first, worked example, numbered steps) | answer format |
| reply language, English level | wording and language |
| interests (up to 2) | examples only |

The fields come from what predicts learning in the research on tutoring: prior knowledge, goals,
self-efficacy, anxiety and self-regulation. Personality types and learning styles are left out, since
matching instruction to them has not been shown to improve outcomes. Every field except the recurring
errors is an enumerated value or number, so the prompt receives fixed sentences, and unknown fields are
rejected.

## Evaluation

**Crisis screen** (`eval/safety_eval.py`, results in `eval/results/`). The set has 30 crisis messages
(direct, indirect, misspelled, Russian and Kazakh) and 40 benign messages chosen to trip a keyword
filter, such as SAT passages about war or death and "this question is killing me".

| Layer | Recall on crisis messages | False alarms on benign messages |
|---|---|---|
| Local patterns only (no key needed) | 0.733 (22 of 30) | 0.025 (1 of 40) |
| OpenAI `omni-moderation-latest` only | 0.933 (28 of 30) | 0.000 |
| Combined (what the app runs) | 0.967 (29 of 30) | 0.025 (1 of 40) |

Run on 14 October 2026.

The patterns were refined after reading their misses on this set and on phrasings from a code review,
so the local recall above is optimistic: on a reviewer's fresh phrasings, before those fixes, the
patterns caught 2 of 18. Keyword matching is a floor. The messages it misses are indirect ("nobody
would notice if I was gone"), which is the moderation layer's job. The moderation number was never tuned
on this set, so it is the fairer of the two; with 30 crisis messages each miss moves recall by 3 points,
and a proper estimate needs a larger held-out set written by someone else. The one false alarm is a
baseball "suicide squeeze".

**Does personalization help?** (`eval/tutor_eval.py`). 30 student messages with structured profiles, each
answered with and without the profile, graded by a judge model that sees the profile but is never told
which condition produced the reply. It scores correctness, use of the profile fields and teaching
quality; length is checked in code. The judge defaults to the tutor's own model, so a fair read uses
another vendor's: `python eval/tutor_eval.py --judge-model claude-sonnet-5-5` (needs `ANTHROPIC_API_KEY`
and `pip install anthropic`). Per-item replies and grades are in `eval/results/tutor_quality.items.jsonl` (second run) and
`tutor_quality.first_run.items.jsonl`; `tutor_quality.json` summarizes the second run.

First run (14 October 2026, the first 20 items and the first version of the profile prompt, `gpt-6-luna` as the tutor,
Claude Sonnet 5.5 as the judge):

| Condition | Correct | Personalized (0-2) | Teaching (0-2) | Replies within 180 words | USD per reply |
|---|---|---|---|---|---|
| With structured profile | 1.00 | 1.05 | 1.80 | 1.00 | 0.000103 |
| Without profile | 1.00 | 0.75 | 1.85 | 1.00 | 0.000088 |

The profile raises the personalization score by 0.30 points on the 0-2 scale, at about 17% more cost per
reply. Correctness stays at 1.00 and teaching quality is unchanged within noise (1.80 against 1.85).
An earlier run with free-text tags and the tutor model as its own judge gave 0.85 against 0.65, a gap of
0.20. The two runs used different judges, so the change from 0.20 to 0.30 is a loose comparison.

Reading the replies showed why the gap was small. A profile asking for Russian replies got English, and
the rules were phrased as background, so replies rarely named a recurring slip or opened with
encouragement for an anxious student. The prompt now states the rules as instructions, places the
language rule last, and the eval gained ten items where a profile should matter plus code-checked format
rules (reply language, numbered steps, short sentences).

Second run (14 October 2026, all 30 items, revised prompt, same tutor and judge):

| Condition | Correct | Personalized (0-2) | Teaching (0-2) | Format checks passed | USD per reply |
|---|---|---|---|---|---|
| With structured profile | 0.93 | 1.70 | 1.87 | 8 of 8 | 0.000141 |
| Without profile | 0.93 | 0.63 | 1.77 | 2 of 8 | 0.000107 |

The personalization score rises by 1.07 points on the 0-2 scale at about 32% more cost per reply, and
teaching quality stays level. The profile leaves correctness unchanged, and every checkable format rule
(reply language, numbered steps, short sentences) is followed with it. Two of the eight pass without it,
since the same profile defines what the checks expect in both conditions.

Read this result with its history in mind. I revised the prompt after reading the first run's replies, so
this run measures a prompt tuned against this evaluation. The ten new items were written after that
reading too. It is a single run with one judge model and 30 items, and the judge sees the profile in both
conditions. A fresh set of items written by someone else, repeated runs and a second judge are the
stronger tests, and this result stands as a first measurement.

Reading all 60 replies after this run turned up three flaws in the evaluation itself. Item t29 was
ambiguous (a blank with no antecedent, so "You're" was as defensible as "They're"), the correctness rubric
marked a hint-first reply wrong for withholding the answer, and the t20 reference mixed content with
profile fit. Correctness (0.93 in both conditions) therefore understates both. The item, reference and rubric
are fixed in the repo, and the numbers above come from the earlier version. One miss belongs to the tutor:
the study plan for a student with 5 free hours a week overshot that budget.

## API

| Method and path | Purpose |
|---|---|
| `POST /students` | create a session with `name`, `profile` and `tags`: returns `id` and a one-time `token` |
| `PUT /students/{id}/tags` | replace interests |
| `PUT /students/{id}/profile` | replace the structured profile |
| `POST /students/{id}/chat` | one reply as JSON |
| `POST /students/{id}/chat/stream` | the same, as server-sent events (`delta`, then `done` or `error`) |
| `GET /students/{id}/history` | stored conversation |
| `DELETE /students/{id}` | delete the session and its history |
| `GET /admin/usage` | spend by day, budget, safety events, limiter size (needs `ADMIN_TOKEN`) |
| `GET /health` | mode, model, moderation status |

Every `/students/{id}/...` route needs `Authorization: Bearer <token>`. A wrong token and an unknown
id both return 404, so ids cannot be probed.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | unset | without it the app runs in a labelled demo mode |
| `TUTOR_MODEL` | `gpt-6-luna` | model for the Responses API |
| `DAILY_BUDGET_USD` | `2.0` | chat pauses when today's spend reaches this |
| `CHAT_LIMIT_PER_HOUR` | `30` | messages per visitor per hour |
| `CHAT_LIMIT_GLOBAL_PER_DAY` | `500` | messages across all visitors per day |
| `SESSIONS_PER_HOUR` | `10` | new sessions per visitor per hour |
| `TRUSTED_PROXY_HOPS` | `1` on Hugging Face and Render, else `0` | proxies in front of the app whose `X-Forwarded-For` entries are trusted |
| `HISTORY_TOKEN_BUDGET` | `3000` | estimated tokens of history replayed per request |
| `SAFETY_SCORE_THRESHOLD` | `0.3` | moderation self-harm score that counts as a hit |
| `SAFETY_CARE_MINUTES` | `30` | how long a session stays in care mode after a flag |
| `SAFETY_WEBHOOK_URL` | unset | where to POST a text-free alert when a message is flagged |
| `TUTOR_PRICE_IN`, `TUTOR_PRICE_OUT` | unset | USD per million tokens, for a model missing from the price table |
| `ADMIN_TOKEN` | unset | enables `/admin/usage` |

Rate limits and budget reservations live in process memory, which suits a single-process demo; several
workers would keep them in Redis. Request bodies over 64 KB are refused before they are read. SQLite sits on temporary storage on Hugging Face, so sessions reset when the
Space restarts.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                  # runs offline, no key needed
python eval/safety_eval.py              # crisis screen, local layer
uvicorn app.main:app --reload           # http://localhost:8000
```

Docker: `docker build -t sat-tutor . && docker run -p 8000:8000 -e OPENAI_API_KEY=$OPENAI_API_KEY sat-tutor`.
The container runs as an unprivileged user. `render.yaml` deploys the same image on Render.

## Layout

```
app/main.py        routes, auth, chat flow, streaming, admin
app/safety.py      crisis screen and the fixed help reply
app/profile.py     structured profile and the rules derived from it
app/tutor.py       persona, prompt building, history trimming, model calls
app/db.py          SQLite schema and queries
app/ratelimit.py   rate limits
app/static/        chat page
eval/              safety and tutor-quality evals
tests/             API and safety tests
```
