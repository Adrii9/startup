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
    loading: 'Carregant…', former_member: 'antic membre',

    login_title: 'Entra al teu espai',
    login_help: 'Els projectes on treballes amb la teva gent, i les seves IAs, en un sol lloc.',
    google_btn: 'Entra amb Google',
    google_missing: "L'entrada amb Google encara no està configurada en aquest servidor.",
    dev_label: 'Accés de desenvolupament', dev_go: 'Entrar',

    join_title: '{i} et convida a «{t}»',
    join_help: 'Hi entraràs com a membre. Podràs llegir-ho tot i el teu assistent hi podrà escriure.',
    join_btn: 'Uneix-te al projecte', join_google: 'Entra amb Google per unir-te',
    join_open: 'Obrir el projecte', join_already: 'Ja ets en aquest projecte.',
    join_invalid: 'Aquesta invitació ja no val',
    join_invalid_help: "Ha caducat, s'ha fet servir massa vegades o l'han revocat. Demana'n una de nova.",

    search: 'Busca projectes', no_results: 'Cap projecte coincideix.',
    new_project: 'Projecte nou', collapse: 'Amaga els projectes', expand: 'Mostra els projectes',
    no_projects: 'Encara no ets a cap projecte.', empty_new: 'Crea el primer',
    acc_connections: 'Connexions', acc_trash: 'Paperera', acc_delete: 'Esborrar el compte',
    logout: 'Sortir',

    np_title: 'Projecte nou', np_name: 'Nom', np_colour: 'Color', np_create: 'Crear',
    np_need_name: 'Posa-li un nom.',
    np_after: 'Hi convidaràs gent des dels ajustos del projecte.',
    t_created: '«{n}» creat.',

    cancel: 'Cancel·lar', copy: 'Copiar', copied: 'Copiat.', close: 'Tancar', save: 'Desar',

    link: 'Linkar una conversa', link_title: 'Linkar una conversa a «{t}»',
    link_steps: "1. A la teva IA, crea un Projecte (a Claude: Projectes → nou).\n2. Assegura't que hi tens activada una connexió de shared-context (Compte → Connexions).\n3. Enganxa aquest text a les instruccions del Projecte.\n\nA partir d'aquí, cada conversa dins d'aquell Projecte treballa en «{t}».",
    link_note: 'La connexió la configures un sol cop per assistent. Aquest text és l\'únic que canvia d\'un projecte a l\'altre.',
    who_here: 'Qui hi ha', never_wrote: 'encara no ha escrit',

    con_title: 'Connexions',
    con_help: "Cada assistent que connectes té la seva pròpia URL. Si una se t'escapa en una captura o deixes de fer-la servir, la revoques i les altres segueixen funcionant.",
    con_label: 'Per a quin assistent?', con_placeholder: 'Claude, ChatGPT del portàtil…',
    con_create: 'Crear connexió', con_empty: 'Encara no tens cap connexió.',
    con_new_title: 'La teva nova connexió',
    con_new_warning: "Copia-la ara i enganxa-la a la configuració de connectors del teu assistent. No es tornarà a mostrar: no la guardem enlloc.",
    con_created: 'creada {d}', con_used: 'usada {d}', con_never: 'mai usada',
    con_revoke: 'Revocar', con_revoke_q: 'Revocar «{n}»?',
    con_revoke_body: "L'assistent que la fa servir deixarà de poder llegir i escriure als teus projectes a l'instant.",
    t_revoked: 'Connexió revocada.',

    ps_title: 'Ajustos de «{t}»', ps_name: 'Nom', ps_colour: 'Color', ps_saved: 'Desat.',
    ps_members: 'Membres', ps_owner: 'propietari', ps_member: 'membre', ps_you: '(tu)',
    ps_remove: 'Treure', ps_make_owner: 'Fer propietari',
    ps_invites: 'Invitacions',
    ps_invite_help: "Genera un enllaç i envia'l per on vulguis. Qui l'obri entrarà amb Google i serà dins. Caduca en 7 dies.",
    ps_invite_create: "Crear un enllaç d'invitació", ps_no_invites: 'Cap enllaç actiu.',
    ps_invite_meta: 'caduca {d} · {u} usos', ps_invite_revoke: 'Revocar',
    ps_danger: 'Zona perillosa',
    ps_delete: 'Esborrar el projecte', ps_delete_q: 'Esborrar «{t}»?',
    ps_delete_body: 'Anirà a la paperera. El podràs recuperar durant 30 dies; després desapareixerà per sempre, amb tot el que conté.',
    ps_leave: 'Marxar del projecte', ps_leave_q: 'Marxar de «{t}»?',
    ps_leave_body: "Deixaràs de veure'l i el teu assistent hi perdrà l'accés. El que hi has escrit es queda.",
    ps_remove_q: 'Treure {n}?',
    ps_remove_body: "Perdrà l'accés a l'instant. Les entrades que ha escrit es queden, perquè són la història del projecte.",
    ps_owner_q: 'Fer {n} propietari?',
    ps_owner_body: 'Passarà a gestionar el projecte, i tu en seràs un membre més.',
    ps_member_only: "Només el propietari pot convidar gent, treure'n o esborrar el projecte.",
    t_deleted: '«{n}» és a la paperera.', t_left: 'Has marxat de «{n}».',

    tr_title: 'Paperera', tr_help: 'Els projectes que has esborrat. Passats 30 dies, desapareixen per sempre.',
    tr_empty: 'La paperera és buida.', tr_restore: 'Recuperar', tr_purge_on: "s'esborrarà {d}",
    t_restored: '«{n}» recuperat.',

    da_title: 'Esborrar el compte',
    da_body: "Es tancarà la teva sessió i es revocaran totes les teves connexions. Els projectes on només hi ets tu s'esborraran. On hi ha més gent, el que has escrit es quedarà com a d'un «antic membre».\n\nNo es pot desfer.",
    da_confirm: 'Esborrar-lo per sempre',

    t_gone: 'Ja no tens accés a aquest projecte.', t_signed_out: 'Has sortit.'
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
    loading: 'Cargando…', former_member: 'antiguo miembro',

    login_title: 'Entra en tu espacio',
    login_help: 'Los proyectos en los que trabajas con tu gente, y con sus IAs, en un solo sitio.',
    google_btn: 'Entrar con Google',
    google_missing: 'El acceso con Google todavía no está configurado en este servidor.',
    dev_label: 'Acceso de desarrollo', dev_go: 'Entrar',

    join_title: '{i} te invita a «{t}»',
    join_help: 'Entrarás como miembro. Podrás leerlo todo y tu asistente podrá escribir.',
    join_btn: 'Unirse al proyecto', join_google: 'Entra con Google para unirte',
    join_open: 'Abrir el proyecto', join_already: 'Ya estás en este proyecto.',
    join_invalid: 'Esta invitación ya no vale',
    join_invalid_help: 'Ha caducado, se ha usado demasiadas veces o la han revocado. Pide una nueva.',

    search: 'Busca proyectos', no_results: 'Ningún proyecto coincide.',
    new_project: 'Proyecto nuevo', collapse: 'Esconder los proyectos', expand: 'Mostrar los proyectos',
    no_projects: 'Todavía no estás en ningún proyecto.', empty_new: 'Crea el primero',
    acc_connections: 'Conexiones', acc_trash: 'Papelera', acc_delete: 'Borrar la cuenta',
    logout: 'Salir',

    np_title: 'Proyecto nuevo', np_name: 'Nombre', np_colour: 'Color', np_create: 'Crear',
    np_need_name: 'Ponle un nombre.',
    np_after: 'Invitarás a gente desde los ajustes del proyecto.',
    t_created: '«{n}» creado.',

    cancel: 'Cancelar', copy: 'Copiar', copied: 'Copiado.', close: 'Cerrar', save: 'Guardar',

    link: 'Enlazar una conversación', link_title: 'Enlazar una conversación a «{t}»',
    link_steps: '1. En tu IA, crea un Proyecto (en Claude: Proyectos → nuevo).\n2. Asegúrate de tener activada una conexión de shared-context (Cuenta → Conexiones).\n3. Pega este texto en las instrucciones del Proyecto.\n\nA partir de ahí, cada conversación dentro de ese Proyecto trabaja en «{t}».',
    link_note: 'La conexión la configuras una sola vez por asistente. Este texto es lo único que cambia de un proyecto a otro.',
    who_here: 'Quién está', never_wrote: 'aún no ha escrito',

    con_title: 'Conexiones',
    con_help: 'Cada asistente que conectas tiene su propia URL. Si una se te escapa en una captura o dejas de usarla, la revocas y las demás siguen funcionando.',
    con_label: '¿Para qué asistente?', con_placeholder: 'Claude, ChatGPT del portátil…',
    con_create: 'Crear conexión', con_empty: 'Todavía no tienes ninguna conexión.',
    con_new_title: 'Tu nueva conexión',
    con_new_warning: 'Cópiala ahora y pégala en la configuración de conectores de tu asistente. No se volverá a mostrar: no la guardamos en ningún sitio.',
    con_created: 'creada {d}', con_used: 'usada {d}', con_never: 'nunca usada',
    con_revoke: 'Revocar', con_revoke_q: '¿Revocar «{n}»?',
    con_revoke_body: 'El asistente que la usa dejará de poder leer y escribir en tus proyectos al instante.',
    t_revoked: 'Conexión revocada.',

    ps_title: 'Ajustes de «{t}»', ps_name: 'Nombre', ps_colour: 'Color', ps_saved: 'Guardado.',
    ps_members: 'Miembros', ps_owner: 'propietario', ps_member: 'miembro', ps_you: '(tú)',
    ps_remove: 'Quitar', ps_make_owner: 'Hacer propietario',
    ps_invites: 'Invitaciones',
    ps_invite_help: 'Genera un enlace y envíalo por donde quieras. Quien lo abra entrará con Google y estará dentro. Caduca en 7 días.',
    ps_invite_create: 'Crear un enlace de invitación', ps_no_invites: 'Ningún enlace activo.',
    ps_invite_meta: 'caduca {d} · {u} usos', ps_invite_revoke: 'Revocar',
    ps_danger: 'Zona peligrosa',
    ps_delete: 'Borrar el proyecto', ps_delete_q: '¿Borrar «{t}»?',
    ps_delete_body: 'Irá a la papelera. Podrás recuperarlo durante 30 días; después desaparecerá para siempre, con todo lo que contiene.',
    ps_leave: 'Salir del proyecto', ps_leave_q: '¿Salir de «{t}»?',
    ps_leave_body: 'Dejarás de verlo y tu asistente perderá el acceso. Lo que has escrito se queda.',
    ps_remove_q: '¿Quitar a {n}?',
    ps_remove_body: 'Perderá el acceso al instante. Las entradas que ha escrito se quedan, porque son la historia del proyecto.',
    ps_owner_q: '¿Hacer propietario a {n}?',
    ps_owner_body: 'Pasará a gestionar el proyecto, y tú serás un miembro más.',
    ps_member_only: 'Solo el propietario puede invitar gente, quitarla o borrar el proyecto.',
    t_deleted: '«{n}» está en la papelera.', t_left: 'Has salido de «{n}».',

    tr_title: 'Papelera', tr_help: 'Los proyectos que has borrado. Pasados 30 días, desaparecen para siempre.',
    tr_empty: 'La papelera está vacía.', tr_restore: 'Recuperar', tr_purge_on: 'se borrará {d}',
    t_restored: '«{n}» recuperado.',

    da_title: 'Borrar la cuenta',
    da_body: 'Se cerrará tu sesión y se revocarán todas tus conexiones. Los proyectos en los que solo estás tú se borrarán. Donde hay más gente, lo que has escrito quedará como de un «antiguo miembro».\n\nNo se puede deshacer.',
    da_confirm: 'Borrarla para siempre',

    t_gone: 'Ya no tienes acceso a este proyecto.', t_signed_out: 'Has salido.'
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
    loading: 'Loading…', former_member: 'former member',

    login_title: 'Sign in to your workspace',
    login_help: 'The projects you work on with your people, and their assistants, in one place.',
    google_btn: 'Sign in with Google',
    google_missing: 'Google sign-in is not configured on this server yet.',
    dev_label: 'Development sign-in', dev_go: 'Sign in',

    join_title: '{i} invites you to “{t}”',
    join_help: 'You will join as a member. You can read everything, and your assistant can write.',
    join_btn: 'Join the project', join_google: 'Sign in with Google to join',
    join_open: 'Open the project', join_already: 'You are already in this project.',
    join_invalid: 'This invite no longer works',
    join_invalid_help: 'It has expired, been used up, or been revoked. Ask for a new one.',

    search: 'Search projects', no_results: 'No project matches that.',
    new_project: 'New project', collapse: 'Hide the projects', expand: 'Show the projects',
    no_projects: 'You are not in any project yet.', empty_new: 'Create the first one',
    acc_connections: 'Connections', acc_trash: 'Trash', acc_delete: 'Delete account',
    logout: 'Sign out',

    np_title: 'New project', np_name: 'Name', np_colour: 'Colour', np_create: 'Create',
    np_need_name: 'Give it a name.',
    np_after: 'You invite people from the project’s settings.',
    t_created: '“{n}” created.',

    cancel: 'Cancel', copy: 'Copy', copied: 'Copied.', close: 'Close', save: 'Save',

    link: 'Link a conversation', link_title: 'Link a conversation to “{t}”',
    link_steps: '1. In your assistant, create a Project (in Claude: Projects → new).\n2. Make sure a shared-context connection is enabled there (Account → Connections).\n3. Paste this text into the Project\'s instructions.\n\nFrom then on, every conversation inside that Project works in “{t}”.',
    link_note: 'You set the connection up once per assistant. This text is the only thing that changes from one project to the next.',
    who_here: 'Who is here', never_wrote: 'has not written yet',

    con_title: 'Connections',
    con_help: 'Every assistant you connect gets its own URL. If one ends up in a screenshot or you stop using it, revoke it and the others keep working.',
    con_label: 'Which assistant is it for?', con_placeholder: 'Claude, ChatGPT on the laptop…',
    con_create: 'Create connection', con_empty: 'You have no connections yet.',
    con_new_title: 'Your new connection',
    con_new_warning: 'Copy it now and paste it into your assistant’s connector settings. It will not be shown again: it is not stored anywhere.',
    con_created: 'created {d}', con_used: 'used {d}', con_never: 'never used',
    con_revoke: 'Revoke', con_revoke_q: 'Revoke “{n}”?',
    con_revoke_body: 'The assistant using it will stop being able to read and write your projects immediately.',
    t_revoked: 'Connection revoked.',

    ps_title: '“{t}” settings', ps_name: 'Name', ps_colour: 'Colour', ps_saved: 'Saved.',
    ps_members: 'Members', ps_owner: 'owner', ps_member: 'member', ps_you: '(you)',
    ps_remove: 'Remove', ps_make_owner: 'Make owner',
    ps_invites: 'Invites',
    ps_invite_help: 'Make a link and send it however you like. Whoever opens it signs in with Google and is in. It expires in 7 days.',
    ps_invite_create: 'Create an invite link', ps_no_invites: 'No active links.',
    ps_invite_meta: 'expires {d} · {u} uses', ps_invite_revoke: 'Revoke',
    ps_danger: 'Danger zone',
    ps_delete: 'Delete the project', ps_delete_q: 'Delete “{t}”?',
    ps_delete_body: 'It goes to the trash. You can restore it for 30 days; after that it is gone for good, with everything in it.',
    ps_leave: 'Leave the project', ps_leave_q: 'Leave “{t}”?',
    ps_leave_body: 'You will stop seeing it and your assistant will lose access. What you wrote stays.',
    ps_remove_q: 'Remove {n}?',
    ps_remove_body: 'They lose access immediately. The entries they wrote stay, because they are the project’s history.',
    ps_owner_q: 'Make {n} the owner?',
    ps_owner_body: 'They will manage the project, and you will be a member like everyone else.',
    ps_member_only: 'Only the owner can invite people, remove them, or delete the project.',
    t_deleted: '“{n}” is in the trash.', t_left: 'You left “{n}”.',

    tr_title: 'Trash', tr_help: 'Projects you deleted. After 30 days they are gone for good.',
    tr_empty: 'The trash is empty.', tr_restore: 'Restore', tr_purge_on: 'deleted for good {d}',
    t_restored: '“{n}” restored.',

    da_title: 'Delete account',
    da_body: 'Your session ends and every connection you made is revoked. Projects with only you in them are deleted. Where other people are, what you wrote stays, attributed to a “former member”.\n\nThis cannot be undone.',
    da_confirm: 'Delete it for good',

    t_gone: 'You no longer have access to this project.', t_signed_out: 'Signed out.'
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
