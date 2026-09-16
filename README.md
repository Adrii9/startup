# shared-context

Shared projects that several people's AI assistants write into and read from,
so nobody has to paste screenshots between chats. What travels between them is
not files but the reasoning: what was decided, what was wanted, and what was
tried and dropped — the part that is lost when a conversation ends.

## Run it

```bash
uv run uvicorn app.server:app --port 8765 --reload
```

Open <http://127.0.0.1:8765/> and create an account.

```bash
uv run pytest
```

The suite covers accounts, connections, projects, invites, deletion and the
write/read loop, with neither an assistant nor Google involved. Keep debugging
here: opening three chat windows is for validating the magic moment, not for
chasing a `NULL`.

## Shape

```
app/db.py        schema. The event log is append-only and is the source of truth.
app/store.py     everything the product does. No MCP, HTTP or browser code in it.
app/passwords.py hashing and checking passwords.
app/auth.py      the optional Google sign-in.
app/linking.py   the text that links a conversation to a project.
app/files.py     storing uploads and pulling the text out of them.
app/render.py    what an assistant actually reads back from a tool call.
app/server.py    three surfaces over store.py: MCP, plain HTTP, and the web.
app/static/      the web: board, graph, detail panel, account and project admin.
```

## Two credentials, never interchangeable

| | For | How you get it | What it can do |
|---|---|---|---|
| **Session** | a person in a browser | signing in | manage your account and projects |
| **Connection** | one assistant | Account → Connections, in the web | read and write the projects you are in |

A connection token lives inside a connector URL, gets pasted into assistant
settings, and ends up in screenshots. So it is only ever a write key for the
projects you are in: it cannot sign in to the web or manage anything, and you
can revoke one without touching your session or your other assistants.

Only hashes are stored, for both. A connection is shown exactly once, when it is
made; a copy of the database is not a way into anyone's account.

## Signing in

With a username and a password. Nothing to configure.

- Passwords are hashed with **scrypt** (standard library, about 20 ms each), with a
  salt per account. The stored value is never the password.
- A failed sign-in takes the same time and gives the same answer whether or not
  the username exists, so the box does not reveal who has an account.
- **Five wrong passwords for one username, or twenty from one address, lock
  sign-in for 15 minutes** — and while locked even the right password is refused,
  so a guesser learns nothing. Kept in memory; a restart clears it.
- Changing your password signs out every other browser you were signed in on.
- Usernames are unique regardless of case and may use any alphabet ("Adrià").

**There is no self-service password reset**, because the server sends no email.
Whoever runs the server sets `RESET_PASSWORD="username:the new password"`,
redeploys, and **removes the variable again** — it applies on every boot while it
is set, and ends that account's sessions. It grants nothing new: anyone who can
set an environment variable can already replace the code and read the database.

Google sign-in is built too, and stays hidden unless configured. To switch it on,
set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `PUBLIC_URL`, and register
`<PUBLIC_URL>/auth/google/callback` as the redirect URI in Google Cloud.

## Projects, roles and invites

A project has one **owner** and any number of **members**. The owner renames,
invites, removes people, hands over ownership and deletes; a member reads,
writes through their assistant, and can leave. A project is never left without
an owner: to leave, the owner hands it to someone first.

People join through **invite links** — `/join/<code>`, sent however you like,
valid for 7 days, revocable. Whoever opens one signs in, or creates an account
right there, and is in.
There is deliberately no list of everyone to pick from: at any size beyond a
group of friends, that list is itself a leak.

A project someone is not in behaves exactly like one that does not exist, in the
tools and in the web, so nobody learns which projects exist by probing.

## Removing and deleting

**Removing someone does not remove what they wrote.** Their entries are the
project's history and other people's decisions build on them. They lose access
immediately — including a page they have open, which is told to leave — and
their entries stay under their name.

**Deleting a project** puts it in the owner's trash, restorable, and leaves it
there. Nothing in this product deletes anything on a timer: a project nobody has
opened in months is still somebody's work, and a clock that quietly removes it is
a clock nobody remembers setting. The trash says how much disk each project is
still holding, and emptying one out is a button its owner presses. That purge is
the one place anything in a log is ever deleted — append-only means history is
not rewritten, not that nobody may delete their own project.

**Deleting an account** is refused while it owns projects other people are in —
those have to be handed over or deleted first, rather than silently orphaned.
Projects only that person was in go with them. Everywhere else they leave, and
what they wrote stays, attributed to an anonymous former member.

## History, and what is true now

The log answers *what happened*. An assistant sitting down to work needs a
different question answered: *what is true right now*. History grows forever;
the standing summary does not.

So `catch_up` leads with **the brief** — every entry not yet superseded, grouped
into decisions in force, what the team knows, and what is still open. It is
computed from the log on every read and never written. Each item collapses to
**one line**, because its job is to be an index that fits in every assistant's
context however long the project runs.

