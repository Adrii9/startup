# Linking a conversation to a project

The server never sees a conversation, so linking cannot be something it
remembers. It is a piece of text, pasted once into an assistant's project
instructions, that says which project that assistant works in. The tools make
`project` a required argument, so the assistant carries it on every call.

## The two things each person sets up

**Once per assistant — the connection.** In the web, **Account → Connections →
Create connection**. You get a URL, `https://<host>/u/<token>/mcp`, shown once:
add it to your assistant as a custom connector. It says who you are, and it is
the same for every project you are in, so it never needs touching again — unless
it leaks, in which case you revoke it there and make another.

**Once per project — the instructions.** In the web, open the project and press
**Link a conversation**. It generates the text with the project already filled
in. Paste it into a Project in your assistant (in Claude: Projects → new → the
instructions field). Every conversation started inside that Project then works
in that project.

That is the whole mechanism. Nothing is wired between the web and your
assistant; the button writes the instructions for you.

## Why `project` is required rather than inferred

A missing or wrong project is the same failure as a wrong name: work lands where
nobody expects it and nothing says so. So the server never guesses. An unknown
or absent project is refused, and the error lists the projects the caller is in,
which makes recovery one step. Models obey a required schema field far more
reliably than a sentence in a prompt, which is why it is in the schema and not
only in the text.

## The text itself

It lives in `app/linking.py`, one version per language the web speaks. Edit it
there, not here: it is the most important prompt in the product, and the web
serves it from that file.

## ChatGPT and Gemini

The same text works. In ChatGPT it goes in a Project's instructions; in Gemini
CLI, in `GEMINI.md`. Both are wired up but neither has been used in anger yet.
