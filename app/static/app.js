/* Wiring. The server owns every project; this only draws them.

   Flow: /api/me says who is signed in and which projects they are in. Picking a
   project loads its snapshot from /api/state and follows /events for changes.
   Nothing is kept here that the server does not also have. */

const $ = s => document.querySelector(s);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
};

const LEVEL_COLOUR = {
  decision: '#a78bfa', artifact: '#22d3ee', fact: '#60a5fa',
  question: '#fbbf24', write: '#34d399', note: '#6b7280', section: '#d4d4d8'
};
const LEVEL_RADIUS = {
  decision: 9, artifact: 8, section: 10, write: 7, fact: 7, question: 7.5, note: 5
};
/* Whether a level survives into the standing brief. Kept in step with IN_BRIEF
   in store.py -- if these two ever disagree, the panel lies about the rules. */
const IN_STATE = {
  decision: 'd_until_super', fact: 'd_yes', question: 'd_yes',
  artifact: 'd_meta_only', write: 'd_title_only', note: 'd_delta_only', section: 'd_yes'
};

const S = {
  me: null, slug: null, state: null, open: null, query: '',
  view: LS.get('sc.view') || 'board',
  dim: +(LS.get('sc.dim') || 2),
  collapsed: LS.get('sc.collapsed') === '1'
};
let graph, source, meTimer;

const initials = n => n.trim().split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
const fmt = iso => {
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso.replace(' ', 'T') + 'Z');
  return new Intl.DateTimeFormat(locale(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(d);
};
const entry = id => S.state.events.find(e => e.id === id);
const project = () => S.me && S.me.projects.find(p => p.slug === S.slug);

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: opts.body ? { 'content-type': 'application/json' } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined
  });
  if (r.status === 401) { showLogin(); throw new Error('signed out'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || r.statusText), { status: r.status });
  return data;
}

let toastT;
function toast(msg) {
  const n = $('#toast');
  n.textContent = msg; n.hidden = false;
  clearTimeout(toastT);
  toastT = setTimeout(() => { n.hidden = true; }, 2600);
}

/* ── sign in / out ────────────────────────────────────────────────── */
function showLogin() {
  if (source) { source.close(); source = null; }
  clearInterval(meTimer);
  S.me = null; S.state = null;
  $('#app').hidden = true;
  $('#login').hidden = false;
  $('#loginErr').hidden = true;
  $('#loginToken').value = '';
  setTimeout(() => $('#loginToken').focus(), 0);
}

async function signIn(ev) {
  ev.preventDefault();
  const token = $('#loginToken').value.trim();
  if (!token) return;
  const r = await fetch('/api/login', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ token })
  });
  if (!r.ok) { $('#loginErr').textContent = t('login_bad'); $('#loginErr').hidden = false; return; }
  boot();
}

async function signOut() {
  await fetch('/api/logout', { method: 'POST' });
  showLogin();
  toast(t('t_signed_out'));
}

/* ── projects rail ────────────────────────────────────────────────── */
function renderSidebar() {
  const list = $('#ctxList');
  list.textContent = '';
  const q = S.query.trim().toLowerCase();
  const shown = S.me.projects.filter(p => !q || p.title.toLowerCase().includes(q));
  $('#noResults').hidden = shown.length > 0 || !q;
  if (!S.me.projects.length) list.append(el('p', 'no-results', t('no_projects')));

  for (const p of shown) {
    const row = el('button', 'ctx' + (p.slug === S.slug ? ' on' : ''));
    row.style.setProperty('--c', p.colour);
    row.title = p.title;
    row.append(el('div', 'bead', initials(p.title)));
    const txt = el('div', 'ctx-text');
    txt.append(el('span', 'ctx-name', p.title), el('span', 'ctx-sub', p.members.join(' · ')));
    row.append(txt);
    if (p.unread && p.slug !== S.slug) row.append(el('span', 'badge', String(p.unread)));
    row.addEventListener('click', () => selectProject(p.slug));
    list.append(row);
  }
  $('#whoami').textContent = t('signed_as', { n: S.me.name });
}

async function refreshMe() {
  try {
    S.me = await api('/api/me');
    renderSidebar();
  } catch (e) { /* signed out is handled inside api() */ }
}

