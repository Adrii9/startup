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
app/db.py      schema + connection. The event table is append-only and is the
               only source of truth; every other table is a projection.
app/store.py   the two operations. No MCP or HTTP import anywhere in it.
app/render.py  the envelope: what an assistant actually reads.
app/server.py  two wrappers over the same functions — MCP, and plain HTTP.
```

The dual wrapper is not belt-and-braces. Claude and ChatGPT speak MCP; Gemini's
consumer app is US-only for custom MCP servers, so Oscar comes in through
`/api/*` or Gemini CLI. MCP is a convenience here, never a requirement.

## The two tools

- `catch_up()` — the document, plus everything that happened since your cursor.
- `record(summary, intent, rejected, section, content, artifact, supersedes)`

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
https://<host>/u/adria-dev-token/mcp
https://<host>/u/oscar-dev-token/mcp
https://<host>/u/pau-dev-token/mcp
```

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
curl -X POST https://<host>/u/oscar-dev-token/api/record \
  -H 'content-type: application/json' \
  -d '{"summary":"generated the cover","intent":"minimalist","artifact":"cover.png"}'
```

The seeded tokens are fixed so connector URLs survive a rebuild. They are demo
secrets — they become real ones, or OAuth, before anyone outside the three of
you joins.

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
