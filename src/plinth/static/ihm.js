/*
 * plinth — the page utilities of a local web UI: the namespace `Ihm`.
 * Served at /plinth/static/ihm.js. A classic script, not an ES module, so that
 * it also works inlined in a page; it defines `window.Ihm` and nothing else.
 *
 *   Ihm.configure({ header: 'X-Hello', name: 'hello' })   // first, always
 *   const poller = Ihm.poll({ url: '/api/state', render })
 *
 * | Ihm.*                 | What it does                                          |
 * |-----------------------|-------------------------------------------------------|
 * | configure({header,    | the daemon's guard header (sent on every call) and    |
 * |   name})              | the UI's name (prefix of what is kept in localStorage) |
 * | call(method, path, b) | fetch + header + JSON; throws Error(message), .status |
 * | act(node, run)        | one click at a time: run() → toast, then refresh      |
 * | say(text, error)      | the toast                                             |
 * | button, menu, icon,   | small builders; data reaches the DOM through          |
 * |   chip, badge, empty  |   textContent only                                    |
 * | foldable(section,     | a header that folds its section, remembered per       |
 * |   head, key, what)    |   browser (works without localStorage)                |
 * | confirm({...})        | the confirmation dialog: counts, lists, and shows an  |
 * |                       |   error INSIDE the dialog (a toast lies under it)     |
 * | picker({...})         | a filterable list in a dialog (↑ ↓ Enter Esc)         |
 * | poll({url, render})   | refresh loop; render(data) only when data changed and |
 * |                       |   never while a menu, a dialog or a click is open;    |
 * |                       |   banners "daemon unreachable" and "daemon stale"     |
 * | blocked()             | true while a redraw would swap what the user handles  |
 */
