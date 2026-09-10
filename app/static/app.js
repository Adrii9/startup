/* Wiring. The server owns every project and every account; this only draws them.

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
const btn = (cls, txt, onClick) => {
  const b = el('button', cls, txt);
  b.type = 'button';
  if (onClick) b.addEventListener('click', onClick);
  return b;
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
  config: { google: false }, me: null, slug: null, state: null, open: null, query: '',
  view: LS.get('sc.view') || 'board',
  dim: +(LS.get('sc.dim') || 2),
  collapsed: LS.get('sc.collapsed') === '1'
};
let graph, source, meTimer;

const initials = n => n.trim().split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase() || '·';
const toDate = iso => new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso.replace(' ', 'T') + 'Z');
const fmt = iso => new Intl.DateTimeFormat(locale(), {
  day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(toDate(iso));
const fmtDay = iso => new Intl.DateTimeFormat(locale(), { day: 'numeric', month: 'short' }).format(toDate(iso));
const who = e => e.member_gone ? t('former_member') : e.member_name;
const entry = id => S.state.events.find(e => e.id === id);
const project = () => S.me && S.me.projects.find(p => p.slug === S.slug);
const isOwner = () => S.state && S.state.role === 'owner';

async function api(path, opts = {}) {
  const r = await fetch(path, {
    method: opts.method || 'GET',
    headers: opts.body ? { 'content-type': 'application/json' } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined
  });
  if (r.status === 401 && !opts.quiet401) { showLogin(); throw new Error('signed out'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || r.statusText),
                                 { status: r.status, code: data.code, params: data.params });
  return data;
}

/* The server's reason, in the reader's language when it sent a code for it,
   and in its own words otherwise. */
const errMsg = e => (e.code && STRINGS.en[e.code]) ? t(e.code, e.params || {}) : e.message;

let toastT;
function toast(msg) {
  const n = $('#toast');
  n.textContent = msg; n.hidden = false;
  clearTimeout(toastT);
  toastT = setTimeout(() => { n.hidden = true; }, 2800);
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const ta = el('textarea'); ta.value = text; document.body.append(ta);
    ta.select(); document.execCommand('copy'); ta.remove();
  }
  toast(t('copied'));
}

/* ── one dialog shell, filled per use ─────────────────────────────── */
function openSheet({ title, body, foot = [], wide = false }) {
  $('#sheetTitle').textContent = title;
  $('#sheet').classList.toggle('wide-sheet', wide);
  const b = $('#sheetBody'); b.textContent = '';
  for (const n of [].concat(body)) b.append(n);
  const f = $('#sheetFoot'); f.textContent = '';
  for (const n of foot) f.append(n);
  $('#sheetFoot').hidden = !foot.length;
  $('#sheetModal').hidden = false;
}
function closeSheet() { $('#sheetModal').hidden = true; }

/* A yes/no that resolves to whether the person really meant it. Destructive
   actions go through here rather than confirm(), so the consequence is spelled
   out in the same words everywhere and the button says what it will do. */
function confirmSheet({ title, body, action, danger = true }) {
  return new Promise(resolve => {
    const done = v => { closeSheet(); resolve(v); };
    openSheet({
      title,
      body: [el('p', 'sheet-text', body)],
      foot: [btn('ghost-btn', t('cancel'), () => done(false)),
             btn(danger ? 'danger-btn' : 'primary-btn', action, () => done(true))]
    });
  });
}

function field(label, input) {
  const f = el('label', 'field');
  f.append(el('span', null, label), input);
  return f;
}
function swatches(palette, current) {
  const box = el('div', 'swatches');
  box.dataset.colour = current;
  for (const col of palette) {
    const b = btn(col === current ? 'on' : '', null, () => {
      for (const x of box.children) x.classList.remove('on');
      b.classList.add('on'); box.dataset.colour = col;
    });
    b.style.background = col;
    box.append(b);
  }
  return box;
}

