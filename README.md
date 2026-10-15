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

A FastAPI service for a personalized LLM SAT tutor. A structured student profile shapes the system prompt,
recent history is replayed on follow-ups, and a crisis screen runs before the model sees any message.

**Live demo:** https://dulkinsar-sat-tutor.hf.space

It rebuilds AIristotle, a tutor I built in 2023–2024 that around 120 students used in its busiest months.
The original code belongs to the company. This version was written in October 2026 with Claude Code
(AI-assisted). [docs/background.md](docs/background.md) covers the original and what it taught me.

## Request path

```
POST /students/{id}/chat
  -> session token check        bearer token from session creation; only its hash is stored
  -> rate limits                per visitor per hour, global per day (proxy-aware visitor identity)
  -> care mode                  for 30 minutes after a crisis flag, every message gets the help reply
  -> crisis screen              local patterns + OpenAI moderation; a hit returns a fixed help message,
                                skips the model, stores nothing, starts care mode and alerts a mentor
  -> history                    latest 40 rows, trimmed to a token budget on whole turns
  -> budget reservation         worst-case cost reserved against DAILY_BUDGET_USD under one lock
  -> model                      OpenAI Responses API (gpt-6-luna), streamed or plain
  -> settle                     log real tokens and cost, release the reservation (same lock)
```

Billing is counted on every exit: a reply cut off at the token cap is billed from its `incomplete`
event, a stream with no usage event and a client that disconnects mid-stream are billed from an
estimate. Reading today's spend and reserving happen under one lock, and so do logging and releasing,
so a concurrent request always sees a call either as spend or as a reservation.

## Student profile

Each field maps to a fixed tutoring rule (`app/profile.py`), so the prompt gets sentences from a table
and no free text except three short notes on recurring errors.

| Field | What the tutor does with it |
|---|---|
| target score, current score, test date | prioritizes high-yield skills; test strategy within 30 days |
| level in Reading and Writing, and in Math | difficulty and how much to scaffold |
| recurring errors (up to 3) | names the slip when a question touches it |
| motivation, test anxiety, confidence | framing, tone, step size, easier or harder follow-ups |
| weekly study hours, planning habit | size of any study plan |
| feedback style, reply language, English level | answer format and wording |
| interests (up to 2) | examples only |

The fields follow what predicts learning in tutoring research: prior knowledge, goals, self-efficacy,
anxiety and self-regulation. Personality types and learning styles are left out.

## Evaluation

**Crisis screen** (`eval/safety_eval.py`): 30 crisis messages (direct, indirect, misspelled, Russian,
Kazakh) and 40 benign ones built to trip a keyword filter.

| Layer | Recall on crisis | False alarms on benign |
|---|---|---|
| Local patterns only | 0.733 (22 of 30) | 0.025 (1 of 40) |
| `omni-moderation-latest` only | 0.933 (28 of 30) | 0.000 |
| Combined (what the app runs) | 0.967 (29 of 30) | 0.025 (1 of 40) |

`eval/results/safety_local.json` holds the local run; the moderation rows come from a run on my machine
whose output is still to be committed.

**Personalization** (`eval/tutor_eval.py`): 30 messages with profiles, each answered with and without the
profile and graded blind to condition by Claude Sonnet 5.5; reply language, numbered steps and sentence
length are checked in code. Tutor `gpt-6-luna`, 14 October 2026; per-item replies and grades are in
`eval/results/`.

| Condition | Correct | Personalized (0-2) | Teaching (0-2) | Format checks | USD per reply |
|---|---|---|---|---|---|
| With profile | 0.93 | 1.70 | 1.87 | 8 of 8 | 0.000141 |
| Without profile | 0.93 | 0.63 | 1.77 | 2 of 8 | 0.000107 |

**Limits of these numbers.** The local patterns and the tutor prompt were both revised after reading
their misses on these same sets, so both results are optimistic. Each eval is one run with one judge.
The stronger tests are a held-out set written by someone else, repeated runs with intervals, and a
second judge. One tutor miss traced to the prompt: 4 to 9 study hours produced no rule, which is fixed.

## API

| Method and path | Purpose |
|---|---|
| `POST /students` | create a session from `name`, `profile`, `tags`; returns `id` and a one-time `token` |
| `PUT /students/{id}/profile`, `PUT /students/{id}/tags` | replace the profile or interests |
| `POST /students/{id}/chat` | one reply as JSON |
| `POST /students/{id}/chat/stream` | server-sent events: `delta`, then `done` or `error` |
| `GET /students/{id}/history` | stored conversation |
| `DELETE /students/{id}` | delete the session and its history |
| `GET /admin/usage` | spend by day, budget, safety events (needs `ADMIN_TOKEN`) |
| `GET /health` | mode, model, moderation status |

Student routes need `Authorization: Bearer <token>`; a wrong token and an unknown id both return 404.

## Run

```bash
pip install -r requirements-dev.txt
pytest                         # offline, no key needed
uvicorn app.main:app --reload  # demo mode without OPENAI_API_KEY
```

Docker: `docker build -t sat-tutor . && docker run -p 8000:8000 -e OPENAI_API_KEY=$OPENAI_API_KEY sat-tutor`.
Key settings are `DAILY_BUDGET_USD` (default 2.0), `CHAT_LIMIT_PER_HOUR`, `TRUSTED_PROXY_HOPS`,
`SAFETY_WEBHOOK_URL` and `ADMIN_TOKEN`; the rest are in `app/`. Limits and reservations live in process
memory, so the app runs as a single worker.
