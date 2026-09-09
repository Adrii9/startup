# shared-context — M0

One workspace that three people's AI assistants write into and read from, so
nobody has to paste screenshots between chats.

**M0 proves one thing only: the write/read loop closes.** Someone records work
through their own assistant; everyone else's assistant learns about it the next
time it touches the workspace for any reason.

## Run it

```bash
uv run uvicorn app.server:app --port 8765 --reload
```

Then open <http://127.0.0.1:8765/> — the document and the log, readable with no
AI connected at all. That is deliberate: a new teammate should see the project
moving before being asked to configure anything.

```bash
uv run pytest
```

Eight tests cover the loop end to end without any assistant. Keep debugging
here — opening three chat windows is for validating the magic moment, not for
chasing a `NULL`.

## Shape

```
app/db.py            schema + connection. The event table is append-only and is
                     the only source of truth; every other table is a projection.
app/store.py         the two operations, and derive_level. No MCP or HTTP import.
app/render.py        the envelope: what an assistant actually reads.
app/server.py        wrappers over the same functions — MCP, plain HTTP, the page.
app/static/          the reader's view: board, graph, detail panel, CA/ES/EN.
```

## What an entry counts as

`derive_level` decides, and the writer does not. Declaration sets the category
where one was given — a fact answering a question stays a fact — and structure
**promotes** work that turned out to be more than work: a dropped option or a
superseded entry makes it a decision whether or not the model thought to say so.
Structure never demotes.

Within plain work an artifact outranks a dropped option, so producing a file
with a choice along the way stays history instead of crowding the brief.

The point is that a model cannot inflate the importance of its own entry, and
three different models cannot drift apart on what counts as a decision, because
none of them is being asked to judge. The detail panel in the web view says out
loud which field a level came from, and whether that level survives into the
brief — the rules are meant to be auditable, not magic.

The dual wrapper is not belt-and-braces. Claude and ChatGPT speak MCP; Gemini's
consumer app is US-only for custom MCP servers, so Oscar comes in through
`/api/*` or Gemini CLI. MCP is a convenience here, never a requirement.

## History, and what is true now

The log answers *what happened*. An assistant sitting down to work needs a
different question answered: *what is true right now*. History grows forever;
the standing summary does not.

So `catch_up` leads with **the brief** — every entry not yet superseded, grouped
into decisions in force, what the team knows, and what is still open. It is
computed from the log on every read and never written. Each item collapses to
**one line**, because its job is to be an index that fits in every assistant's
context however long the project runs; the body stays in the log for whoever
needs it. If this ever stops fitting on a screen, it has stopped working.

A decision keeps its reason in the brief. A decision without its reason gets
quietly re-litigated.

`supersedes` carries three meanings with one pointer: a decision reversed, a
question answered, a fact corrected. Anything superseded drops out of the brief.

## The two tools

- `catch_up()` — the brief, the document, then everything since your cursor.
- `record(summary, details, kind, intent, rejected, section, content, artifact, supersedes)`

`kind` is one of `decision`, `fact`, `question`, `work`. The first three stay in
the brief; `work` scrolls away into history. When a model omits it, it is
inferred rather than rejected — a wrong guess is recoverable, a failed tool call
in the middle of someone's work is not.

`summary` is the headline; `details` is the substance. See [LINKING.md](LINKING.md)
for the instructions that make an assistant do this without being asked.

`intent` and `rejected` are first-class parameters of the only write tool, so a
model cannot skip them without seeing them. Their descriptions explicitly
authorise leaving them blank, which is what stops a model inventing a rationale
nobody gave — an invented reason is worse than none, because the other two
assistants read it as fact.

**Every response carries the delta.** You cannot push to an LLM, so the news
rides along with whatever the assistant was already doing.

## Connecting an assistant

Each person has their own URL. The token is a path segment, which survives
proxies and client-side URL rewriting better than a query string:

```
https://<host>/u/<token>/mcp
```

**Tokens never live in this repo.** Set `MEMBER_TOKENS` in the environment as
`Adria:xxx,Oscar:yyy,Pau:zzz`, or set nothing and let the server mint random
ones on first boot — it prints all three paths to the log at startup. Members
keep their token across restarts, so a redeploy never invalidates a connector
someone has already configured.

> **`localhost` will not work.** Claude's connectors reach your server from
> Anthropic's cloud, not from your laptop. Deploy to a public HTTPS host from
> day one and always work against that URL — a tunnel with a rotating address
> means reconfiguring three connectors every morning.

| | Where |
|---|---|
| Claude | Settings → Connectors → Add custom connector → paste the URL |
| ChatGPT | Enable developer mode, then add the URL as a custom connector |
| Gemini | Gemini CLI (the consumer app's custom-MCP feature is US-only), or `/api/*` |

For anything that does not speak MCP:

```bash
curl -X POST https://<host>/u/<token>/api/record \
  -H 'content-type: application/json' \
  -d '{"summary":"generated the cover","intent":"minimalist","artifact":"cover.png"}'
```

A token is the whole of the auth story right now. That is fine for three people
who know each other, and stops being fine the moment a workspace is shared with
anyone else — at which point this becomes OAuth.

## What M0 deliberately does not have

No accounts, no OAuth, no permissions, no invitations, no live web updates, no
conflict handling beyond last-write-wins, no knowledge graph, no search. One
fixed workspace, three seeded members.

Delete `workspace.db` to start over.

## Next

**M1** — connect all three assistants for real and do a whole piece of work
this way. Do not build past this point until the three of you have seen the
moment land: Oscar asks his Gemini for something, and Adrià's Claude knows about
it without anyone pasting anything. If that does not impress you, nothing after
it will.