function selectProject(slug) {
  S.slug = slug;
  LS.set('sc.project', slug);
  history.replaceState(null, '', '#' + slug);
  closeDetail();
  S.state = null;
  renderSidebar();
  renderTop();
  if (source) source.close();
  api('/api/state?project=' + encodeURIComponent(slug)).then(apply).catch(() => setLive(false));
  connect();
}

/* ── live updates, one project at a time ──────────────────────────── */
function connect() {
  const slug = S.slug;
  source = new EventSource('/events?project=' + encodeURIComponent(slug));
  source.onopen = () => setLive(true);
  source.onmessage = m => { if (slug === S.slug) { setLive(true); apply(JSON.parse(m.data)); } };
  source.onerror = () => {
    setLive(false);
    source.close();
    // Reconnect only if still on the same project and still signed in.
    setTimeout(() => { if (S.me && S.slug === slug) connect(); }, 3000);
  };
}

function setLive(on) {
  $('#wsLive').textContent = on ? t('live') : t('offline');
  $('#wsLive').classList.toggle('off', !on);
}

/* ── graph, built from the same snapshot ──────────────────────────── */
function buildGraph(st) {
  const nodes = [], links = [];
  for (const s of st.sections) {
    nodes.push({ id: 'sec:' + s.key, level: 'section', label: s.title,
                 colour: LEVEL_COLOUR.section, r: LEVEL_RADIUS.section, ref: s });
  }
  for (const e of st.events) {
    nodes.push({ id: 'e:' + e.id, level: e.level, label: e.summary,
                 colour: LEVEL_COLOUR[e.level] || LEVEL_COLOUR.note,
                 r: LEVEL_RADIUS[e.level] || 5, dead: e.is_dead, ref: e });
    if (e.section_key) links.push({ s: 'e:' + e.id, t: 'sec:' + e.section_key, kind: 'in' });
    if (e.supersedes_id) links.push({ s: 'e:' + e.id, t: 'e:' + e.supersedes_id, kind: 'kills' });
    for (const r of e.refs || []) links.push({ s: 'e:' + e.id, t: 'e:' + r, kind: 'refs' });
  }
  const ids = new Set(nodes.map(n => n.id));
  return { nodes, links: links.filter(l => ids.has(l.s) && ids.has(l.t)) };
}

function renderLegend(g) {
  const seen = new Set(), box = $('#legend');
  box.textContent = '';
  for (const n of g.nodes) {
    if (seen.has(n.level)) continue;
    seen.add(n.level);
    const s = el('span'), i = el('i');
    i.style.background = n.colour;
    s.append(i, document.createTextNode(t('lvl_' + n.level)));
    box.append(s);
  }
}

/* ── who is here, and through what ────────────────────────────────── */
/* The web cannot see anyone's conversations, so it does not pretend to. What it
   can see is which assistant last wrote under each name -- so that is what it
   shows, instead of a "pick your session" menu that could never be true. */
function renderPeople() {
  const box = $('#people');
  box.textContent = '';
  if (!S.state) return;
  box.title = t('who_here');
  for (const m of S.state.members) {
    const b = el('span', 'person' + (m.agent ? '' : ' idle'));
    b.append(el('span', 'p-bead', initials(m.name)));
    b.append(el('span', 'p-agent', m.agent || '—'));
    b.title = m.name + ' · ' + (m.agent ? m.agent + ' · ' + fmt(m.last_at) : t('never_wrote'));
    box.append(b);
  }
}