/* ── signing in ───────────────────────────────────────────────────── */
let authMode = 'login';
let authNext = '/';

function showLogin(next) {
  if (source) { source.close(); source = null; }
  clearInterval(meTimer);
  S.me = null; S.state = null;
  $('#app').hidden = true; $('#join').hidden = true;
  $('#login').hidden = false;
  authNext = next || (location.pathname + location.hash) || '/';
  // Google only appears when the server has it configured; otherwise the page
  // is just the two fields, which is all an MVP needs.
  $('#googleBtn').href = '/auth/google?next=' + encodeURIComponent(authNext);
  $('#googleBtn').hidden = !S.config.google;
  $('#googleOr').hidden = !S.config.google;
  setAuthMode(authMode);
  // Focus without scrolling: on a short screen the browser would otherwise
  // push the card up to show the field, hiding the sign-in / sign-up tabs.
  setTimeout(() => $('#authUser').focus({ preventScroll: true }), 0);
}

function setAuthMode(mode) {
  authMode = mode;
  for (const b of $('#authTabs').children) b.classList.toggle('on', b.dataset.mode === mode);
  $('#authGo').textContent = t(mode === 'login' ? 'login_go' : 'signup_go');
  $('#authPass').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  $('#authHint').hidden = mode !== 'signup';
  $('#authErr').hidden = true;
}

async function submitAuth(ev) {
  ev.preventDefault();
  const body = { username: $('#authUser').value.trim(), password: $('#authPass').value, next: authNext };
  $('#authGo').disabled = true;
  try {
    const r = await api('/auth/' + (authMode === 'login' ? 'login' : 'signup'),
                        { method: 'POST', body, quiet401: true });
    $('#authPass').value = '';
    // Assigning an address that differs only after the # does not reload the
    // page, so the app would sit on the sign-in screen. Same page: boot in place.
    const target = new URL(r.next || '/', location.origin);
    if (target.pathname === location.pathname) {
      history.replaceState(null, '', target.pathname + target.hash);
      boot();
    } else {
      location.href = target.href;
    }
  } catch (e) {
    $('#authErr').textContent = errMsg(e);
    $('#authErr').hidden = false;
  } finally {
    $('#authGo').disabled = false;
  }
}

async function signOut() {
  await fetch('/auth/logout', { method: 'POST' });
  history.replaceState(null, '', '/');
  showLogin();
  toast(t('t_signed_out'));
}

/* ── joining from an invite link ──────────────────────────────────── */
async function showJoin(code) {
  $('#app').hidden = true; $('#login').hidden = true; $('#join').hidden = false;
  const r = await fetch('/api/invites/' + encodeURIComponent(code));
  const b = $('#joinBtn'), g = $('#joinGoogle');
  b.hidden = true; g.hidden = true;
  if (!r.ok) {
    $('#joinBead').hidden = true;
    $('#joinTitle').textContent = t('join_invalid');
    $('#joinHelp').textContent = t('join_invalid_help');
    return;
  }
  const inv = await r.json();
  $('#joinBead').hidden = false;
  $('#joinBead').textContent = initials(inv.title);
  $('#joinBead').style.setProperty('--c', inv.colour);
  $('#joinTitle').textContent = t('join_title', { i: inv.inviter, t: inv.title });
  if (inv.already_member) {
    $('#joinHelp').textContent = t('join_already');
    b.hidden = false; b.textContent = t('join_open');
    b.onclick = () => { location.href = '/#' + inv.slug; };
  } else if (inv.signed_in) {
    $('#joinHelp').textContent = t('join_help');
    b.hidden = false; b.textContent = t('join_btn');
    b.onclick = async () => {
      const res = await api('/api/invites/' + encodeURIComponent(code) + '/accept', { method: 'POST' });
      location.href = '/#' + res.slug;
    };
  } else {
    // Not signed in: sign in or sign up, and come straight back here to join.
    $('#joinHelp').textContent = t('join_help');
    b.hidden = false; b.textContent = t('join_google');
    b.onclick = () => { authMode = 'signup'; showLogin('/join/' + code); };
  }
}