`supersedes` carries three meanings with one pointer: a decision reversed, a
question answered, a fact corrected. Anything superseded drops out of the brief.

## What an entry counts as

`derive_level` decides, not the writer. Declaration sets the category where one
was given — a fact answering a question stays a fact — and structure
**promotes** work that turned out to be more than work: a dropped option or a
superseded entry makes it a decision whether or not the model said so. Structure
never demotes. Within plain work an artifact outranks a dropped option, so
producing a file with a choice along the way stays out of the brief.

The point is that a model cannot inflate its own entry, and three different
models cannot drift apart on what a decision is, because none of them is judging.
The detail panel in the web says which field a level came from, and whether it
survives into the brief.

## The tools

- `catch_up(project)` — the brief, the document, the files, then everything since your cursor.
- `record(project, summary, details, kind, intent, rejected, refs, section, content, artifact, supersedes)`
- `read_file(project, file, part)` — the contents of one file, on request.
- `write_file(project, name, content, note)` — put a file in, or rewrite one.

Two tools was the rule, and the file pair is the exception that proves why: a
file cannot ride along in `catch_up` without costing every assistant the rest of
its context, so it has to be asked for — and once an assistant can read one, the
thing it will want next is to leave one behind.

`project` is required on both; an unknown or missing one is refused with the
list of projects the caller is in. Entry numbers count from #1 in each project,
and a `supersedes` or `refs` pointing into another project does not resolve.

`intent` and `rejected` are first-class parameters of the only write tool, and
their descriptions explicitly allow leaving them blank — which is what stops a
model inventing a reason nobody gave.

**Every response carries the delta.** You cannot push to an LLM, so the news
rides along with whatever the assistant was already doing.

## Files and repositories

Files are **uploaded and kept**; repositories are **linked and never copied**.

Upload anything into a project from the web and every assistant sees it listed
in `catch_up` — one line each, with whether it can be read at all. The content
waits for `read_file`, which hands it back in 8000-character parts. Text, code,
PDF and .docx arrive as text; images come back as images; anything else says so
rather than wasting a round trip. A file is listed by number (`F1`) or by name,
and an upload goes through the log like anything else, so the team sees it
arrive.

Assistants write files too, with `write_file`: a script, a draft, a
configuration, a set of notes — whatever has a shape of its own and would be
lost inside a conversation. What it sends replaces the file whole, which is
stated in the tool's own description, because writing from memory is how a
teammate's work disappears.

**Writing a name that already exists is a new version, not a second file.**
Whatever was there is kept: the old bytes stay on disk under their own hash and
the old text stays in `file_revision`, so replacing somebody's work is something
they can look at rather than something they discover by its absence. If you
overwrite a version a teammate wrote minutes ago, the answer says so.

A repository is stored as a link and a branch, nothing more. Every assistant
already has a GitHub connector of its own, and whatever it reads through that is
more current than any copy we could keep; what was missing was the whole team
knowing which repository the project is about.

Limits are 20 MB per file and 200 MB per project. Files are served from `/f/...`
to members only, never inline except images and PDFs, under a sandbox CSP — a
teammate's upload is not a place to run scripts from.

**A file's text is wrapped as untrusted data** before an assistant sees it, in
those words, because a document is exactly where an instruction aimed at someone
else's assistant would be hidden.

## Connecting an assistant

1. In the web: **Account → Connections → Create connection**, name it, copy the URL.
2. Add it to your assistant as a custom connector.
3. For each project, **Link a conversation** gives the instructions to paste into
   a Project in your assistant — see [LINKING.md](LINKING.md).

| | Where the URL goes |
|---|---|
| Claude | Settings → Connectors → Add custom connector |
| ChatGPT | Developer mode, then add it as a custom connector |
| Gemini | Gemini CLI settings (the consumer app's custom MCP is US-only), or `/api/*` |

> **`localhost` will not work.** Claude's connectors reach your server from
> Anthropic's cloud, not from your laptop. Always use the public HTTPS URL.

For anything that does not speak MCP, the same operations over plain HTTP:

```bash
curl -X POST https://<host>/u/<connection token>/api/record \
  -H 'content-type: application/json' \
  -d '{"project":"tfg","summary":"generated the cover","artifact":"cover.png"}'
```

## Not built yet

**OAuth for MCP**, so a connector URL carries no secret at all and the assistant
signs in through Google itself. The token-in-URL path stays either way, as the
one that works with every client.

**Guarding against prompt injection between people.** The product is, by
design, a channel for text one person's assistant wrote to enter another's
context. Among friends that is fine; once projects hold people you do not fully
trust, it is a real attack surface. File contents are already handed over
wrapped as data, and the web marks every entry as data — but the entries
themselves reach the assistants unwrapped.
