/* Lattix Locus prototype shell — builds the shared navigation, context bar,
   command palette, approval inbox and notification drawer around each page's
   <main>. Sample data only; the real UI reads everything from lib/api.ts. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const store = {
    get: (k, d) => { try { return localStorage.getItem('locus.' + k) ?? d; } catch { return d; } },
    set: (k, v) => { try { localStorage.setItem('locus.' + k, v); } catch {} }
  };

  const I = {
    home: '<path d="M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z"/>',
    work: '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 9h18M8 4v16"/>',
    activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    library: '<path d="M4 4h4v16H4zM10 4h4v16h-4zM16 5l4 1-3 14-4-1z"/>',
    memory: '<path d="M12 3v18M5 7c0-2 3-4 7-4s7 2 7 4-3 4-7 4-7-2-7-4zM5 7v10c0 2 3 4 7 4s7-2 7-4V7M5 12c0 2 3 4 7 4s7-2 7-4"/>',
    tower: '<path d="M12 2v4M8 22l4-16 4 16M6 14h12M9.5 10h5M4 6a11 11 0 0 1 16 0"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    inbox: '<path d="M3 13h5l1 3h6l1-3h5M5 4h14l2 9v7H3v-7z"/>',
    bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 21h4"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5"/>',
    panel: '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M9 4v16"/>',
    assist: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 10h8"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    shield: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/>',
    alert: '<path d="M12 3 2 20h20zM12 10v4M12 17v.5"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    check: '<path d="m5 12 5 5 9-10"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    pause: '<path d="M8 5v14M16 5v14"/>',
    play: '<path d="M7 4v16l13-8z"/>',
    stop: '<rect x="6" y="6" width="12" height="12"/>',
    file: '<path d="M6 3h8l4 4v14H6zM14 3v4h4"/>',
    filter: '<path d="M3 5h18l-7 8v6l-4 2v-8z"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    branch: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10M18 10c0 4-6 3-12 7"/>',
    node: '<rect x="4" y="4" width="16" height="6" rx="1"/><rect x="4" y="14" width="16" height="6" rx="1"/><path d="M8 7h.01M8 17h.01"/>',
    attach: '<path d="M20 11 12 19a5 5 0 0 1-7-7l8-8a3.5 3.5 0 0 1 5 5l-8 8a2 2 0 0 1-3-3l7-7"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="1"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>'
  };
  const icon = (n) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter" aria-hidden="true">${I[n] || ''}</svg>`;

  const NAV = [
    { k: 'home', label: 'Home', href: 'home.html', icon: 'home' },
    { k: 'work', label: 'Work', href: 'work.html', icon: 'work', sub: [['Tasks', 'work.html'], ['Projects', 'work-projects.html'], ['Schedules', 'work-schedules.html'], ['Conversations', 'work-conversations.html']] },
    { k: 'activity', label: 'Activity', href: 'activity.html', icon: 'activity', sub: [['Runs', 'activity.html'], ['Approvals', 'activity.html#approvals'], ['Artifacts', 'activity.html#artifacts']] },
    { k: 'library', label: 'Library', href: 'library.html', icon: 'library', sub: [['Agents & teams', 'library.html'], ['Execution engines', 'library.html#engines'], ['Workflows', 'library.html#workflows'], ['Tools & MCP', 'library.html#mcp'], ['A2A peers', 'library.html#a2a'], ['Skills & mods', 'library.html#skills']] },
    { k: 'memory', label: 'Memory', href: 'memory.html', icon: 'memory', sub: [['Records', 'memory.html'], ['Contradictions', 'memory.html#contradictions'], ['Governance', 'memory.html#governance']] },
    { k: 'tower', label: 'Control Tower', short: 'Tower', href: 'control-tower.html', icon: 'tower', sub: [['Fleet', 'control-tower.html'], ['Model routing', 'control-tower.html#routing'], ['Policy operations', 'control-tower.html#policy'], ['Evaluations', 'control-tower.html#evals']] },
    { k: 'settings', label: 'Settings', href: 'settings.html', icon: 'settings' }
  ];

  const LS = window.LocusState; const D = LS ? LS.H : null;
  const pendingApr = () => D ? D.pending() : [];
  function inboxHtml() { const ps = pendingApr(); return ps.map(a => `
          <li data-apr="${D.esc(a.id)}"><span class="st ${D.esc(a.risk === 'crit' ? 'st-crit' : 'st-warn')}" aria-label="${D.esc(a.risk === 'crit' ? 'High impact' : 'Needs review')}"></span>
            <div class="li-b"><b>${D.esc(a.title)}</b><p class="small muted" style="margin-top:2px">${D.esc(a.why)} · ${D.esc(a.rule)}</p>
              <div class="row small" style="margin-top:6px;flex-wrap:wrap"><span class="id">${D.esc(a.id)}</span><span class="muted">·</span><span class="small">${D.esc(a.by)}</span><span class="muted">·</span><a class="id" href="activity-run.html?run=${D.esc(a.run)}">${D.esc(a.run)}</a><span class="muted">·</span><span class="small" data-exp="${D.esc(a.id)}">expires in ${D.left(a.expires)}</span>${a.stepUp ? '<span class="tag">Step-up</span>' : ''}</div>
              <div class="row" style="margin-top:10px"><button class="btn btn-sm btn-primary" data-review="${D.esc(a.id)}">Review decision</button><a class="btn btn-sm btn-ghost" href="activity-run.html?run=${D.esc(a.run)}#approvals">Open run</a></div></div></li>`).join(''); }
  const NOTES = [
    { st: 'st-ok', t: 'Quarterly vendor review — draft complete', d: 'RUN-48188 produced 2 artifacts', age: '12 min' },
    { st: 'st-crit', t: 'Node edge-sfo-02 missed 3 heartbeats', d: 'Runs rescheduled to edge-sfo-01', age: '26 min' },
    { st: 'st-warn', t: 'Memory contradiction detected', d: 'MEM-7781 conflicts with MEM-6620', age: '1 h' },
    { st: 'st-sim', t: 'Improvement proposal ready for review', d: 'IMP-114 · citation formatting', age: '3 h' }
  ];
  const PALETTE = [
    ['Screens', 'Home', 'home.html', ''], ['Screens', 'Work · tasks', 'work.html', ''], ['Screens', 'Work · projects', 'work-projects.html', ''], ['Screens', 'Work · schedules', 'work-schedules.html', ''], ['Screens', 'Work · conversations', 'work-conversations.html', ''], ['Screens', 'Activity · runs', 'activity.html', ''], ['Screens', 'Screen registry & scorecard', 'screen-registry.html', ''],
    ['Screens', 'Library · agents & teams', 'library.html', ''], ['Screens', 'Memory · records', 'memory.html', ''], ['Screens', 'Control Tower · fleet', 'control-tower.html', ''],
    ['Screens', 'Settings', 'settings.html', ''], ['Screens', 'Sign in & onboarding', 'auth.html', ''], ['Screens', 'Screen inventory & traceability', 'index.html', ''],
    ['Recent', 'Vendor risk review — Q4', 'task.html?task=TSK-3318', 'TSK-3318'], ['Recent', 'Run · vendor risk summary', 'activity-run.html?run=RUN-48213', 'RUN-48213'],
    ['Recent', 'Research Analyst · agent builder', 'library-agent.html?agent=AGT-0042', 'AGT-0042'], ['Recent', 'Vendor Assessment · team composer', 'library-team.html?team=TEAM-07', 'TEAM-07'], ['Actions', 'Account and session', '#acct', ''], ['Recent', 'Procurement policy collection', 'memory.html', 'COL-019'],
    ['Actions', 'New task', 'home.html#compose', 'N'], ['Actions', 'Open approval inbox', '#inbox', 'I'], ['Actions', 'Toggle theme', '#theme', ''], ['Actions', 'Reset prototype sample data', '#reset', '']
  ];

  function subCur(sub, i) {
    const file = location.pathname.split('/').pop() || 'index.html';
    const hash = location.hash;
    const m = sub.findIndex(s => s[1].includes('#') ? s[1] === file + hash : s[1] === file && !(hash && sub.some(t => t[1] === file + hash)));
    return m === i;
  }
  function build() {
    const body = document.body;
    const main = $('main');
    const page = body.dataset.nav;
    const crumbs = JSON.parse(body.dataset.crumbs || '[]');
    if (store.get('theme', 'light') === 'dark') document.documentElement.dataset.theme = 'dark';

    const app = document.createElement('div');
    app.className = 'app' + (store.get('nav', '') === 'collapsed' ? ' nav-collapsed' : '');
    app.innerHTML = `
      <div class="mode-band" role="presentation" data-od-id="mode-band"></div>
      <div class="proto-bar" data-od-id="prototype-bar"><span>Locus target-state prototype</span><span class="hide-sm">Sample data · not connected to the Locus backend · v${(window.LocusBuild || {}).version || ''}</span><span class="sp"></span>
        <span class="trc trc-ex">Existing</span><span class="trc trc-fd">Foundation</span><span class="trc trc-pl">Planned</span><a href="index.html" class="hide-sm">Screen inventory</a></div>
      <nav class="nav" aria-label="Primary" data-od-id="left-nav">
        <a class="nav-brand" href="home.html" aria-label="Locus home"><img class="mark mark-l" src="assets/brand/locus-icon-light.svg" alt=""><img class="mark mark-d" src="assets/brand/locus-icon-dark.svg" alt=""><span>Locus</span></a>
        <button class="nav-ws" type="button" data-od-id="workspace-selector" aria-label="Switch organization and workspace"><span class="ws-av">NW</span><span class="ws-t"><b>Northwind · Operations</b><span>tenant nw-prod</span></span></button>
        <ul class="nav-list">${NAV.map(n => `
          <li><a class="nav-item" href="${D.esc(n.href)}" ${n.k === page ? 'aria-current="page"' : ''} title="${D.esc(n.label)}">${icon(n.icon)}<span>${n.short ? `<span class="nl-l">${D.esc(n.label)}</span><span class="nl-s" aria-hidden="true">${D.esc(n.short)}</span>` : n.label}</span>${n.k === 'activity' ? `<span class="count" id="navAprCount" aria-label="${D.esc(pendingApr().length)} awaiting decision" ${D.esc(pendingApr().length ? '' : 'hidden')}>${D.esc(pendingApr().length)}</span>` : ''}</a>
          ${n.k === page && n.sub ? `<ul class="nav-sub">${n.sub.map((s, i) => `<li><a href="${D.esc(s[1])}" ${subCur(n.sub, i) ? 'aria-current="page"' : ''}>${D.esc(s[0])}</a></li>`).join('')}</ul>` : ''}</li>`).join('')}
          <li class="nav-sect">Favorites</li>
          <li class="nav-fav"><a class="nav-item" href="task.html?task=TSK-3318" title="Vendor risk review">${icon('star')}<span>Vendor risk review</span></a></li>
          <li class="nav-fav"><a class="nav-item" href="library-agent.html?agent=AGT-0042" title="Research Analyst">${icon('star')}<span>Research Analyst</span></a></li>
        </ul>
        <div class="nav-foot"><button class="nav-item" style="border:0;background:none;width:100%" id="navToggle" type="button" aria-label="Collapse navigation" title="Collapse navigation ( [ )">${icon('panel')}<span>Collapse</span></button></div>
      </nav>
      <header class="top" data-od-id="context-bar">
        <nav class="crumbs" aria-label="Breadcrumb">${crumbs.map((c, i) => i === crumbs.length - 1 ? `<span class="cur">${D.esc(c[0])}</span>` : `<a href="${D.esc(c[1])}">${D.esc(c[0])}</a><span class="sep">/</span>`).join('')}</nav>
        <span class="sp"></span>
        <button class="search-trigger" id="openPalette" type="button" data-od-id="command-search">${icon('search')}<span class="st-label">Search or run a command</span><span class="kbd">Ctrl K</span></button>
        <span class="mode-pill" role="status" title="Operating mode: Simulation. No enforcement or equipment connection; all records are sample data." data-od-id="operating-mode">Simulation<span class="mp-long"> · no enforcement</span></span>
        <span id="prioCounts" data-od-id="priority-counts" style="display:inline-flex;gap:4px"></span>
        <button class="runtime" id="runtimeBtn" type="button" data-od-id="runtime-status" aria-haspopup="true"><span class="st st-ok" id="rtSt"></span><span class="rt-text" id="rtText">Hosted · nw-prod</span></button>
        <button class="icon-btn" id="openInbox" type="button" aria-label="Approval inbox, ${D.esc(pendingApr().length)} pending" title="Approvals ( I )" data-od-id="approval-inbox-trigger">${icon('inbox')}<span class="pip" id="aprPip" ${D.esc(pendingApr().length ? '' : 'hidden')}>${D.esc(pendingApr().length)}</span></button>
        <button class="icon-btn" id="openNotes" type="button" aria-label="Notifications, ${NOTES.length} unread" data-od-id="notifications-trigger">${icon('bell')}<span class="pip">${NOTES.length}</span></button>
        <button class="icon-btn" id="openAssist" type="button" aria-label="Open Locus assistant" title="Locus assistant">${icon('assist')}</button>
        <button class="icon-btn" id="themeBtn" type="button" aria-label="Toggle dark and light theme">${icon('sun')}</button>
        <button class="avatar" id="openAcct" type="button" aria-label="Account and session: Dana Reyes" style="border-radius:50%" data-od-id="account-trigger">DR</button>
      </header>`;
    body.insertBefore(app, main);
    main.classList.add('main');
    app.appendChild(main);

    const ov = document.createElement('div');
    ov.innerHTML = `
      <div class="scrim" id="scrim"></div>
      <aside class="drawer" id="inbox" aria-label="Approval and intervention inbox" data-od-id="approval-inbox">
        <div class="drawer-h"><h2>Decisions waiting on you</h2><span class="tag" id="aprTag">${D.esc(pendingApr().length)} pending</span><button class="icon-btn" data-close aria-label="Close">${icon('x')}</button></div>
        <div class="drawer-b"><ul class="list" id="inboxList">${inboxHtml()}</ul>
          <div class="empty" id="inboxEmpty" ${pendingApr().length ? "hidden" : ""}>No decisions are waiting. Approvals appear here when policy requires a human.</div></div>
      </aside>
      <aside class="drawer" id="notes" aria-label="Notifications">
        <div class="drawer-h"><h2>Notifications</h2><button class="btn btn-sm btn-ghost" id="markRead">Mark all read</button><button class="icon-btn" data-close aria-label="Close">${icon('x')}</button></div>
        <div class="drawer-b"><ul class="list">${NOTES.map(n => `<li><span class="st ${D.esc(n.st)}"></span><div class="li-b"><b>${D.esc(n.t)}</b><span class="small muted">${D.esc(n.d)}</span></div><span class="id">${D.esc(n.age)}</span></li>`).join('')}</ul></div>
      </aside>
      <aside class="drawer" id="assist" aria-label="Locus assistant">
        <div class="drawer-h"><h2>Locus assistant</h2><span class="tag">Context: ${D.esc(crumbs.length ? crumbs[crumbs.length - 1][0] : 'page')}</span><button class="icon-btn" data-close aria-label="Close">${icon('x')}</button></div>
        <div class="drawer-b" style="padding:16px;display:flex;flex-direction:column;gap:12px">
          <p class="small muted">Answers questions about what is on screen. It reads with your permissions and cannot act without the same approvals as any agent.</p>
          <div class="panel panel-s"><div class="panel-b small">Why is RUN-48213 waiting?</div></div>
          <div class="panel"><div class="panel-b small">The run drafted a vendor risk summary and attempted to email it to an external domain. Policy <span class="ent">POL-egress-07</span> requires a human decision for CONFIDENTIAL content leaving the tenant. Approval <span class="ent">APR-2291</span> is in your inbox.</div></div>
          <textarea class="textarea" rows="3" placeholder="Ask about this page…" aria-label="Ask the assistant"></textarea>
          <span class="trc trc-pl">Planned capability</span>
        </div>
      </aside>
      <aside class="drawer" id="acct" aria-label="Account and session" data-od-id="session-panel">
        <div class="drawer-h"><h2>Account and session</h2><button class="icon-btn" data-close aria-label="Close">${icon('x')}</button></div>
        <div class="drawer-b" style="padding:16px" id="acctBody"></div>
      </aside>
      <div class="dialog dialog-lg" id="decDlg" role="dialog" aria-labelledby="decH" data-od-id="decision-review">
        <div class="dialog-h"><h2 id="decH" tabindex="-1" autofocus>Review decision</h2><span class="sp" style="flex:1"></span><button class="icon-btn" data-close aria-label="Close">${icon('x')}</button></div>
        <div class="dialog-b" id="decBody"></div>
        <div class="dialog-f" id="decFoot"></div>
      </div>
      <div class="palette" id="palette" role="dialog" aria-label="Command search">
        <input id="palInput" placeholder="Search tasks, runs, agents, memory, settings…" autocomplete="off" aria-label="Search">
        <ul id="palList"></ul>
      </div>
      <div class="toasts" id="toasts" aria-live="polite"></div>`;
    body.appendChild(ov);
    // page-level dialogs and drawers must sit outside .app: opening one makes .app inert, which would otherwise disable the dialog itself
    $$('.dialog, .drawer', main).forEach(d => body.insertBefore(d, ov));
  }

  /* ── decision review: the only path to approve or deny. It shows exactly what is being authorized,
        binds the decision to that request's fingerprint, and asks for step-up when policy requires it. ── */
  let decId = null, decFp = null, decMsg = '', stepping = false;
  const sessLine = () => { const ss = D.s.session, st = D.sessionState(); return st === 'active' ? `Tenant session ${D.esc(ss.tenant.sid)} · ${D.esc(ss.tenant.amr.join(' + '))} · expires in ${D.left(ss.tenant.expires)}` : st === 'expired' ? 'Tenant session expired' : 'Signed out of Northwind'; };
  function decHtml(a) { const ag = D.agent(a.agent), cap = D.CAPS[a.action] || {label:a.action, via:'—'}, e = D.esc, fresh = D.stepUpFresh(), st = D.sessionState(), pol = a.policy || {bundle:'unknown'};
    const stTag = a.status === 'pending' ? `<span class="st ${D.esc(a.risk === 'crit' ? 'st-crit' : 'st-warn')}">${D.esc(a.risk === 'crit' ? 'High impact' : 'Needs decision')}</span>` : `<span class="st st-idle">${e(String(a.status).replace(/^./, c => c.toUpperCase()))}</span>`;
    return `<div class="row" style="flex-wrap:wrap;margin-bottom:12px">${D.esc(stTag)}<span class="cls">${e(a.cls)}</span><span class="id">${e(a.id)} · ${e(a.fp || 'no fingerprint')}</span></div>
      ${decMsg ? `<div class="callout warn" style="margin-bottom:12px"><span>${icon('alert')}</span><p class="small">${e(decMsg)}</p></div>` : ''}
      <p class="dec-act"><b>${e(a.title)}</b></p>
      <dl class="kv dec-kv">
        <dt>Requested by</dt><dd>${e(a.by)} <span class="muted">for</span> ${e(D.nameOf(a.onBehalf))} · task <a href="task.html?task=${encodeURIComponent(a.task)}">${e(a.task)}</a> · run <a href="activity-run.html?run=${encodeURIComponent(a.run)}">${e(a.run)}</a></dd>
        <dt>Executes as</dt><dd>${e(ag ? ag.name : a.agent)} (${e(a.agent)}) on ${e(a.executor ? a.executor.engine : '—')} · node ${e(a.executor ? a.executor.node : '—')}</dd>
        <dt>Exact action</dt><dd>${e(cap.label)} <span class="mono small">${e(a.action)}</span> via ${e(cap.via)}</dd>
        <dt>Destination</dt><dd><span class="mono">${e(a.resource)}</span> · <b>${e(a.egress || '—')}</b></dd>
        <dt>Data released</dt><dd>${e(a.data || '—')}${(a.items || []).length ? `<ul class="dec-items">${a.items.map(x => `<li><span class="mono">${e(x.id)}</span> ${e(x.title)} <span class="id">${e(x.hash)}</span></li>`).join('')}</ul>` : ''}<span class="small muted" style="display:block;margin-top:4px">Classification ${e(a.cls)} · source <span class="mono">${e(a.ns)}</span></span></dd>
        <dt>Authority used</dt><dd>Grant <span class="mono">${e(a.grant || '—')}</span>, once. Approving does not widen this or any other grant, and does not approve later sends.</dd>
        <dt>Expires</dt><dd>${a.status === 'pending' && a.expires ? `<span data-exp="${e(a.id)}">in ${D.left(a.expires)}</span> · at ${D.hm(a.expires).slice(0, 5)}. After that the run stays blocked and nothing is sent.` : a.status === 'pending' ? 'No expiry recorded: treat as invalid' : 'Not pending'}</dd>
        <dt>Policy</dt><dd><span class="mono">${e(a.rule)}</span> in bundle ${e(pol.bundle)} (${e(D.POLICY.published)}) · ${e(a.why)}</dd>
        <dt>If approved</dt><dd>${e(a.onApprove)}</dd><dt>If denied</dt><dd>${e(a.onDeny)}</dd>
      </dl>
      <div class="panel panel-s dec-sess"><div class="panel-b small"><p class="label" style="margin-bottom:6px">Your authority to decide</p>
        <ul class="dec-checks">
          <li><span class="st st-ok"></span>Device: ${e(D.s.session.device.method)} · ${e(D.s.session.device.id)}. Proves who is at this device only.</li>
          <li><span class="st ${D.esc(st === 'active' ? 'st-ok' : 'st-crit')}"></span>${sessLine()}</li>
          <li><span class="st ${D.esc(!a.stepUp ? 'st-idle' : fresh ? 'st-ok' : 'st-warn')}"></span>${!a.stepUp ? 'Step-up not required for this action' : fresh ? `Step-up verified ${D.ago(D.s.session.stepUp.at)} · ${e(D.s.session.stepUp.method)} · valid 5 min` : 'Step-up required: confirm it is you within the last 5 minutes'}</li>
          <li><span class="st st-ok"></span>Approver role: task owner (${e(D.nameOf(a.onBehalf))})</li>
        </ul></div></div>
      ${a.status === 'pending' ? `<div class="field" style="margin-top:12px"><label for="decNote">Note for the audit record (optional)</label><textarea id="decNote" class="textarea" rows="2"></textarea></div>` : a.decision ? `<p class="small" style="margin-top:12px">Decided by ${e(D.nameOf(a.decision.by))} at ${D.hm(a.decision.at)} · step-up ${e(a.decision.stepUp)} · session ${e(a.decision.session || '—')}</p>` : ''}`; }
  function decFootHtml(a) { const st = D.sessionState(), fresh = D.stepUpFresh();
    if (a.status === 'expired') return `<span class="small muted" style="flex:1">This request expired. Nothing was sent.</span><button class="btn" data-close>Close</button>${a.replacedBy ? '' : '<button class="btn btn-primary" data-dec-act="rerequest">Ask the agent to request again</button>'}`;
    if (a.status !== 'pending') return `<button class="btn" data-close>Close</button>`;
    if (st !== 'active') return `<span class="small muted" style="flex:1">Sign in to Northwind again to decide. Device sign-in alone is not enough.</span><button class="btn" data-close>Close</button><a class="btn btn-primary" href="auth.html?reauth=1&return=${encodeURIComponent(location.pathname.split('/').pop() + location.search)}">Sign in again</a>`;
    const needStep = a.stepUp && !fresh;
    return `<span class="small muted dec-fp">Your decision is recorded against ${D.esc(a.fp)}.</span><button class="btn" data-dec-act="deny">Deny</button>${needStep ? `<button class="btn btn-primary" data-dec-act="stepup" ${stepping ? 'disabled' : ''}>${D.esc(stepping ? 'Waiting for Windows Hello…' : 'Confirm it is you to approve')}</button>` : `<button class="btn btn-primary" data-dec-act="approve">Approve this send once</button>`}`; }
  function renderDec() { if (decId == null) return; const a = D.apr(decId); if (!a) return;
    if (decFp && a.fp !== decFp && a.status === 'pending') { decMsg = 'This request changed while you were reviewing it. Check the details again before deciding.'; decFp = a.fp; }
    D.setH($('#decBody'), decHtml(a)); D.setH($('#decFoot'), decFootHtml(a).replace('Approve this send once', a.action === 'email.send' ? 'Approve this send once' : 'Approve this action once'));
    $$('#decFoot [data-close]').forEach(b => b.onclick = closeAll); }
  function review(id) { if (!D || !D.apr(id)) return; decId = id; decFp = D.apr(id).fp; decMsg = ''; stepping = false; renderDec(); open($('#decDlg')); }
  function decAct(act) { const a = D.apr(decId); if (!a) return; const note = ($('#decNote') || {}).value || '';
    if (act === 'rerequest') { const n = LS.A.rerequest(a.id); if (n) { toast('Requested again with a new expiry', n); decId = n; decFp = D.apr(n).fp; decMsg = ''; renderDec(); } return; }
    if (act === 'stepup') { stepping = true; renderDec(); setTimeout(() => { LS.A.stepUp('Passkey · Windows Hello'); stepping = false; decMsg = ''; renderDec(); const b = $('#decFoot [data-dec-act="approve"]'); if (b) b.focus(); }, 900); return; }
    const res = LS.A.decide(a.id, act, note.trim(), decFp) || {};
    if (res.ok) { closeAll(); toast(act === 'approve' ? 'Approved once. Recorded with the request fingerprint and your step-up.' : 'Denied. The agent continues without this action.', a.id); return; }
    decMsg = res.error || 'The decision could not be recorded.'; if (res.changed) decFp = D.apr(a.id).fp; renderDec(); }
  function acctHtml() { const ss = D.s.session, st = D.sessionState(), e = D.esc;
    return `<p class="small muted" style="margin-bottom:12px">Three separate facts decide what you can do. Each is checked by the server, not by this app.</p>
      <div class="sess-card"><p class="label">1 · Verified local identity</p><p><b>${e(D.nameOf(ss.user))}</b> on ${e(ss.device.id)}</p><p class="small muted">${e(ss.device.method)} · verified ${D.ago(ss.device.at)}. Unlocks this device and its local keys. It does not sign you in to Northwind.</p></div>
      <div class="sess-card"><p class="label">2 · Tenant session</p>${st === 'active' ? `<p><b>${e(ss.tenant.name)}</b> <span class="st st-ok">Active</span></p><p class="small muted">${e(ss.tenant.idp)} · ${e(ss.tenant.amr.join(' + '))} · session ${e(ss.tenant.sid)}<br>Issued ${D.hm(ss.tenant.issued).slice(0, 5)} · expires in ${D.left(ss.tenant.expires)} · ends after 30 min idle</p>` : `<p><span class="st st-crit">${D.esc(st === 'expired' ? 'Expired' : 'Signed out')}</span></p><p class="small muted">Runs you started keep their own grants and continue. You cannot decide approvals or start work until you sign in again.</p><a class="btn btn-sm btn-primary" style="margin-top:8px" href="auth.html?reauth=1">Sign in again</a>`}</div>
      <div class="sess-card"><p class="label">3 · Step-up for consequential decisions</p>${D.stepUpFresh() ? `<p><span class="st st-ok">Fresh</span> ${e(ss.stepUp.method)} · ${D.ago(ss.stepUp.at)}</p>` : '<p><span class="st st-idle">Not fresh</span></p>'}<p class="small muted">Approving external sends, production changes or Confidential data asks you to confirm again. Valid 5 minutes, for decisions only.</p></div>
      <div class="row" style="margin-top:16px;flex-wrap:wrap"><button class="btn btn-sm" data-sess="expire" data-sim>Simulate session expiry</button><button class="btn btn-sm btn-ghost" data-sess="signout">Sign out of Northwind</button></div>
      <div class="acct-more"><p class="label" style="margin:20px 0 8px">On this device</p><div class="row" style="flex-wrap:wrap"><button class="btn btn-sm" data-sess="theme">Switch theme</button><button class="btn btn-sm" data-sess="assist">Open Locus assistant</button><span class="small muted">Runtime: ${D.esc(($('#rtText') || {}).textContent || '')}</span></div></div>`; }
  function renderAcct() { if (D) D.setH($('#acctBody'), acctHtml()); }

  /* toasts are plain text: callers pass raw strings, never markup */
  const txt = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;'}[c]));
  function toast(msg, id) {
    const t = document.createElement('div');
    t.className = 'toast';
    t.innerHTML = `<span style="flex:1">${txt(msg)}</span>${id ? `<span class="id">${txt(id)}</span>` : ''}`;
    $('#toasts').appendChild(t);
    setTimeout(() => t.remove(), 3600);
  }

  let openEl = null, lastFocus = null;
  const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  function open(el) { if (!el) return; const prev = document.activeElement; closeAll(true); lastFocus = prev; el.classList.add('open'); el.setAttribute('aria-modal', 'true'); if (!el.getAttribute('role')) el.setAttribute('role', 'dialog'); $('#scrim').classList.add('open'); openEl = el;
    $$('.app').forEach(a => a.setAttribute('inert', '')); const f = el.querySelector('[autofocus]') || el.querySelector('input,textarea,select') || el.querySelector(FOCUSABLE); if (f) setTimeout(() => f.focus(), 60); }
  function closeAll(keepFocus) { const was = openEl; if (was && was.id === 'decDlg') decId = null; $$('.drawer.open,.palette.open,.dialog.open').forEach(e => { e.classList.remove('open'); e.removeAttribute('aria-modal'); }); $('#scrim').classList.remove('open'); openEl = null; $$('.app').forEach(a => a.removeAttribute('inert'));
    if (was && keepFocus !== true && lastFocus && document.contains(lastFocus)) { const lf = lastFocus; setTimeout(() => lf.focus(), 0); } }
  document.addEventListener('keydown', e => { if (e.key !== 'Tab' || !openEl) return; const f = $$(FOCUSABLE, openEl).filter(x => x.offsetParent !== null || x === document.activeElement); if (!f.length) return; const i = f.indexOf(document.activeElement);
    if (e.shiftKey && (i <= 0)) { e.preventDefault(); f[f.length - 1].focus(); } else if (!e.shiftKey && (i === f.length - 1 || i === -1)) { e.preventDefault(); f[0].focus(); } });

  function renderPalette(q) {
    const items = PALETTE.filter(p => !q || (p[1] + ' ' + p[3]).toLowerCase().includes(q.toLowerCase()));
    let grp = '', html = '';
    items.forEach((p, i) => { if (p[0] !== grp) { grp = p[0]; html += `<li class="grp">${D.esc(grp)}</li>`; } html += `<li><a href="${D.esc(p[2])}" class="${D.esc(i === 0 ? 'is-act' : '')}">${D.esc(p[1])}<span class="id">${D.esc(p[3])}</span></a></li>`; });
    $('#palList').innerHTML = html || '<li class="empty">No matches. Try a task name, run ID or agent.</li>';
  }

  function toggleTheme() {
    const d = document.documentElement.dataset.theme === 'dark';
    if (d) delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = 'dark';
    store.set('theme', d ? 'light' : 'dark');
  }

  function wire() {
    $('#navToggle').onclick = () => { const a = $('.app'); a.classList.toggle('nav-collapsed'); store.set('nav', a.classList.contains('nav-collapsed') ? 'collapsed' : ''); };
    $('#openPalette').onclick = () => { renderPalette(''); $('#palInput').value = ''; open($('#palette')); };
    $('#palInput').oninput = e => renderPalette(e.target.value);
    $('#palInput').onkeydown = e => { if (e.key === 'Enter') { const a = $('#palList a.is-act') || $('#palList a'); if (a) a.click(); } };
    $('#palList').onclick = e => { const a = e.target.closest('a'); if (!a) return; const h = a.getAttribute('href'); if (h === '#inbox') { e.preventDefault(); open($('#inbox')); } else if (h === '#acct') { e.preventDefault(); renderAcct(); open($('#acct')); } else if (h === '#theme') { e.preventDefault(); toggleTheme(); closeAll(); } else if (h === '#reset') { e.preventDefault(); closeAll(); if (LS) { LS.A.reset(); toast('Sample data reset. All prototype state returned to its starting point.'); } } };
    $('#openInbox').onclick = () => open($('#inbox'));
    $('#openAcct').onclick = () => { renderAcct(); open($('#acct')); };
    $('#acctBody').onclick = e => { const b = e.target.closest('[data-sess]'); if (!b) return; if (b.dataset.sess === 'theme') { toggleTheme(); return; } if (b.dataset.sess === 'assist') { open($('#assist')); return; } if (b.dataset.sess === 'expire') { LS.A.expireSession(); toast('Session expired. Approvals now ask you to sign in again.'); } else { LS.A.signOut(); toast('Signed out of Northwind. Device identity is unchanged.'); } renderAcct(); };
    document.addEventListener('click', e => { const r = e.target.closest('[data-review]'); if (r) { e.preventDefault(); e.stopPropagation(); review(r.dataset.review); return; } const d = e.target.closest('[data-dec-act]'); if (d) decAct(d.dataset.decAct); }, true);
    setInterval(() => { if (!D) return; $$('[data-exp]').forEach(x => { const a = D.apr(x.dataset.exp); if (a && a.status === 'pending') x.textContent = (x.closest('#decDlg') ? 'in ' : 'expires in ') + D.left(a.expires); }); }, 1000);
    $('#openNotes').onclick = () => open($('#notes'));
    $('#openAssist').onclick = () => open($('#assist'));
    $('#themeBtn').onclick = toggleTheme;
    $('#markRead').onclick = () => { $('#openNotes .pip').remove(); toast('Notifications marked read'); };
    $('#scrim').onclick = closeAll;
    $$('[data-close]').forEach(b => b.onclick = closeAll);
    if (LS) LS.on(() => { refreshInbox(); renderDec(); if (openEl && openEl.id === 'acct') renderAcct(); }); prioCounts();
    // runtime state cycle (demonstrates hosted / local / disconnected)
    const RT = [['st-ok', 'Hosted · nw-prod'], ['st-ok', 'Local · this device'], ['st-crit', 'Disconnected · retrying']];
    let rti = 0;
    $('#runtimeBtn').onclick = () => {
      rti = (rti + 1) % RT.length; $('#rtSt').className = 'st ' + RT[rti][0]; $('#rtText').textContent = RT[rti][1];
      document.body.classList.toggle('is-disconnected', rti === 2); document.dispatchEvent(new CustomEvent('locus:runtime', { detail: { state: rti === 2 ? 'down' : 'up' } }));
      const ban = $('#discBanner');
      if (rti === 2 && !ban) { const d = document.createElement('div'); d.id = 'discBanner'; d.className = 'callout crit'; d.style.cssText = 'margin:12px 28px 0;'; d.innerHTML = icon('alert') + '<div style="flex:1"><b>Runtime disconnected.</b> <span class="small">Live run streams are stale and shown as of 14:02:11. Nothing below is marked complete until the stream reconnects.</span></div><button class="btn btn-sm">Retry now</button>'; $('main').prepend(d); }
      if (rti !== 2 && ban) ban.remove();
      toast('Runtime: ' + RT[rti][1]);
    };
    let g = false;
    document.addEventListener('keydown', e => {
      const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#openPalette').click(); return; }
      if (e.key === 'Escape') { closeAll(); return; }
      if (typing) return;
      if (e.key === 'i') open($('#inbox'));
      if (e.key === '[') $('#navToggle').click();
      if (g) { const m = { h: 'home', w: 'work', a: 'activity', l: 'library', m: 'memory', c: 'control-tower', s: 'settings' }[e.key]; if (m) location.href = m + '.html'; g = false; }
      else if (e.key === 'g') { g = true; setTimeout(() => g = false, 900); }
    });

    // generic: tabs
    $$('[role="tablist"]').forEach(tl => {
      const tabs = $$('[role="tab"]', tl);
      const sel = t => { tabs.forEach(x => { const on = x === t; x.setAttribute('aria-selected', on); x.tabIndex = on ? 0 : -1; const p = document.getElementById(x.getAttribute('aria-controls')); if (p) p.hidden = !on; }); };
      tabs.forEach(t => { t.onclick = () => sel(t); t.onkeydown = e => { const i = tabs.indexOf(t); if (e.key === 'ArrowRight') { tabs[(i + 1) % tabs.length].focus(); sel(tabs[(i + 1) % tabs.length]); } if (e.key === 'ArrowLeft') { tabs[(i - 1 + tabs.length) % tabs.length].focus(); sel(tabs[(i - 1 + tabs.length) % tabs.length]); } }; });
      const h = location.hash.slice(1); const m = h && tabs.find(t => t.dataset.hash === h); if (m) sel(m);
    });
    // generic: segmented + filter chips
    $$('.seg').forEach(s => s.addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; $$('button', s).forEach(x => x.setAttribute('aria-pressed', x === b)); }));
    document.addEventListener('click', e => { const c = e.target.closest('.chip-f'); if (c) c.setAttribute('aria-pressed', c.getAttribute('aria-pressed') !== 'true'); });
    // generic: selectable rows
    document.addEventListener('click', e => {
      const r = e.target.closest('[data-sel]'); if (!r || e.target.closest('button:not([data-sel]),a,input')) return;
      const grp = r.closest('[data-select-group]'); if (grp) $$('[data-sel]', grp).forEach(x => { x.classList.toggle('is-sel', x === r); if (x.tagName === 'TR') { if (x === r) x.setAttribute('aria-current', 'true'); else x.removeAttribute('aria-current'); } });
      document.dispatchEvent(new CustomEvent('locus:select', { detail: { group: grp && grp.dataset.selectGroup, id: r.dataset.sel, el: r } }));
    });
    document.addEventListener('keydown', e => { const r = e.target.closest && e.target.closest('[data-sel]'); if (r && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); r.click(); } });
    // row focusability is applied by mark() below
    // generic: dialogs
    document.addEventListener('click', e => { const o = e.target.closest('[data-open]'); if (o) { e.preventDefault(); open(document.getElementById(o.dataset.open)); } });
    // generic: toasts
    // generic: actions without a simulated state transition are labelled as planned, never reported as done
    document.addEventListener('click', e => { const t = e.target.closest('[data-toast]'); if (!t) return; if (t.hasAttribute('data-sim')) return toast(t.dataset.toast, t.dataset.toastId);
      const lbl = [...t.childNodes].filter(n => !(n.classList && n.classList.contains('vh'))).map(n => n.textContent).join('').trim(); const e2 = D ? D.esc : (x => x); toast(`Planned capability · not simulated. ${lbl ? '“' + lbl + '”' : 'This action'} would: ${t.dataset.toast}`); });
    // accessible selection: rendered rows stay focusable and expose selection state
    /* rows that already contain a link or button are reached through that control; only link-free rows become focusable. Selection is announced with aria-current, not aria-selected outside a grid. */
    const mark = () => $$('[data-sel]').forEach(r => { const inner = r.querySelector('a[href],button'); if (inner && r !== inner) { if (r.getAttribute('tabindex') === '0' && !r.dataset.keepTab) r.removeAttribute('tabindex'); } else if (!r.hasAttribute('tabindex')) r.tabIndex = 0; if (r.tagName === 'TR') { r.removeAttribute('aria-selected'); if (r.classList.contains('is-sel')) r.setAttribute('aria-current', 'true'); else r.removeAttribute('aria-current'); } });
    /* planned controls never look or announce as working actions; a live MVP build hides them entirely (LocusBuild.hidePlanned) */
    const plan = () => $$('[data-toast]:not([data-sim]):not(.is-planned)').forEach(b => { b.classList.add('is-planned'); b.setAttribute('aria-disabled', 'true'); if (!b.querySelector('.vh')) b.insertAdjacentHTML('beforeend', '<span class="vh"> (planned, not available)</span>'); });
    if ((window.LocusBuild || {}).hidePlanned) document.documentElement.classList.add('mvp-live');
    new MutationObserver(() => { mark(); plan(); }).observe(document.body, { childList: true, subtree: true }); plan();
    mark();
  }

  function prioCounts() { if (!D) return; const s = D.s, p1 = s.runs.filter(r => r.status === 'failed').length + pendingApr().filter(a => a.risk === 'crit').length, p2 = pendingApr().filter(a => a.risk !== 'crit').length;
    const el = $('#prioCounts'); if (!el) return; el.innerHTML = `<a class="prio ${D.esc(p1 ? 'p1' : 'zero')}" href="activity.html" aria-label="${D.esc(p1)} priority 1 critical conditions">▲ ${D.esc(p1)}<span class="pl"> P1</span></a><a class="prio ${D.esc(p2 ? 'p2' : 'zero')}" href="activity.html#approvals" aria-label="${D.esc(p2)} priority 2 warnings">◆ ${D.esc(p2)}<span class="pl"> P2</span></a>`; }
  function refreshInbox() { prioCounts(); const n = pendingApr().length; const pip = $('#aprPip'), c = $('#navAprCount');
    if (pip) { pip.textContent = n; pip.hidden = !n; } if (c) { c.textContent = n; c.hidden = !n; c.setAttribute('aria-label', n + ' awaiting decision'); }
    $('#openInbox').setAttribute('aria-label', `Approval inbox, ${n} pending`); $('#aprTag').textContent = n + ' pending';
    const sig = pendingApr().map(a => a.id).join(); if ($('#inboxList').dataset.sig === sig) return; $('#inboxList').dataset.sig = sig;
    const keep = $('#inbox').contains(document.activeElement); $('#inboxList').innerHTML = inboxHtml(); $('#inboxEmpty').hidden = n > 0; if (keep) { const f = $('#inbox .icon-btn'); f && f.focus(); } }
  window.Locus = { toast, open: id => open(document.getElementById(id)), close: closeAll, review, icon, $, $$ };
  build();
  wire();
  $$('[data-icon]').forEach(el => el.insertAdjacentHTML('afterbegin', icon(el.dataset.icon)));
})();