/* ── board ────────────────────────────────────────────────────────── */
function renderBoard() {
  const st = S.state;

  const stand = $('#stand');
  stand.textContent = '';
  const groups = [
    [t('decisions'), st.brief.decisions, ''],
    [t('facts'), st.brief.facts, ''],
    [t('open'), st.brief.questions, 'q']
  ];
  for (const [label, items, cls] of groups) {
    const box = el('div', 'box');
    box.append(el('h3', 'rubric', label));
    if (!items.length) box.append(el('p', 'why', t('empty_col')));
    for (const e of items) {
      const p = el('p', cls);
      p.append(el('span', 'num', '#' + e.id), el('b', null, e.summary));
      if (e.level === 'decision' && e.intent) {
        p.append(el('span', 'why', ' — ' + t('because') + ' ' + e.intent));
      }
      p.style.cursor = 'pointer';
      p.addEventListener('click', () => openEntry(e.id));
      box.append(p);
    }
    stand.append(box);
  }

  const doc = $('#docCol');
  doc.textContent = '';
  if (!st.sections.length) doc.append(el('p', 'empty', t('empty_doc')));
  for (const s of st.sections) {
    const d = el('div', 'doc');
    d.append(el('h3', null, s.title), el('p', null, s.content));
    d.addEventListener('click', () => openSection(s.key));
    doc.append(d);
  }

  const feed = $('#feedCol');
  feed.textContent = '';
  const list = st.events.slice().sort((a, b) => b.id - a.id);
  if (!list.length) feed.append(el('p', 'empty', t('empty_col')));
  for (const e of list) {
    const card = el('div', 'entry' + (e.is_dead ? ' dead' : ''));
    const head = el('div', 'e-head');
    head.append(el('span', 'e-num', '#' + e.id), el('span', 'e-who', e.member_name),
                el('span', 'tag', e.agent), el('span', 'tag', t('lvl_' + e.level)));
    if (e.section_key) head.append(el('span', 'tag', e.section_key));
    head.append(el('span', 'e-when', fmt(e.created_at)));
    card.append(head, el('p', 'e-title', e.summary));
    if (e.details) card.append(el('p', 'e-body', e.details));
    if (e.intent) {
      const p = el('p', 'e-line');
      p.append(el('b', 'want', t(e.intent_source === 'stated' ? 'wanted' : 'inferred') + ' '),
               el('span', 'want', e.intent));
      card.append(p);
    }
    for (const r of e.rejected || []) {
      const p = el('p', 'e-line');
      p.append(el('b', 'drop', t('dropped') + ' '),
               el('span', 'drop', r.option + (r.reason ? ' — ' + r.reason : '')));
      card.append(p);
    }
    if (e.refs && e.refs.length) {
      card.append(el('p', 'e-line', t('builds_on') + ' ' + e.refs.map(r => '#' + r).join(', ')));
    }
    if (e.supersedes_id) card.append(el('p', 'e-line kill', t('supersedes', { n: e.supersedes_id })));
    if (e.is_dead) card.append(el('p', 'e-line gone', t('superseded')));
    if (e.artifact_url) card.append(el('div', 'art', '📎 ' + e.artifact_url));
    card.addEventListener('click', () => openEntry(e.id));
    feed.append(card);
  }
}

/* ── detail ───────────────────────────────────────────────────────── */
function row(label, value) {
  const r = el('div', 'd-row');
  r.append(el('span', null, label), el('div', null, value));
  return r;
}
function relBtn(e) {
  const b = el('button'), i = el('i');
  i.style.background = LEVEL_COLOUR[e.level] || LEVEL_COLOUR.note;
  b.append(i, el('span', null, '#' + e.id + ' · ' + e.summary));
  b.addEventListener('click', () => openEntry(e.id));
  return b;
}
function openEntry(id) { S.open = { type: 'entry', id }; reopen(); }
function openSection(key) { S.open = { type: 'section', id: key }; reopen(); }
function closeDetail() { S.open = null; $('#detail').classList.remove('open'); if (graph) graph.select(null); }