(function (global) {
  'use strict';
  const HEADER_RE = /^X-[A-Za-z0-9][A-Za-z0-9-]*$/;
  const NAME_RE = /^[a-z][a-z0-9-]{0,40}$/;
  const STATUS_PATH = '/plinth/status';
  const MENU = 'details.ihm-menu';
  const TOAST_MS = 7000;
  const settings = { header: '', name: '' };
  let busy = false;
  let poller = null;
  let counter = 0;

  function configure(options) {
    const { header, name } = options || {};
    if (!HEADER_RE.test(header || '')) {
      throw new Error('Ihm.configure: header must be the daemon\'s guard header, X-…');
    }
    if (!NAME_RE.test(name || '')) {
      throw new Error('Ihm.configure: name must be lowercase letters, digits and -');
    }
    settings.header = header;
    settings.name = name;
  }

  function needConfig(what) {
    if (!settings.header) throw new Error('Ihm.configure({header, name}) must run before ' + what);
  }

  // -- small builders -----------------------------------------------------------------
  function make(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  const plural = (count, word, many) => count + ' ' + (count === 1 ? word : (many || word + 's'));

  function ago(epoch) {
    if (!epoch) return '';
    const seconds = Math.max(0, Date.now() / 1000 - epoch);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' min ago';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' h ago';
    if (seconds < 2 * 86400) return 'yesterday';
    if (seconds < 7 * 86400) return Math.floor(seconds / 86400) + ' days ago';
    return new Date(epoch * 1000).toLocaleDateString(undefined,
      { day: 'numeric', month: 'short', year: 'numeric' });
  }

  // Constant SVG paths only: never data.
  const ICONS = {
    chevron: '<path d="M6 9l6 6 6-6"/>',
    check: '<path d="M5 12l5 5 9-10"/>',
    alert: '<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    play: '<path d="M7 4l13 8-13 8z"/>',
    terminal: '<path d="M4 17l6-5-6-5"/><path d="M12 19h8"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    dot: '<circle cx="12" cy="12" r="4"/>',
    hand: '<path d="M8 13V5a1.5 1.5 0 0 1 3 0v6M11 11V4a1.5 1.5 0 0 1 3 0v7M14 11V6a1.5 1.5 0 0 1 3 0v8a6 6 0 0 1-6 6h-1a5 5 0 0 1-4-2l-3-4a1.5 1.5 0 0 1 2.3-2L8 13"/>',
    layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  };

  function addIcon(name, paths) { ICONS[name] = paths; }

  function icon(name) {
    const holder = document.createElement('span');
    holder.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
      + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
      + (ICONS[name] || ICONS.dot) + '</svg>';
    return holder.firstChild;
  }

  function chip(text, options) {
    const { icon: name, tone, mono, title } = options || {};
    const node = make('span', 'chip' + (mono ? ' mono' : '') + (tone ? ' is-' + tone : ''));
    if (name) node.append(icon(name));
    node.append(text);
    if (title) node.title = title;
    return node;
  }

  const badge = (text, tone) => make('span', 'badge' + (tone ? ' is-' + tone : ''), text);

  function empty(title, lines, action) {
    const box = make('div', 'card empty');
    box.append(make('h2', '', title));
    for (const text of lines || []) box.append(make('p', '', text));
    if (action) box.append(action);
    return box;
  }

  // -- the toast -------------------------------------------------------------------------
  let toastNode = null;
  let toastTimer = 0;
  function toastBox() {
    if (!toastNode) {
      toastNode = make('div', 'ihm-toast');
      const message = make('span', 'message');
      message.setAttribute('role', 'status');
      message.setAttribute('aria-live', 'polite');
      const close = make('button', '', '×');
      close.type = 'button';
      close.setAttribute('aria-label', 'Dismiss');
      close.addEventListener('click', () => toastNode.classList.remove('show'));
      toastNode.append(message, close);
      document.body.append(toastNode);
    }
    return toastNode;
  }

  function say(text, error) {
    const node = toastBox();
    clearTimeout(toastTimer);
    node.querySelector('.message').textContent = text || '';
    node.classList.toggle('error', Boolean(error));
    node.classList.toggle('show', Boolean(text));
    if (text && !error) toastTimer = setTimeout(() => node.classList.remove('show'), TOAST_MS);
  }

  // -- talking to the daemon ---------------------------------------------------------------
  async function call(method, path, body) {
    needConfig('Ihm.call');
    const headers = { [settings.header]: settings.name };
    const init = { method, headers, cache: 'no-store' };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    const response = await fetch(path, init);
    let data = null;
    try { data = await response.json(); } catch (err) { data = null; }
    if (!response.ok) {
      const error = new Error((data && data.error) || ('HTTP ' + response.status));
      error.status = response.status;
      throw error;
    }
    return data;
  }

  // -- menus, and when a redraw must wait --------------------------------------------------
  const menuOpen = () => Boolean(document.querySelector(MENU + '[open]'));
  function closeMenus(except) {
    for (const node of document.querySelectorAll(MENU + '[open]')) {
      if (node !== except) node.open = false;
    }
  }
  document.addEventListener('click', (e) => {
    closeMenus(e.target instanceof Element ? e.target.closest(MENU) : null);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenus(); });

  // A redraw under the cursor would swap the button being pressed, close the
  // menu being read, or rebuild what a dialog points at.
  const blocked = () => busy || menuOpen() || Boolean(document.querySelector('dialog[open]'));

  // Once nothing blocks any more, the poller draws what it held back.
  const unblocked = () => setTimeout(() => { if (poller && !blocked()) poller.catchUp(); }, 0);
  document.addEventListener('toggle', (e) => {
    if (e.target instanceof Element && e.target.matches(MENU) && !e.target.open) unblocked();
  }, true);
  document.addEventListener('close', unblocked, true);

  function menu(label, entries, title) {
    const node = make('details', 'ihm-menu');
    const summary = make('summary', '', label || 'More');
    if (title) summary.title = title;
    const list = make('div', 'menu');
    for (const entry of entries) list.append(entry === '-' ? make('hr') : entry);
    node.append(summary, list);
    return node;
  }

  // One click at a time, and the page is refreshed right after: a button that
  // did something must show its effect without waiting for the next poll.
  async function act(node, run) {
    if (busy) return;
    busy = true;
    if (node) node.disabled = true;
    closeMenus();
    try {
      const text = await run();
      if (text) say(text, false);
    } catch (err) {
      say(err.message, true);
    } finally {
      busy = false;
      if (node) node.disabled = false;
      if (poller) await poller.refresh();
    }
  }

  function button(label, cls, title, run) {
    const node = make('button', cls, label);
    node.type = 'button';
    if (title) node.title = title;
    if (run) node.addEventListener('click', () => act(node, run));
    return node;
  }

  // -- folding -----------------------------------------------------------------------------
  // Open by default, remembered per browser under `<name>.folded.<key>`. The
  // page must work without storage (private window, blocked site data): every
  // access is guarded, and memory keeps the state while the page lives.
  const folds = new Map();
  const foldKey = (key) => settings.name + '.folded.' + key;

  function isFolded(key) {
    if (!folds.has(key)) {
      let stored = false;
      try { stored = global.localStorage.getItem(foldKey(key)) === '1'; } catch (err) { stored = false; }
      folds.set(key, stored);
    }
    return folds.get(key);
  }

  function setFolded(key, value) {
    folds.set(key, value);
    try {
      if (value) global.localStorage.setItem(foldKey(key), '1');
      else global.localStorage.removeItem(foldKey(key));
    } catch (err) { /* memory only */ }
  }

  function foldable(section, head, key, what) {
    needConfig('Ihm.foldable');
    const toggle = make('button', 'fold-toggle');
    toggle.type = 'button';
    toggle.append(icon('chevron'));
    const show = (folded) => {
      section.classList.toggle('folded', folded);
      toggle.setAttribute('aria-expanded', String(!folded));
      toggle.setAttribute('aria-label', (folded ? 'Unfold ' : 'Fold ') + what);
      toggle.title = folded ? 'Unfold' : 'Fold';
    };
    const flip = () => {
      const folded = !section.classList.contains('folded');
      setFolded(key, folded);
      show(folded);
    };
    show(isFolded(key));
    toggle.addEventListener('click', (e) => { e.stopPropagation(); flip(); });
    head.classList.add('foldable');
    head.addEventListener('click', (e) => {
      // A button, a link or a field of the header does its own job.
      if (e.target instanceof Element && e.target.closest('button, a, input, select, details')) return;
      if (global.getSelection && String(global.getSelection())) return;   // selecting text
      flip();
    });
    return toggle;
  }

  // -- dialogs -----------------------------------------------------------------------------
  function dialogShell(cls, title, lines, tone) {
    const dialog = make('dialog', 'ihm-dialog' + (cls ? ' ' + cls : ''));
    const head = make('div', 'dialog-head' + (tone ? ' is-' + tone : ''));
    const heading = make('h2', '', title);
    heading.id = 'ihm-dialog-title-' + (++counter);
    dialog.setAttribute('aria-labelledby', heading.id);
    head.append(heading);
    for (const line of lines) head.append(make('p', '', line));
    dialog.append(head);
    return dialog;
  }

  // confirm({title, lines, items, limit, kept, label, tone, hint, run}) → Promise<bool>
  // `run` does the work and returns the sentence the toast shows; if it throws,
  // its message is shown in the dialog, which stays open.
  function confirm(options) {
    const opts = options || {};
    if (typeof opts.run !== 'function') throw new Error('Ihm.confirm: run is required');
    const tone = opts.tone || '';
    const risky = tone === 'irreversible' || tone === 'error' || tone === 'danger';
    const lines = opts.lines || (opts.text ? [opts.text] : []);
    return new Promise((resolve) => {
      const dialog = dialogShell('', opts.title || 'Confirm', lines, tone);
      const items = opts.items || [];
      const limit = opts.limit || 8;
      if (items.length) {
        const list = make('ul', 'dialog-list');
        for (const item of items.slice(0, limit)) {
          const entry = make('li');
          entry.append(item instanceof Node ? item : String(item));
          list.append(entry);
        }
        if (items.length > limit) list.append(make('li', '', '… and ' + (items.length - limit) + ' more'));
        dialog.append(list);
      }
      dialog.append(make('p', 'dialog-kept', opts.kept || ''));
      const foot = make('div', 'dialog-foot');
      const hint = make('span', 'hint', opts.hint || '');
      hint.setAttribute('aria-live', 'polite');
      const cancel = make('button', '', opts.cancel || 'Cancel');
      cancel.type = 'button';
      const confirmButton = make('button', risky ? 'primary danger' : 'primary', opts.label || 'Confirm');
      confirmButton.type = 'button';
      foot.append(hint, cancel, confirmButton);
      dialog.append(foot);
      let done = false;
      cancel.addEventListener('click', () => { if (!busy) dialog.close(); });
      dialog.addEventListener('click', (e) => { if (e.target === dialog && !busy) dialog.close(); });
      dialog.addEventListener('cancel', (e) => { if (busy) e.preventDefault(); });
      dialog.addEventListener('close', () => { dialog.remove(); resolve(done); });
      confirmButton.addEventListener('click', async () => {
        if (busy) return;
        busy = true;
        confirmButton.disabled = true;
        cancel.disabled = true;
        hint.className = 'hint';
        hint.textContent = opts.working || 'Working…';
        let message = '';
        try {
          message = await opts.run();
          done = true;
        } catch (err) {
          // The toast lies under the modal dialog: the error is shown in it.
          hint.className = 'hint problem';
          hint.textContent = err.message;
        } finally {
          busy = false;
          confirmButton.disabled = false;
          cancel.disabled = false;
        }
        if (done) {
          dialog.close();
          if (message) say(message, false);
        }
        if (poller) poller.refresh();
      });
      document.body.append(dialog);
      dialog.showModal();
      (risky ? cancel : confirmButton).focus();
    });
  }

  // picker({title, lines, load, row, match, choose, placeholder}) → Promise<item|null>
  // load() gives the items; row(item) → {name, sub, badges: [Node]}; choose(item)
  // acts and returns the toast's sentence, or throws (shown in the dialog).
  function picker(options) {
    const opts = options || {};
    if (typeof opts.load !== 'function' || typeof opts.choose !== 'function' ||
        typeof opts.row !== 'function') {
      throw new Error('Ihm.picker: load, row and choose are required');
    }
    return new Promise((resolve) => {
      const dialog = dialogShell('wide', opts.title || 'Choose', opts.lines || [], '');
      const box = make('div', 'picker-search');
      const search = make('input');
      search.type = 'search';
      search.placeholder = opts.placeholder || 'Filter';
      search.autocomplete = 'off';
      search.spellcheck = false;
      search.setAttribute('aria-label', opts.placeholder || 'Filter');
      box.append(search);
      const list = make('div', 'picker-list');
      list.setAttribute('role', 'listbox');
      list.id = 'ihm-picker-list-' + (++counter);
      search.setAttribute('aria-controls', list.id);
      const foot = make('div', 'dialog-foot');
      const hint = make('span', 'hint', '↑ ↓ to move, Enter to choose, Esc to close');
      hint.setAttribute('aria-live', 'polite');
      const cancel = make('button', '', 'Cancel');
      cancel.type = 'button';
      foot.append(hint, cancel);
      dialog.append(box, list, foot);
      let items = [];
      let shown = [];
      let active = 0;
      let chosen = null;

      const words = () => search.value.toLowerCase().split(/\s+/).filter(Boolean);
      const defaultMatch = (item, terms) => {
        const view = opts.row(item);
        const text = ((view.name || '') + ' ' + (view.sub || '')).toLowerCase();
        return terms.every((w) => text.includes(w));
      };

      function setActive(index) {
        active = index;
        list.querySelectorAll('.option').forEach((node, i) => {
          node.classList.toggle('active', i === index);
          node.setAttribute('aria-selected', String(i === index));
          if (i === index) {
            search.setAttribute('aria-activedescendant', node.id);
            node.scrollIntoView({ block: 'nearest' });
          }
        });
      }

      function render() {
        const terms = words();
        shown = items.filter((item) => (opts.match || defaultMatch)(item, terms));
        list.textContent = '';
        if (!items.length) list.append(make('p', 'picker-empty', opts.emptyText || 'Nothing to choose from.'));
        else if (!shown.length) list.append(make('p', 'picker-empty', 'Nothing matches “' + search.value.trim() + '”.'));
        shown.forEach((item, index) => {
          const view = opts.row(item);
          const option = make('button', 'option');
          option.type = 'button';
          option.id = list.id + '-' + index;
          option.setAttribute('role', 'option');
          const line = make('span', 'line');
          line.append(make('span', 'name', view.name || ''));
          for (const extra of view.badges || []) line.append(extra);
          option.append(line);
          if (view.sub) option.append(make('span', 'sub', view.sub));
          option.addEventListener('click', () => pick(item));
          option.addEventListener('mousemove', () => { if (active !== index) setActive(index); });
          list.append(option);
        });
        if (shown.length) setActive(Math.min(active, shown.length - 1));
        else search.removeAttribute('aria-activedescendant');
        hint.className = 'hint';
        hint.textContent = shown.length + ' of ' + items.length + ' · ↑ ↓ Enter';
      }

      async function pick(item) {
        if (busy || !item) return;
        busy = true;
        let message = '';
        try {
          message = await opts.choose(item);
          chosen = item;
        } catch (err) {
          hint.className = 'hint problem';
          hint.textContent = err.message;
        } finally {
          busy = false;
        }
        if (chosen) {
          dialog.close();
          if (message) say(message, false);
        }
        if (poller) poller.refresh();
      }

      search.addEventListener('input', () => { active = 0; render(); });
      search.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          if (!shown.length) return;
          const step = e.key === 'ArrowDown' ? 1 : -1;
          setActive((active + step + shown.length) % shown.length);
        } else if (e.key === 'Enter') {
          e.preventDefault();
          pick(shown[active]);
        }
      });
      cancel.addEventListener('click', () => { if (!busy) dialog.close(); });
      dialog.addEventListener('click', (e) => { if (e.target === dialog && !busy) dialog.close(); });
      dialog.addEventListener('cancel', (e) => { if (busy) e.preventDefault(); });
      dialog.addEventListener('close', () => { dialog.remove(); resolve(chosen); });
      document.body.append(dialog);
      dialog.showModal();
      search.focus();
      list.append(make('p', 'picker-empty', 'Loading…'));
      Promise.resolve().then(() => opts.load()).then((loaded) => {
        items = loaded || [];
        if (dialog.open) render();
      }, (err) => {
        list.textContent = '';
        list.append(make('p', 'picker-empty', 'Cannot load the list: ' + err.message));
      });
    });
  }

  // -- banners -------------------------------------------------------------------------------
  const banners = {};
  function banner(kind, show, text, action) {
    let node = banners[kind];
    if (!show) {
      if (node) node.hidden = true;
      return;
    }
    if (!node) {
      node = make('div', 'ihm-banner ' + (kind === 'stale' ? 'is-warn' : 'is-error'));
      node.dataset.ihmBanner = kind;
      node.setAttribute('role', 'alert');
      const inner = make('div', 'ihm-banner-inner');
      inner.append(make('span'));
      if (action) inner.append(action);
      node.append(inner);
      const bar = document.querySelector('.topbar');
      if (bar) bar.after(node);
      else document.body.prepend(node);
      banners[kind] = node;
    }
    node.querySelector('span').textContent = text;
    node.hidden = false;
  }

  function daemonLine(up, text) {
    const node = document.querySelector('[data-ihm-daemon]');
    if (!node) return;
    if (!node.querySelector('.text')) {
      node.textContent = '';
      node.append(make('span', 'dot'), make('span', 'text'));
    }
    node.classList.add('daemon');
    node.classList.toggle('up', up);
    node.classList.toggle('down', !up);
    node.querySelector('.text').textContent = text;
  }

  // -- the refresh loop ------------------------------------------------------------------------
  // poll({url, render, every, onRestart}) → {refresh, redraw, stop, data}
  function poll(options) {
    needConfig('Ihm.poll');
    const opts = options || {};
    if (!opts.url || typeof opts.render !== 'function') throw new Error('Ihm.poll: url and render are required');
    const every = opts.every || 4000;
    let last = null;
    let lastKey = '';
    let shownKey = '';
    let stopped = false;
    const restart = opts.onRestart
      ? button('Restart daemon', 'primary', 'Restart the daemon on the code on disk', opts.onRestart) : null;

    function draw(force) {
      if (last === null || blocked()) return;          // drawn once unblocked
      const key = lastKey + '|' + Math.floor(Date.now() / 60000);
      if (!force && key === shownKey) return;
      shownKey = key;
      opts.render(last);
    }

    async function refresh() {
      if (stopped) return;
      try {
        const [status, data] = await Promise.all([call('GET', STATUS_PATH), call('GET', opts.url)]);
        banner('down', false);
        // The page is read from disk at every request, the daemon's Python
        // once: after an update, the daemon is older than this page.
        banner('stale', status.stale !== false,
          'The daemon runs older code than the one on disk: this page may show less than it '
          + 'should until the daemon restarts.', restart);
        daemonLine(true, 'Daemon up on port ' + status.port);
        last = data;
        lastKey = JSON.stringify(data);
        draw(false);
      } catch (err) {
        const text = err.status ? 'The daemon answered with an error: ' + err.message
          : 'The daemon is unreachable (' + err.message + '): start it again.';
        banner('down', true, text);
        daemonLine(false, err.status ? 'Daemon error' : 'Daemon unreachable');
      }
    }

    const timer = setInterval(() => { if (!busy) refresh(); }, every);
    poller = {
      refresh,
      redraw: () => draw(true),
      catchUp: () => draw(false),
      stop() { stopped = true; clearInterval(timer); },
      get data() { return last; },
    };
    refresh();
    return poller;
  }

  global.Ihm = Object.freeze({
    configure, call, act, say, button, menu, icon, addIcon, chip, badge, empty, make, plural, ago,
    foldable, confirm, picker, poll, blocked, closeMenus,
  });
})(window);