/* ── projects rail ────────────────────────────────────────────────── */
function renderSidebar() {
  const list = $('#ctxList');
  list.textContent = '';
  const q = S.query.trim().toLowerCase();
  const shown = S.me.projects.filter(p => !q || p.title.toLowerCase().includes(q));
  $('#noResults').hidden = shown.length > 0 || !q;

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
  const a = S.me.account;
  $('#accountName').textContent = a.name;
  $('#accountAvatar').textContent = initials(a.name);
  $('#accountBtn').title = a.email || a.name;
}

function renderAccountMenu() {
  const m = $('#accountMenu');
  m.textContent = '';
  const head = el('div', 'menu-head');
  head.append(el('b', null, S.me.account.name), el('span', null, S.me.account.email || ''));
  m.append(head);
  const item = (label, fn, cls = '') => {
    const b = el('button', cls);
    b.append(el('div', 'm-main', label));
    b.addEventListener('click', () => { m.hidden = true; fn(); });
    m.append(b);
  };
  item(t('acc_connections'), openConnections);
  item(t('acc_trash'), openTrash);
  item(t('acc_password'), openChangePassword);
  m.append(el('hr'));
  item(t('logout'), signOut);
  item(t('acc_delete'), deleteAccount, 'danger-item');
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
  history.replaceState(null, '', '/#' + slug);
  closeDetail();
  S.state = null;
  renderSidebar();
  renderTop();
  if (source) source.close();
  api('/api/state?project=' + encodeURIComponent(slug)).then(apply).catch(() => setLive(false));
  connect();
}

async function pickAfterChange(msg) {
  await refreshMe();
  const next = S.me.projects[0];
  if (next) selectProject(next.slug); else { S.slug = null; S.state = null; renderTop(); }
  if (msg) toast(msg);
}