function reopen() {
  if (!S.open || !S.state) return;
  const body = $('#detailBody');
  body.textContent = '';

  if (S.open.type === 'section') {
    const s = S.state.sections.find(x => x.key === S.open.id);
    if (!s) return closeDetail();
    graph.select('sec:' + s.key);
    const k = el('div', 'd-kind'), i = el('i');
    i.style.background = LEVEL_COLOUR.section;
    k.style.color = LEVEL_COLOUR.section;
    k.append(i, document.createTextNode(t('lvl_section')));
    body.append(k, el('h2', null, s.title), el('div', 'quote', s.content || '—'));

    const touched = S.state.events.filter(e => e.section_key === s.key).sort((a, b) => b.id - a.id);
    const rows = el('div', 'd-rows');
    rows.append(row(t('d_instate'), t('d_yes')), row(t('d_derived'), t('src_section')));
    if (touched[0]) rows.append(row(t('d_when'), fmt(touched[0].created_at) + ' · ' + touched[0].member_name));
    body.append(rows);
    if (touched.length) {
      body.append(el('h4', 'rubric', t('d_related')));
      const rel = el('div', 'rel');
      for (const e of touched) rel.append(relBtn(e));
      body.append(rel);
    }
  } else {
    const e = entry(S.open.id);
    if (!e) return closeDetail();
    graph.select('e:' + e.id);
    const colour = LEVEL_COLOUR[e.level] || LEVEL_COLOUR.note;
    const k = el('div', 'd-kind'), i = el('i');
    i.style.background = colour; k.style.color = colour;
    k.append(i, document.createTextNode(t('lvl_' + e.level)));
    body.append(k, el('h2', null, e.summary));
    if (e.details) body.append(el('div', 'quote', e.details));

    if (e.intent) {
      const p = el('p', 'e-line');
      p.append(el('b', 'want', t(e.intent_source === 'stated' ? 'wanted' : 'inferred') + ' '),
               el('span', 'want', e.intent));
      body.append(p);
    }
    for (const r of e.rejected || []) {
      const p = el('p', 'e-line');
      p.append(el('b', 'drop', t('dropped') + ' '),
               el('span', 'drop', r.option + (r.reason ? ' — ' + r.reason : '')));
      body.append(p);
    }

    const rows = el('div', 'd-rows');
    rows.append(row(t('d_level'), t('lvl_' + e.level)));
    rows.append(row(t('d_derived'), t(SOURCE_KEY[e.level_source] || 'src_none')));
    rows.append(row(t('d_instate'), t(e.is_dead ? 'd_meta_only' : IN_STATE[e.level])));
    rows.append(row(t('d_author'), e.member_name));
    rows.append(row(t('d_agent'), e.agent));
    rows.append(row(t('d_when'), fmt(e.created_at)));
    if (e.section_key) rows.append(row(t('d_section'), e.section_key));
    if (e.artifact_url) rows.append(row(t('d_artifact'), e.artifact_url));
    body.append(rows);

    const rel = [];
    for (const r of e.refs || []) { const x = entry(r); if (x) rel.push(x); }
    if (e.supersedes_id) { const x = entry(e.supersedes_id); if (x) rel.push(x); }
    for (const x of S.state.events) if (x.supersedes_id === e.id) rel.push(x);
    if (rel.length) {
      body.append(el('h4', 'rubric', t('d_related')));
      const box = el('div', 'rel');
      for (const x of rel) box.append(relBtn(x));
      body.append(box);
    }
    body.append(el('p', 'd-note', t('d_untrusted')));
  }
  $('#detail').classList.add('open');
}

/* ── new project ──────────────────────────────────────────────────── */
function openNewProject() {
  $('#npName').value = '';
  $('#npErr').hidden = true;
  const sw = $('#npColours');
  sw.textContent = '';
  const pick = S.me.palette[S.me.projects.length % S.me.palette.length];
  sw.dataset.colour = pick;
  for (const col of S.me.palette) {
    const b = el('button', col === pick ? 'on' : '');
    b.type = 'button';
    b.style.background = col;
    b.addEventListener('click', () => {
      for (const x of sw.children) x.classList.remove('on');
      b.classList.add('on');
      sw.dataset.colour = col;
    });
    sw.append(b);
  }
  const box = $('#npMembers');
  box.textContent = '';
  for (const name of S.me.people) {
    const lab = el('label', 'check');
    const cb = el('input');
    cb.type = 'checkbox'; cb.value = name;
    if (name === S.me.name) { cb.checked = true; cb.disabled = true; }
    lab.append(cb, document.createTextNode(' ' + name + (name === S.me.name ? ' ' + t('np_you') : '')));
    box.append(lab);
  }
  $('#npModal').hidden = false;
  setTimeout(() => $('#npName').focus(), 0);
}

async function createProject(ev) {
  ev.preventDefault();
  const title = $('#npName').value.trim();
  if (!title) { $('#npErr').textContent = t('np_need_name'); $('#npErr').hidden = false; return; }
  const members = [...$('#npMembers').querySelectorAll('input:checked:not(:disabled)')].map(c => c.value);
  try {
    const r = await api('/api/projects', {
      method: 'POST', body: { title, colour: $('#npColours').dataset.colour, members }
    });
    $('#npModal').hidden = true;
    await refreshMe();
    selectProject(r.slug);
    toast(t('t_created', { n: r.title }));
  } catch (e) {
    $('#npErr').textContent = e.message; $('#npErr').hidden = false;
  }
}

