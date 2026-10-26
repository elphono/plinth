// hello's page: plinth's Ihm does the plumbing, this file draws the items.
(() => {
  'use strict';
  Ihm.configure({ header: 'X-Hello', name: 'hello' });
  const $ = (id) => document.getElementById(id);
  const TONE_TEXT = {
    ok: 'OK', warn: 'Attention', error: 'Error', live: 'Live', human: 'Your turn',
    irreversible: 'Irreversible', info: 'Info',
  };
  let tones = [];

  function chooseTone(item) {
    Ihm.closeMenus();
    return Ihm.picker({
      title: 'Tone of ' + item.name,
      lines: ['The colour says what the item needs.'],
      placeholder: 'Filter the tones',
      load: async () => tones,
      row: (tone) => ({ name: TONE_TEXT[tone], sub: tone, badges: tone === item.tone ? [Ihm.badge('current')] : [] }),
      choose: async (tone) => (await Ihm.call('POST', '/api/items/' + encodeURIComponent(item.id) + '/tone',
        { tone })).message,
    });
  }

  function remove(item) {
    Ihm.closeMenus();
    return Ihm.confirm({
      title: 'Delete ' + item.name + '?',
      lines: ['It leaves the list now; nothing brings it back.'],
      items: [item.name],
      kept: item.locked ? 'This item is locked: the daemon will refuse.' : '',
      label: 'Delete',
      tone: 'irreversible',
      run: async () => (await Ihm.call('DELETE', '/api/items/' + encodeURIComponent(item.id))).message,
    });
  }

  function row(item) {
    const node = Ihm.make('article', 'row is-' + item.tone);
    node.dataset.id = item.id;
    const main = Ihm.make('div');
    const line = Ihm.make('div', 'row-line');
    line.append(Ihm.make('span', 'title', item.name), Ihm.badge(TONE_TEXT[item.tone], item.tone));
    if (item.locked) line.append(Ihm.badge('locked'));
    main.append(line, Ihm.make('div', 'meta', 'added ' + Ihm.ago(item.created)));
    const actions = Ihm.make('div', 'actions');
    const tone = Ihm.button('Tone…', '', 'Choose the tone of this item');
    tone.addEventListener('click', () => chooseTone(item));
    const del = Ihm.button('Delete…', 'danger', 'Delete this item, after a confirmation');
    del.addEventListener('click', () => remove(item));
    actions.append(tone, Ihm.menu('More', [
      Ihm.button('Open a terminal', '', 'A terminal tab greets this item', async () =>
        (await Ihm.call('POST', '/api/items/' + encodeURIComponent(item.id) + '/terminal')).message),
      '-', del]));
    node.append(main, actions);
    return node;
  }

  function group(tone, items) {
    const card = Ihm.make('section', 'card group is-' + tone);
    card.dataset.tone = tone;
    const head = Ihm.make('header', 'group-head');
    const titles = Ihm.make('div');
    titles.append(Ihm.make('div', 'group-kicker', 'Tone'), Ihm.make('h3', '', TONE_TEXT[tone]));
    const meta = Ihm.make('div', 'group-meta');
    meta.append(Ihm.chip(Ihm.plural(items.length, 'item'), { icon: 'layers', tone }));
    titles.append(meta);
    const tools = Ihm.make('div', 'group-tools');
    tools.append(Ihm.foldable(card, head, 'tone:' + tone, 'the ' + TONE_TEXT[tone] + ' items'));
    head.append(titles, tools);
    card.append(head, ...items.map(row));
    return card;
  }

  function render(data) {
    tones = data.tones;
    const items = data.items;
    const count = (tone) => items.filter((i) => i.tone === tone).length;
    const tiles = $('tiles');
    tiles.textContent = '';
    for (const [value, label, tone] of [[items.length, 'items', ''], [count('human'), 'need you', 'human'],
      [count('irreversible'), 'irreversible', 'irreversible'], [count('ok'), 'ok', 'ok']]) {
      const tile = Ihm.make('div', 'tile' + (value && tone ? ' is-' + tone : ''));
      tile.append(Ihm.make('span', 'value', String(value)), Ihm.make('span', 'label', label));
      tiles.append(tile);
    }
    const box = $('sections');
    box.textContent = '';
    if (!items.length) {
      box.append(Ihm.empty('No item yet', ['Add one with the field above.']));
      return;
    }
    const section = Ihm.make('section', 'section');
    section.dataset.section = 'items';
    const head = Ihm.make('header', 'section-head');
    const mark = Ihm.make('span', 'section-icon');
    mark.append(Ihm.icon('layers'));
    head.append(mark, Ihm.make('span', 'section-kicker', 'Section'), Ihm.make('h2', '', 'Items'),
      Ihm.make('span', 'section-facts', Ihm.plural(items.length, 'item')),
      Ihm.foldable(section, head, 'section:items', 'the items'));
    const body = Ihm.make('div', 'section-body');
    for (const tone of tones) {
      const members = items.filter((i) => i.tone === tone);
      if (members.length) body.append(group(tone, members));
    }
    section.append(head, body);
    box.append(section);
  }

  // The theme: automatic, or forced through <html data-theme>.
  function setTheme(choice) {
    if (choice === 'auto') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = choice;
    for (const node of document.querySelectorAll('[data-theme-choice]')) {
      node.setAttribute('aria-pressed', String(node.dataset.themeChoice === choice));
    }
    try { localStorage.setItem('hello.theme', choice); } catch (err) { /* memory only */ }
  }
  for (const node of document.querySelectorAll('[data-theme-choice]')) {
    node.addEventListener('click', () => setTheme(node.dataset.themeChoice));
  }
  try { setTheme(localStorage.getItem('hello.theme') || 'auto'); } catch (err) { setTheme('auto'); }

  $('add').addEventListener('submit', (e) => {
    e.preventDefault();
    Ihm.act($('add-button'), async () => {
      const answer = await Ihm.call('POST', '/api/items', { name: $('name').value });
      $('name').value = '';
      return answer.message;
    });
  });

  const every = Number(new URLSearchParams(location.search).get('every')) || 3000;
  Ihm.poll({ url: '/api/items', render, every });
})();
