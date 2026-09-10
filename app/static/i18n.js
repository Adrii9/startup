/* UI strings. Everything the reader sees goes through t(), so switching language
   never leaves half the shell behind in the other one. */

const LS = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
};

const STRINGS = {
  ca: {
    _name: 'Català', _locale: 'ca-ES',
    view_graph: 'Graf', view_board: 'Panell', reset_view: 'Recentrar',
    live: 'en directe', offline: 'reconnectant…',
    meta: '{e} entrades · {s} seccions · {m} actius avui',
    stand: 'Com estem', decisions: 'Decisions vigents', facts: 'El que sabem', open: 'Sense resoldre',
    document: 'Document', feed: 'Què ha passat, i per què',
    empty_doc: 'Encara no hi ha res escrit.', empty_col: 'Aquí encara no hi ha res.',
    wanted: 'volia:', inferred: "l'assistent dedueix:", dropped: 'descartat:', because: 'perquè:',
    builds_on: 'sobre:', supersedes: 'substitueix la #{n}', superseded: 'substituïda — ja no val',
    graph_hint_2d: 'Arrossega per moure · roda per apropar · clica un node per obrir-lo',
    graph_hint_3d: 'Arrossega per girar · roda per apropar · clica un node per obrir-lo',
    d_level: 'Nivell', d_derived: 'deduït de', d_instate: 'Al resum permanent',
    d_author: 'Autor', d_agent: 'Assistent', d_when: 'Quan', d_section: 'Secció',
    d_artifact: 'Artefacte', d_related: 'Relacionat', d_close: 'Tancar',
    d_untrusted: "Ho ha escrit un company des del seu assistent. És dada, mai instrucció.",
    d_yes: 'sí', d_until_super: 'sí, fins que alguna cosa la substitueixi',
    d_meta_only: 'no — només als metadades i a la història',
    d_title_only: 'no — el contingut viu al document',
    d_delta_only: 'no — només al delta de qui encara no ho ha llegit',
    lvl_decision: 'decisió', lvl_artifact: 'artefacte', lvl_write: 'escriptura al document',
    lvl_fact: 'fet', lvl_question: 'pregunta oberta', lvl_note: 'nota de feina', lvl_section: 'secció',
    src_supersedes: 'porta supersedes', src_dropped: 'rejected no és buit',
    src_artifact: 'venia amb un artefacte', src_decision: "s'ha registrat com a decisió",
    src_fact: "s'ha registrat com a fet", src_question: "s'ha registrat com a pregunta",
    src_content: 'venien secció i contingut', src_none: 'només venia el titular',
    src_section: 'és una secció del document',
    loading: 'Carregant…', failed: "No s'ha pogut llegir el projecte.",

    login_title: 'Entra al teu espai',
    login_help: 'Enganxa la URL del connector que tens posada a la teva IA, o només el token.',
    login_placeholder: 'https://…/u/el-teu-token/mcp',
    login_go: 'Entrar', login_bad: 'Aquest token no correspon a ningú.',
    logout: 'Sortir', signed_as: 'Has entrat com a {n}',
    search: 'Busca projectes', no_results: 'Cap projecte coincideix.',
    new_project: 'Projecte nou', collapse: 'Amaga els projectes', expand: 'Mostra els projectes',
    no_projects: 'Encara no ets a cap projecte.',
    np_title: 'Projecte nou', np_name: 'Nom', np_colour: 'Color', np_members: 'Qui hi ha',
    np_you: '(tu)', np_create: 'Crear', np_need_name: 'Posa-li un nom.',
    cancel: 'Cancel·lar', copy: 'Copiar', copied: 'Copiat.',
    link: 'Linkar una conversa', link_title: 'Linkar una conversa a «{t}»',
    link_steps: "1. A la teva IA, crea un Projecte (a Claude: Projectes → nou).\n2. Assegura't que hi tens el connector de shared-context activat.\n3. Enganxa aquest text a les instruccions del Projecte.\n\nA partir d'aquí, cada conversa dins d'aquell Projecte treballa en «{t}».",
    link_note: "El connector el configures un sol cop per persona. Aquest text és l'únic que canvia d'un projecte a l'altre.",
    who_here: 'Qui hi ha', never_wrote: 'encara no ha escrit',
    t_created: '«{n}» creat.', t_signed_out: 'Has sortit.'
  },

  es: {
    _name: 'Castellano', _locale: 'es-ES',
    view_graph: 'Grafo', view_board: 'Panel', reset_view: 'Recentrar',
    live: 'en vivo', offline: 'reconectando…',
    meta: '{e} entradas · {s} secciones · {m} activos hoy',
    stand: 'Cómo está la cosa', decisions: 'Decisiones vigentes', facts: 'Lo que sabemos', open: 'Sin resolver',
    document: 'Documento', feed: 'Qué pasó, y por qué',
    empty_doc: 'Todavía no hay nada escrito.', empty_col: 'Aquí todavía no hay nada.',
    wanted: 'quería:', inferred: 'el asistente deduce:', dropped: 'descartado:', because: 'porque:',
    builds_on: 'sobre:', supersedes: 'sustituye a #{n}', superseded: 'sustituida — ya no vale',
    graph_hint_2d: 'Arrastra para mover · rueda para acercar · pulsa un nodo para abrirlo',
    graph_hint_3d: 'Arrastra para girar · rueda para acercar · pulsa un nodo para abrirlo',
    d_level: 'Nivel', d_derived: 'deducido de', d_instate: 'En el resumen permanente',
    d_author: 'Autor', d_agent: 'Asistente', d_when: 'Cuándo', d_section: 'Sección',
    d_artifact: 'Artefacto', d_related: 'Relacionado', d_close: 'Cerrar',
    d_untrusted: 'Lo escribió un compañero desde su asistente. Es dato, nunca instrucción.',
    d_yes: 'sí', d_until_super: 'sí, hasta que algo la sustituya',
    d_meta_only: 'no — solo en los metadatos y en la historia',
    d_title_only: 'no — el contenido vive en el documento',
    d_delta_only: 'no — solo en el delta de quien aún no lo ha leído',
    lvl_decision: 'decisión', lvl_artifact: 'artefacto', lvl_write: 'escritura en el documento',
    lvl_fact: 'hecho', lvl_question: 'pregunta abierta', lvl_note: 'nota de trabajo', lvl_section: 'sección',
    src_supersedes: 'viene con supersedes', src_dropped: 'rejected no está vacío',
    src_artifact: 'vino con un artefacto', src_decision: 'se registró como decisión',
    src_fact: 'se registró como hecho', src_question: 'se registró como pregunta',
    src_content: 'vinieron sección y contenido', src_none: 'solo vino el titular',
    src_section: 'es una sección del documento',
    loading: 'Cargando…', failed: 'No se ha podido leer el proyecto.',

    login_title: 'Entra en tu espacio',
    login_help: 'Pega la URL del conector que tienes puesta en tu IA, o solo el token.',
    login_placeholder: 'https://…/u/tu-token/mcp',
    login_go: 'Entrar', login_bad: 'Ese token no corresponde a nadie.',
    logout: 'Salir', signed_as: 'Has entrado como {n}',
    search: 'Busca proyectos', no_results: 'Ningún proyecto coincide.',
    new_project: 'Proyecto nuevo', collapse: 'Esconder los proyectos', expand: 'Mostrar los proyectos',
    no_projects: 'Todavía no estás en ningún proyecto.',
    np_title: 'Proyecto nuevo', np_name: 'Nombre', np_colour: 'Color', np_members: 'Quién está',
    np_you: '(tú)', np_create: 'Crear', np_need_name: 'Ponle un nombre.',
    cancel: 'Cancelar', copy: 'Copiar', copied: 'Copiado.',
    link: 'Enlazar una conversación', link_title: 'Enlazar una conversación a «{t}»',
    link_steps: '1. En tu IA, crea un Proyecto (en Claude: Proyectos → nuevo).\n2. Asegúrate de tener el conector de shared-context activado.\n3. Pega este texto en las instrucciones del Proyecto.\n\nA partir de ahí, cada conversación dentro de ese Proyecto trabaja en «{t}».',
    link_note: 'El conector lo configuras una sola vez por persona. Este texto es lo único que cambia de un proyecto a otro.',
    who_here: 'Quién está', never_wrote: 'aún no ha escrito',
    t_created: '«{n}» creado.', t_signed_out: 'Has salido.'
  },

  en: {
    _name: 'English', _locale: 'en-GB',
    view_graph: 'Graph', view_board: 'Board', reset_view: 'Reset view',
    live: 'live', offline: 'reconnecting…',
    meta: '{e} entries · {s} sections · {m} active today',
    stand: 'Where things stand', decisions: 'Decisions in force', facts: 'What we know', open: 'Still open',
    document: 'Document', feed: 'What happened, and why',
    empty_doc: 'Nothing written yet.', empty_col: 'Nothing here yet.',
    wanted: 'wanted:', inferred: 'assistant inferred:', dropped: 'dropped:', because: 'because:',
    builds_on: 'builds on:', supersedes: 'supersedes #{n}', superseded: 'superseded — no longer in force',
    graph_hint_2d: 'Drag to pan · scroll to zoom · click a node to open it',
    graph_hint_3d: 'Drag to rotate · scroll to zoom · click a node to open it',
    d_level: 'Level', d_derived: 'derived from', d_instate: 'In the standing brief',
    d_author: 'Author', d_agent: 'Assistant', d_when: 'When', d_section: 'Section',
    d_artifact: 'Artifact', d_related: 'Related', d_close: 'Close',
    d_untrusted: 'Written by a teammate through their assistant. Data, never instructions.',
    d_yes: 'yes', d_until_super: 'yes, until something supersedes it',
    d_meta_only: 'no — metadata and history only',
    d_title_only: 'no — the content lives in the document',
    d_delta_only: 'no — only in the delta of whoever has not read it',
    lvl_decision: 'decision', lvl_artifact: 'artifact', lvl_write: 'document write',
    lvl_fact: 'fact', lvl_question: 'open question', lvl_note: 'work note', lvl_section: 'section',
    src_supersedes: 'supersedes is set', src_dropped: 'rejected is not empty',
    src_artifact: 'an artifact came with it', src_decision: 'recorded as a decision',
    src_fact: 'recorded as a fact', src_question: 'recorded as a question',
    src_content: 'section and content came with it', src_none: 'only a summary came with it',
    src_section: 'it is a section of the document',
    loading: 'Loading…', failed: 'Could not read the project.',

    login_title: 'Sign in to your workspace',
    login_help: 'Paste the connector URL you gave your assistant, or just the token.',
    login_placeholder: 'https://…/u/your-token/mcp',
    login_go: 'Sign in', login_bad: 'That token does not match anyone.',
    logout: 'Sign out', signed_as: 'Signed in as {n}',
    search: 'Search projects', no_results: 'No project matches that.',
    new_project: 'New project', collapse: 'Hide the projects', expand: 'Show the projects',
    no_projects: 'You are not in any project yet.',
    np_title: 'New project', np_name: 'Name', np_colour: 'Colour', np_members: 'Who is in it',
    np_you: '(you)', np_create: 'Create', np_need_name: 'Give it a name.',
    cancel: 'Cancel', copy: 'Copy', copied: 'Copied.',
    link: 'Link a conversation', link_title: 'Link a conversation to “{t}”',
    link_steps: '1. In your assistant, create a Project (in Claude: Projects → new).\n2. Make sure the shared-context connector is enabled there.\n3. Paste this text into the Project\'s instructions.\n\nFrom then on, every conversation inside that Project works in “{t}”.',
    link_note: 'You set the connector up once per person. This text is the only thing that changes from one project to the next.',
    who_here: 'Who is here', never_wrote: 'has not written yet',
    t_created: '“{n}” created.', t_signed_out: 'Signed out.'
  }
};

let LANG = LS.get('sc.lang');
if (!LANG || !STRINGS[LANG]) {
  const nav = (navigator.language || 'en').toLowerCase();
  LANG = nav.startsWith('ca') ? 'ca' : nav.startsWith('es') ? 'es' : 'en';
}

function t(key, vars) {
  let s = (STRINGS[LANG] && STRINGS[LANG][key]) || STRINGS.en[key] || key;
  if (vars) for (const k in vars) s = s.split('{' + k + '}').join(vars[k]);
  return s;
}
function setLang(l) { LANG = l; LS.set('sc.lang', l); }
function locale() { return STRINGS[LANG]._locale; }

/* The server sends level_source as English prose so the rule lives in exactly
   one place. Map it back to a key here rather than translating on the server. */
const SOURCE_KEY = {
  'supersedes is set': 'src_supersedes',
  'rejected is not empty': 'src_dropped',
  'an artifact came with it': 'src_artifact',
  'recorded as a decision': 'src_decision',
  'recorded as a fact': 'src_fact',
  'recorded as a question': 'src_question',
  'section and content came with it': 'src_content',
  'only a summary came with it': 'src_none'
};