/* ── live updates, one project at a time ──────────────────────────── */
function connect() {
  const slug = S.slug;
  source = new EventSource('/events?project=' + encodeURIComponent(slug));
  source.onopen = () => setLive(true);
  source.onmessage = m => { if (slug === S.slug) { setLive(true); apply(JSON.parse(m.data)); } };
  // The server says this project is no longer ours: removed, deleted, signed out.
  source.addEventListener('gone', () => {
    source.close(); source = null;
    if (slug === S.slug) pickAfterChange(t('t_gone'));
  });
  source.onerror = () => {
    setLive(false);
    if (source) source.close();
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
    head.append(el('span', 'e-num', '#' + e.id), el('span', 'e-who' + (e.member_gone ? ' gone-who' : ''), who(e)),
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
    if (touched[0]) rows.append(row(t('d_when'), fmt(touched[0].created_at) + ' · ' + who(touched[0])));
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
    rows.append(row(t('d_author'), who(e)));
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
  const name = el('input'); name.maxLength = 80;
  const sw = swatches(S.me.palette, S.me.palette[S.me.projects.length % S.me.palette.length]);
  const err = el('p', 'login-err'); err.hidden = true;
  const create = async () => {
    const title = name.value.trim();
    if (!title) { err.textContent = t('np_need_name'); err.hidden = false; return; }
    try {
      const r = await api('/api/projects', { method: 'POST', body: { title, colour: sw.dataset.colour } });
      closeSheet();
      await refreshMe();
      selectProject(r.slug);
      toast(t('t_created', { n: r.title }));
    } catch (e) { err.textContent = errMsg(e); err.hidden = false; }
  };
  name.addEventListener('keydown', ev => { if (ev.key === 'Enter') create(); });
  openSheet({
    title: t('np_title'),
    body: [field(t('np_name'), name), (() => { const f = el('div', 'field');
      f.append(el('span', null, t('np_colour')), sw); return f; })(),
      el('p', 'sheet-note', t('np_after')), err],
    foot: [btn('ghost-btn', t('cancel'), closeSheet), btn('primary-btn', t('np_create'), create)]
  });
  setTimeout(() => name.focus(), 0);
}

/* ── link a conversation ──────────────────────────────────────────── */
async function openLink() {
  const p = project();
  if (!p) return;
  const r = await api(`/api/projects/${encodeURIComponent(p.slug)}/link?lang=${LANG}`);
  const ta = el('textarea', 'mono-area'); ta.readOnly = true; ta.value = r.text;
  openSheet({
    title: t('link_title', { t: p.title }), wide: true,
    body: [el('p', 'link-steps', t('link_steps', { t: p.title })), ta,
           el('p', 'sheet-note', t('link_note'))],
    foot: [btn('ghost-btn', t('close'), closeSheet),
           btn('primary-btn', t('copy'), () => copyText(ta.value))]
  });
}

/* ── connections ──────────────────────────────────────────────────── */
async function openConnections(fresh) {
  const { connections } = await api('/api/connections');
  const body = [el('p', 'sheet-text', t('con_help'))];

  // The one moment a connector URL is visible. Nothing stores it, so this box
  // is the only copy that will ever exist.
  if (fresh) {
    const box = el('div', 'fresh-url');
    box.append(el('b', null, t('con_new_title') + ' · ' + fresh.label));
    const url = el('code', 'url-line', fresh.url);
    const row = el('div', 'url-row');
    row.append(url, btn('primary-btn', t('copy'), () => copyText(fresh.url)));
    box.append(row, el('p', 'warn-line', t('con_new_warning')));
    body.push(box);
  }

  const list = el('div', 'rows');
  if (!connections.length) list.append(el('p', 'empty', t('con_empty')));
  for (const c of connections) {
    const r = el('div', 'item-row');
    const main = el('div', 'item-main');
    main.append(el('b', null, c.label));
    main.append(el('span', 'item-sub', c.prefix + '… · ' + t('con_created', { d: fmtDay(c.created_at) }) +
      ' · ' + (c.last_used_at ? t('con_used', { d: fmt(c.last_used_at) }) : t('con_never'))));
    r.append(main, btn('ghost-btn small danger-text', t('con_revoke'), async () => {
      const ok = await confirmSheet({ title: t('con_revoke_q', { n: c.label }),
                                      body: t('con_revoke_body'), action: t('con_revoke') });
      if (ok) { await api('/api/connections/' + c.id, { method: 'DELETE' }); toast(t('t_revoked')); }
      openConnections();
    }));
    list.append(r);
  }
  body.push(list);

  const label = el('input'); label.placeholder = t('con_placeholder'); label.maxLength = 60;
  const make = async () => {
    const made = await api('/api/connections', { method: 'POST', body: { label: label.value } });
    openConnections(made);
  };
  label.addEventListener('keydown', ev => { if (ev.key === 'Enter') make(); });
  const add = el('div', 'add-row');
  add.append(label, btn('primary-btn', t('con_create'), make));
  body.push(field(t('con_label'), add));

  openSheet({ title: t('con_title'), body, wide: true, foot: [btn('ghost-btn', t('close'), closeSheet)] });
}

/* ── project settings ─────────────────────────────────────────────── */
async function openSettings() {
  const p = project();
  if (!p) return;
  const slug = encodeURIComponent(p.slug);
  const { role, you, members } = await api(`/api/projects/${slug}/members`);
  const owner = role === 'owner';
  const body = [];

  if (owner) {
    const name = el('input'); name.value = p.title; name.maxLength = 80;
    const sw = swatches(S.me.palette, p.colour);
    const save = btn('primary-btn small', t('save'), async () => {
      await api(`/api/projects/${slug}`, { method: 'PATCH',
        body: { title: name.value, colour: sw.dataset.colour } });
      await refreshMe(); renderTop(); toast(t('ps_saved'));
    });
    const top = el('div', 'settings-top');
    top.append(field(t('ps_name'), name));
    const cf = el('div', 'field'); cf.append(el('span', null, t('ps_colour')), sw);
    top.append(cf, save);
    body.push(top);
  }

  body.push(el('h4', 'rubric', t('ps_members')));
  const mlist = el('div', 'rows');
  for (const m of members) {
    const r = el('div', 'item-row');
    const main = el('div', 'item-main person-main');
    main.append(el('span', 'p-bead', initials(m.name)));
    const nm = el('div');
    nm.append(el('b', null, m.name + (m.account_id === you ? ' ' + t('ps_you') : '')));
    nm.append(el('span', 'item-sub', t(m.role === 'owner' ? 'ps_owner' : 'ps_member') +
      (m.agent ? ' · ' + m.agent : '')));
    main.append(nm);
    r.append(main);
    if (owner && m.role !== 'owner') {
      const acts = el('div', 'item-acts');
      acts.append(btn('ghost-btn small', t('ps_make_owner'), async () => {
        if (await confirmSheet({ title: t('ps_owner_q', { n: m.name }), body: t('ps_owner_body'),
                                 action: t('ps_make_owner'), danger: false })) {
          await api(`/api/projects/${slug}/owner`, { method: 'POST', body: { account_id: m.account_id } });
          await refreshMe(); selectProject(p.slug);
        } else openSettings();
      }));
      acts.append(btn('ghost-btn small danger-text', t('ps_remove'), async () => {
        if (await confirmSheet({ title: t('ps_remove_q', { n: m.name }), body: t('ps_remove_body'),
                                 action: t('ps_remove') })) {
          await api(`/api/projects/${slug}/members/${m.account_id}`, { method: 'DELETE' });
        }
        openSettings();
      }));
      r.append(acts);
    }
    mlist.append(r);
  }
  body.push(mlist);

  if (owner) {
    body.push(el('h4', 'rubric', t('ps_invites')));
    body.push(el('p', 'sheet-note', t('ps_invite_help')));
    const { invites } = await api(`/api/projects/${slug}/invites`);
    const ilist = el('div', 'rows');
    if (!invites.length) ilist.append(el('p', 'empty', t('ps_no_invites')));
    for (const inv of invites) {
      const r = el('div', 'item-row');
      const main = el('div', 'item-main');
      main.append(el('code', 'url-line', inv.url));
      main.append(el('span', 'item-sub', t('ps_invite_meta', { d: fmtDay(inv.expires_at),
        u: inv.max_uses ? inv.uses + '/' + inv.max_uses : inv.uses })));
      const acts = el('div', 'item-acts');
      acts.append(btn('ghost-btn small', t('copy'), () => copyText(inv.url)));
      acts.append(btn('ghost-btn small danger-text', t('ps_invite_revoke'), async () => {
        await api(`/api/projects/${slug}/invites/${inv.id}`, { method: 'DELETE' });
        openSettings();
      }));
      r.append(main, acts);
      ilist.append(r);
    }
    body.push(ilist);
    body.push(btn('ghost-btn wide-btn', t('ps_invite_create'), async () => {
      const inv = await api(`/api/projects/${slug}/invites`, { method: 'POST', body: {} });
      await copyText(inv.url);
      openSettings();
    }));
  } else {
    body.push(el('p', 'sheet-note', t('ps_member_only')));
  }

  const danger = el('div', 'danger');
  if (owner) {
    danger.append(el('span', null, t('ps_danger')), btn('danger-btn', t('ps_delete'), async () => {
      if (await confirmSheet({ title: t('ps_delete_q', { t: p.title }), body: t('ps_delete_body'),
                               action: t('ps_delete') })) {
        await api(`/api/projects/${slug}`, { method: 'DELETE' });
        await pickAfterChange(t('t_deleted', { n: p.title }));
      } else openSettings();
    }));
  } else {
    danger.append(el('span', null, t('ps_danger')), btn('danger-btn', t('ps_leave'), async () => {
      if (await confirmSheet({ title: t('ps_leave_q', { t: p.title }), body: t('ps_leave_body'),
                               action: t('ps_leave') })) {
        await api(`/api/projects/${slug}/members/${you}`, { method: 'DELETE' });
        await pickAfterChange(t('t_left', { n: p.title }));
      } else openSettings();
    }));
  }
  body.push(danger);

  openSheet({ title: t('ps_title', { t: p.title }), body, wide: true,
              foot: [btn('ghost-btn', t('close'), closeSheet)] });
}

/* ── trash ────────────────────────────────────────────────────────── */
async function openTrash() {
  const { projects } = await api('/api/trash');
  const list = el('div', 'rows');
  if (!projects.length) list.append(el('p', 'empty', t('tr_empty')));
  for (const p of projects) {
    const r = el('div', 'item-row');
    const main = el('div', 'item-main person-main');
    const bead = el('span', 'bead small', initials(p.title)); bead.style.setProperty('--c', p.colour);
    const nm = el('div');
    nm.append(el('b', null, p.title), el('span', 'item-sub', t('tr_purge_on', { d: fmtDay(p.purge_at) })));
    main.append(bead, nm);
    r.append(main, btn('ghost-btn small', t('tr_restore'), async () => {
      await api(`/api/projects/${encodeURIComponent(p.slug)}/restore`, { method: 'POST' });
      closeSheet();
      await refreshMe();
      selectProject(p.slug);
      toast(t('t_restored', { n: p.title }));
    }));
    list.append(r);
  }
  openSheet({ title: t('tr_title'), body: [el('p', 'sheet-text', t('tr_help')), list],
              foot: [btn('ghost-btn', t('close'), closeSheet)] });
}

/* ── change password ──────────────────────────────────────────────── */
function openChangePassword() {
  const pw = () => { const i = el('input'); i.type = 'password'; return i; };
  const current = pw(), next = pw(), again = pw();
  current.autocomplete = 'current-password';
  next.autocomplete = again.autocomplete = 'new-password';
  const err = el('p', 'login-err'); err.hidden = true;
  const save = async () => {
    if (next.value !== again.value) { err.textContent = t('pw_mismatch'); err.hidden = false; return; }
    try {
      await api('/api/account/password', { method: 'POST', body: { current: current.value, new: next.value } });
      closeSheet();
      toast(t('pw_done'));
    } catch (e) { err.textContent = errMsg(e); err.hidden = false; }
  };
  again.addEventListener('keydown', ev => { if (ev.key === 'Enter') save(); });
  openSheet({
    title: t('pw_title'),
    body: [field(t('pw_current'), current), field(t('pw_new'), next), field(t('pw_repeat'), again),
           el('p', 'sheet-note', t('pw_note')), err],
    foot: [btn('ghost-btn', t('cancel'), closeSheet), btn('primary-btn', t('save'), save)]
  });
  setTimeout(() => current.focus(), 0);
}

/* ── delete account ───────────────────────────────────────────────── */
async function deleteAccount() {
  const err = el('p', 'login-err'); err.hidden = true;
  const go = async () => {
    try {
      await api('/api/account', { method: 'DELETE' });
      closeSheet();
      history.replaceState(null, '', '/');
      showLogin();
    } catch (e) { err.textContent = errMsg(e); err.hidden = false; }
  };
  openSheet({
    title: t('da_title'),
    body: [el('p', 'sheet-text', t('da_body')), err],
    foot: [btn('ghost-btn', t('cancel'), closeSheet), btn('danger-btn', t('da_confirm'), go)]
  });
}

/* ── shell ────────────────────────────────────────────────────────── */
function applyStrings() {
  document.documentElement.lang = LANG;
  $('#loginTitle').textContent = t('login_title');
  $('#loginHelp').textContent = t('login_help');
  $('#googleLabel').textContent = t('google_btn');
  $('#googleOr').querySelector('span').textContent = t('or_google');
  $('#authTabs').querySelector('[data-mode=login]').textContent = t('tab_login');
  $('#authTabs').querySelector('[data-mode=signup]').textContent = t('tab_signup');
  $('#userLabel').textContent = t('user_label');
  $('#passLabel').textContent = t('pass_label');
  $('#authHint').textContent = t('signup_hint');
  $('#authGo').textContent = t(authMode === 'login' ? 'login_go' : 'signup_go');
  $('#q').placeholder = t('search');
  $('#noResults').textContent = t('no_results');
  $('#collapseBtn').title = S.collapsed ? t('expand') : t('collapse');
  $('#newProjectBtn').querySelector('span').textContent = t('new_project');
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
  $('#emptyMsg').textContent = t('no_projects');
  $('#emptyNew').textContent = t('empty_new');
}

function renderTop() {
  const p = project(), st = S.state;
  $('#wsDot').style.setProperty('--c', p ? p.colour : '#555');
  $('#wsName').textContent = p ? p.title : '';
  $('#wsMeta').textContent = st
    ? t('meta', { e: st.events.length, s: st.sections.length, m: st.members_active_today })
    : (p ? t('loading') : '');
  // No project, nothing to be live about: an empty bar beats a green dot that lies.
  document.querySelector('.ws').hidden = !p;
  $('#linkBtn').hidden = !p;
  $('#settingsBtn').hidden = !p;
  for (const b of $('#viewSwitch').children) b.classList.toggle('on', b.dataset.view === S.view);
  for (const b of $('#dimSwitch').children) b.classList.toggle('on', +b.dataset.dim === S.dim);
  $('#viewSwitch').hidden = !p;
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
      applyStrings();
      if (S.me) { renderSidebar(); renderTop(); }
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
  try { S.config = await (await fetch('/api/config')).json(); } catch (e) { /* offline */ }

  const join = location.pathname.match(/^\/join\/([^/]+)/);
  if (join) return showJoin(decodeURIComponent(join[1]));

  let me;
  try { me = await api('/api/me'); } catch (e) { return; }
  S.me = me;
  $('#login').hidden = true; $('#join').hidden = true;
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

  $('#authForm').addEventListener('submit', submitAuth);
  $('#authTabs').addEventListener('click', ev => {
    const b = ev.target.closest('[data-mode]'); if (b) setAuthMode(b.dataset.mode);
  });

  $('#collapseBtn').addEventListener('click', () => {
    S.collapsed = !S.collapsed;
    LS.set('sc.collapsed', S.collapsed ? '1' : '0');
    document.getElementById('app').classList.toggle('collapsed', S.collapsed);
    $('#collapseBtn').title = S.collapsed ? t('expand') : t('collapse');
  });
  $('#q').addEventListener('input', ev => { S.query = ev.target.value; renderSidebar(); });

  $('#newProjectBtn').addEventListener('click', openNewProject);
  $('#emptyNew').addEventListener('click', openNewProject);
  $('#linkBtn').addEventListener('click', openLink);
  $('#settingsBtn').addEventListener('click', openSettings);

  $('#accountBtn').addEventListener('click', ev => {
    ev.stopPropagation();
    const m = $('#accountMenu');
    if (m.hidden) { renderAccountMenu(); m.hidden = false; $('#langMenu').hidden = true; } else m.hidden = true;
  });

  $('#sheetClose').addEventListener('click', closeSheet);
  $('#sheetModal').addEventListener('click', ev => { if (ev.target.id === 'sheetModal') closeSheet(); });

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
    if (m.hidden) { renderLangMenu(); m.hidden = false; $('#accountMenu').hidden = true; } else m.hidden = true;
  });
  document.addEventListener('click', () => { $('#langMenu').hidden = true; $('#accountMenu').hidden = true; });
  for (const id of ['#langMenu', '#accountMenu']) $(id).addEventListener('click', ev => ev.stopPropagation());

  document.addEventListener('keydown', ev => {
    if (ev.key !== 'Escape') return;
    if (!$('#sheetModal').hidden) closeSheet();
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