/* ── link a conversation ──────────────────────────────────────────── */
async function openLink() {
  const p = project();
  if (!p) return;
  const r = await api(`/api/projects/${encodeURIComponent(p.slug)}/link?lang=${LANG}`);
  $('#linkTitle').textContent = t('link_title', { t: p.title });
  $('#linkSteps').textContent = t('link_steps', { t: p.title });
  $('#linkText').value = r.text;
  $('#linkNote').textContent = t('link_note');
  $('#linkModal').hidden = false;
}

async function copyLink() {
  const ta = $('#linkText');
  try {
    await navigator.clipboard.writeText(ta.value);
  } catch (e) {
    ta.select(); document.execCommand('copy');
  }
  toast(t('copied'));
}

/* ── shell ────────────────────────────────────────────────────────── */
function applyStrings() {
  document.documentElement.lang = LANG;
  $('#loginTitle').textContent = t('login_title');
  $('#loginHelp').textContent = t('login_help');
  $('#loginToken').placeholder = t('login_placeholder');
  $('#loginGo').textContent = t('login_go');
  $('#q').placeholder = t('search');
  $('#noResults').textContent = t('no_results');
  $('#collapseBtn').title = S.collapsed ? t('expand') : t('collapse');
  $('#newProjectBtn').querySelector('span').textContent = t('new_project');
  $('#logoutBtn').textContent = t('logout');
  $('#viewSwitch').querySelector('[data-view=graph] span').textContent = t('view_graph');
  $('#viewSwitch').querySelector('[data-view=board] span').textContent = t('view_board');
  $('#linkBtn').querySelector('span').textContent = t('link');
  $('#resetBtn').querySelector('span').textContent = t('reset_view');
  $('#lblStand').textContent = t('stand');
  $('#lblDoc').textContent = t('document');
  $('#lblFeed').textContent = t('feed');
  $('#langLabel').textContent = LANG.toUpperCase();
  $('#detailClose').title = t('d_close');
  $('#graphHint').textContent = t(S.dim === 3 ? 'graph_hint_3d' : 'graph_hint_2d');
  $('#npTitle').textContent = t('np_title');
  $('#npNameL').textContent = t('np_name');
  $('#npColourL').textContent = t('np_colour');
  $('#npMembersL').textContent = t('np_members');
  $('#npCancel').textContent = t('cancel');
  $('#npCreate').textContent = t('np_create');
  $('#linkCancel').textContent = t('cancel');
  $('#linkCopy').textContent = t('copy');
  $('#emptyMsg').textContent = t('no_projects');
}

function renderTop() {
  const p = project(), st = S.state;
  $('#wsDot').style.setProperty('--c', p ? p.colour : '#555');
  $('#wsName').textContent = p ? p.title : '';
  $('#wsMeta').textContent = st
    ? t('meta', { e: st.events.length, s: st.sections.length, m: st.members_active_today })
    : (p ? t('loading') : '');
  $('#linkBtn').hidden = !p;
  for (const b of $('#viewSwitch').children) b.classList.toggle('on', b.dataset.view === S.view);
  for (const b of $('#dimSwitch').children) b.classList.toggle('on', +b.dataset.dim === S.dim);
  $('#emptyView').hidden = !!p;
  $('#graphView').hidden = !p || S.view !== 'graph';
  $('#boardView').hidden = !p || S.view !== 'board';
  $('#graphHint').textContent = t(S.dim === 3 ? 'graph_hint_3d' : 'graph_hint_2d');
  renderPeople();
}

function renderLangMenu() {
  const m = $('#langMenu');
  m.textContent = '';
  for (const code of Object.keys(STRINGS)) {
    const b = el('button', code === LANG ? 'on' : '');
    b.append(el('div', 'm-main', STRINGS[code]._name));
    if (code === LANG) b.append(el('span', 'tick', '✓'));
    b.addEventListener('click', () => {
      setLang(code); m.hidden = true;
      applyStrings(); renderSidebar(); renderTop();
      if (S.state) { renderBoard(); renderLegend(buildGraph(S.state)); }
      if (S.open) reopen();
    });
    m.append(b);
  }
}

