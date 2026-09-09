# Linking a conversation to the workspace

The server cannot make an assistant call anything. Linking is therefore not a
switch we flip — it is an instruction each person gives their own assistant,
once, and from then on the assistant knows it is working inside a shared
project rather than alone.

## Claude

Create a **Project** in Claude, add the connector to it, and paste the text
below into the project's instructions. Every conversation started inside that
project is then linked.

---

This conversation is part of a shared project workspace. Other people are
working in the same workspace, each with their own AI assistant.

**Before doing anything in this project, call `catch_up`.** It returns where
things stand — the decisions in force, what the team knows, what is still open —
followed by the document and anything teammates have done since I last looked.
Do not rely on what you remember from earlier in this conversation: other people
change things while we talk.

**Call `record` when something is settled, produced, or dropped:**

- we decide something → `kind: "decision"`, with what we rejected and why
- something becomes true about the project → `kind: "fact"`
- a question is left hanging → `kind: "question"`
- we produce something → `kind: "work"`

**At the end of any exchange where we worked something out, record it.** Err on
the side of recording: a thin entry can be improved later, a lost decision
cannot be recovered. If you are unsure whether something is worth an entry, it
is.

What does not need recording is the step-by-step of how we got there — one
entry per thing established, not one per turn. A log of everything is another
chat transcript, and nobody reads chat transcripts.

**When a stretch of work ends, call `record` once** summarising what we worked
out, what we were aiming for, and what we tried and abandoned. That last part
exists only inside this conversation and is lost the moment it ends.

**Write every entry for a teammate's assistant that cannot see this conversation
and never will.** Put the substance in `details` — the reasoning, the numbers,
the wording we agreed. `summary` is only the headline.

If I did not say why I wanted something, leave `intent` empty. Do not
reconstruct a reason: an invented rationale reaches my teammates as fact.

When a decision reverses an earlier one, or an answer closes an open question,
pass `supersedes` with that entry's number. Otherwise the standing summary keeps
showing the dead one and everyone keeps acting on it.

---

## ChatGPT and Gemini

Same text. In ChatGPT it goes in a Project's instructions; in Gemini CLI it goes
in `GEMINI.md`. Both are wired up but neither has been used in anger yet — get
it working between Claudes first, then port it.
