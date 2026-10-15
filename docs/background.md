# AIristotle: the original and what it taught me

## The product

AIristotle was an LLM SAT tutor I built at Impact Admissions, where I taught SAT and IELTS.
We wrote the idea down in September 2023 and started building on 25 October 2023. The persona was a
patient teacher named after Aristotle. Each student had 7 to 10 profile tags written by their mentor
("targets 1400", "likes chess"), and the tags went into a prompt template
along with the whole conversation so far.

It ran on GPT-4 at first and moved to GPT-4o in May 2024.

## How it was used

Use started slowly. The company asked students to try it and presented the personalization as the
reason to choose it over plain ChatGPT. After a few encouraged sessions students kept coming back on
their own: use took off in late October 2023 and became heavy around February 2024. In the busiest
three months about 120 students used it, more than half of them daily. Summer brought a drop, and the
company began enrolling Year 8 students (about 14 years old), so the users got younger.

## What went wrong

The first sign that use had outgrown the design was the API bill, which grew well past forecast. I traced
the cost through usage and chat logs. Students aged 14 to 17 were confiding personal matters far beyond
SAT help, including mental-health crises. GPT-4 was warm by default, GPT-4o warmer still, and the
personalization made the tutor feel like it knew them. The system had no way to notice any of this:
the bill was its only alarm.

I reported it to my manager and the founder and later stepped away from the project. My last
conversations about it with the CEO were in August 2024.

## What this rebuild changes

- A crisis screen runs before the model; a hit skips the model, stores nothing, keeps the session in
  care mode for 30 minutes and can alert a mentor without sending the text.
- Every call is budgeted and billed, including cut-off replies and abandoned streams, so cost is
  tracked even when nothing is wrong.
- Free-text tags became a structured profile whose fields each map to a tutoring rule, and the effect
  is measured against a no-profile baseline (see the README).

Still missing for production: a named person who reviews flagged sessions, coverage of abuse and
eating-disorder disclosures, consent from parents or guardians, redaction of personal details before text
reaches a third-party API, and a retention limit on logs.