function apply(state) {
  if (state.slug !== S.slug) return;   // a late answer for a project we have left
  S.state = state;
  document.title = state.title + ' · shared-context';
  renderTop();
  renderBoard();
  const g = buildGraph(state);
  graph.setData(g);
  graph.setDim(S.dim);
  renderLegend(g);
  if (S.open) reopen();
}

async function boot() {
  let me;
  try { me = await api('/api/me'); } catch (e) { return; }
  S.me = me;
  $('#login').hidden = true;
  $('#app').hidden = false;
  document.getElementById('app').classList.toggle('collapsed', S.collapsed);
  graph.resize();

  const want = decodeURIComponent(location.hash.slice(1)) || LS.get('sc.project');
  const pick = me.projects.find(p => p.slug === want) || me.projects[0];
  renderSidebar();
  if (pick) selectProject(pick.slug);
  else { S.slug = null; renderTop(); }

  clearInterval(meTimer);
  meTimer = setInterval(refreshMe, 15000);   // unread counts for the other projects
}

function init() {
  graph = new GraphView($('#canvas'), (ref, node) => {
    if (!ref) return closeDetail();
    if (node.level === 'section') openSection(ref.key); else openEntry(ref.id);
  });

  $('#loginForm').addEventListener('submit', signIn);
  $('#logoutBtn').addEventListener('click', signOut);

  $('#collapseBtn').addEventListener('click', () => {
    S.collapsed = !S.collapsed;
    LS.set('sc.collapsed', S.collapsed ? '1' : '0');
    document.getElementById('app').classList.toggle('collapsed', S.collapsed);
    $('#collapseBtn').title = S.collapsed ? t('expand') : t('collapse');
  });
  $('#q').addEventListener('input', ev => { S.query = ev.target.value; renderSidebar(); });

  $('#newProjectBtn').addEventListener('click', openNewProject);
  $('#npForm').addEventListener('submit', createProject);
  for (const b of document.querySelectorAll('.np-close')) b.addEventListener('click', () => { $('#npModal').hidden = true; });

  $('#linkBtn').addEventListener('click', openLink);
  $('#linkCopy').addEventListener('click', copyLink);
  for (const b of document.querySelectorAll('.link-close')) b.addEventListener('click', () => { $('#linkModal').hidden = true; });

  for (const id of ['#npModal', '#linkModal']) {
    $(id).addEventListener('click', ev => { if (ev.target.id === id.slice(1)) $(id).hidden = true; });
  }

  $('#viewSwitch').addEventListener('click', ev => {
    const b = ev.target.closest('[data-view]'); if (!b) return;
    S.view = b.dataset.view; LS.set('sc.view', S.view);
    renderTop();
    if (S.view === 'graph') graph.resize();
  });
  $('#dimSwitch').addEventListener('click', ev => {
    const b = ev.target.closest('[data-dim]'); if (!b) return;
    S.dim = +b.dataset.dim; LS.set('sc.dim', String(S.dim));
    graph.setDim(S.dim); graph.spin = S.dim === 3;
    renderTop();
  });
  $('#resetBtn').addEventListener('click', () => graph.reset());
  $('#detailClose').addEventListener('click', closeDetail);

  $('#langBtn').addEventListener('click', ev => {
    ev.stopPropagation();
    const m = $('#langMenu');
    if (m.hidden) { renderLangMenu(); m.hidden = false; } else m.hidden = true;
  });
  document.addEventListener('click', () => { $('#langMenu').hidden = true; });
  $('#langMenu').addEventListener('click', ev => ev.stopPropagation());

  document.addEventListener('keydown', ev => {
    if (ev.key !== 'Escape') return;
    if (!$('#npModal').hidden) $('#npModal').hidden = true;
    else if (!$('#linkModal').hidden) $('#linkModal').hidden = true;
    else if (S.open) closeDetail();
  });
  window.addEventListener('hashchange', () => {
    const slug = decodeURIComponent(location.hash.slice(1));
    if (S.me && slug !== S.slug && S.me.projects.some(p => p.slug === slug)) selectProject(slug);
  });

  applyStrings();
  graph.spin = S.dim === 3;
  graph.start();
  boot();
}

init();
