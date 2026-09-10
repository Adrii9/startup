"""The text that links a conversation to a project.

The server never sees a conversation, so "linking" cannot be something it
remembers. It is this text, pasted into an assistant's project instructions:
it tells the assistant which project it is in, and the required `project`
argument carries that on every call. The web generates it; nothing is wired.

One template per language the web speaks. Assistants follow any of them.
"""

from __future__ import annotations

TEMPLATES = {
    "ca": """Aquesta conversa forma part del projecte compartit «{title}». Altres persones hi treballen, cadascuna amb el seu propi assistent.

A TOTES les crides a `catch_up` i `record`, passa project="{slug}". És el que diu a quin projecte pertany aquesta conversa; sense això la crida falla.

Abans de fer res, crida `catch_up`. Et torna on som ara mateix: les decisions vigents, què sabem, què queda obert, el document, i el que han fet els companys des de l'última vegada. No et refiïs del que recordes d'abans en aquesta conversa: els altres canvien coses mentre parlem.

Crida `record` quan alguna cosa quedi decidida, produïda o descartada. Al final de qualsevol intercanvi on hàgim tret alguna cosa en clar, registra-ho: peca de registrar de més, que una entrada prima es pot millorar i una decisió perduda no. El que no cal registrar és el pas a pas: una entrada per cosa establerta, no una per torn.

Escriu cada entrada per a l'assistent d'un company que no pot veure aquesta conversa. `summary` és el titular. A `details` hi va el que un company necessita per actuar — el raonament, els números, el que s'ha acordat — no la resposta sencera ni tota la recerca.

Si no t'he dit per què volia una cosa, deixa `intent` buit. No te l'inventis: una raó inventada arriba als meus companys com un fet.

Quan una entrada revoqui una decisió, respongui una pregunta o corregeixi un fet, passa `supersedes` amb el seu número. Quan es recolzi en entrades anteriors, passa-les a `refs`.""",
    "es": """Esta conversación forma parte del proyecto compartido «{title}». Otras personas trabajan en él, cada una con su propio asistente.

En TODAS las llamadas a `catch_up` y `record`, pasa project="{slug}". Es lo que dice a qué proyecto pertenece esta conversación; sin eso la llamada falla.

Antes de hacer nada, llama a `catch_up`. Te devuelve dónde estamos: las decisiones vigentes, lo que sabemos, lo que queda abierto, el documento, y lo que han hecho los compañeros desde la última vez. No te fíes de lo que recuerdes de antes en esta conversación: los demás cambian cosas mientras hablamos.

Llama a `record` cuando algo quede decidido, producido o descartado. Al final de cualquier intercambio en el que hayamos sacado algo en claro, regístralo: mejor registrar de más, que una entrada escueta se puede mejorar y una decisión perdida no. Lo que no hace falta registrar es el paso a paso: una entrada por cosa establecida, no una por turno.

Escribe cada entrada para el asistente de un compañero que no puede ver esta conversación. `summary` es el titular. En `details` va lo que un compañero necesita para actuar — el razonamiento, las cifras, lo acordado — no la respuesta entera ni toda la investigación.

Si no te he dicho por qué quería algo, deja `intent` vacío. No te lo inventes: una razón inventada les llega a mis compañeros como un hecho.

Cuando una entrada revoque una decisión, responda una pregunta o corrija un hecho, pasa `supersedes` con su número. Cuando se apoye en entradas anteriores, pásalas en `refs`.""",
    "en": """This conversation is part of the shared project "{title}". Other people work in it, each with their own assistant.

On EVERY call to `catch_up` and `record`, pass project="{slug}". That is what says which project this conversation belongs to; without it the call fails.

Before doing anything, call `catch_up`. It returns where things stand: the decisions in force, what we know, what is still open, the document, and what teammates have done since you last looked. Do not rely on what you remember from earlier in this conversation: other people change things while we talk.

Call `record` when something is decided, produced or dropped. At the end of any exchange where we worked something out, record it: err on the side of recording, since a thin entry can be improved and a lost decision cannot. What does not need recording is the step-by-step: one entry per thing established, not one per turn.

Write every entry for a teammate's assistant that cannot see this conversation. `summary` is the headline. `details` holds what a teammate needs in order to act — the reasoning, the numbers, what was agreed — not the whole answer or all the research.

If I did not say why I wanted something, leave `intent` empty. Do not invent one: a made-up reason reaches my teammates as fact.

When an entry reverses a decision, answers a question or corrects a fact, pass `supersedes` with its number. When it builds on earlier entries, pass them in `refs`.""",
}


def instructions(slug: str, title: str, lang: str = "ca") -> str:
    return TEMPLATES.get(lang, TEMPLATES["en"]).format(slug=slug, title=title)
