// Drives the example UI, hello, in a real headless Chromium: every piece of
// ihm.js and ihm.css that a reader cannot check by eye.
//
//   node hello_check.mjs <playwright-dir> <base-url> <out-dir> <watched-module>
//
// <watched-module> is a module of a COPY of plinth the daemon watches: the
// script appends to it, and the page must say the daemon is stale.
import { appendFileSync } from 'node:fs';
import { join } from 'node:path';

const { start } = await import(process.env.PLINTH_HELPERS);
const t = await start();
const { page, base, fail, step } = t;
const [watched] = t.args;
const HEADER = { 'X-Hello': 'check' };
const rows = () => page.locator('article.row').count();
const waitRows = (n) => page.waitForFunction((count) =>
  document.querySelectorAll('article.row').length === count, n, { timeout: 5000 })
  .catch(() => fail('expected ' + n + ' rows'));
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const row = (name) => page.locator('article.row', { has: page.locator('.title', { hasText: name }) });
const toast = () => page.locator('.ihm-toast.show .message');
const api = (method, path, data) => page.request.fetch(base + path, { method, headers: HEADER, data });

await page.goto(base + '/?every=300');
await waitRows(3);
const line = (await page.locator('[data-ihm-daemon]').textContent()).trim();
if (!line.startsWith('Daemon up on port')) fail('daemon line: ' + line);
if (await page.locator('[data-ihm-banner]:visible').count()) fail('a banner shows on a healthy daemon');
const tiles = await page.locator('#tiles .value').allTextContents();
if (JSON.stringify(tiles) !== '["3","1","1","1"]') fail('tiles: ' + JSON.stringify(tiles));
const classes = await page.locator('article.row').evaluateAll((n) => n.map((r) => r.className));
if (JSON.stringify(classes) !== '["row is-ok","row is-human","row is-irreversible"]') {
  fail('row tones: ' + JSON.stringify(classes));
}
step('the page draws the items, their tones, the tiles and the daemon line');

await t.shots('hello');

// -- the theme, automatic and forced ------------------------------------------------
const background = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
const LIGHT = 'rgb(243, 244, 246)';
const DARK = 'rgb(13, 16, 21)';
await page.emulateMedia({ colorScheme: 'light' });
if (await background() !== LIGHT) fail('light background: ' + await background());
await page.locator('[data-theme-choice="dark"]').click();
if (await background() !== DARK) fail('dark forced under a light preference: ' + await background());
await page.emulateMedia({ colorScheme: 'dark' });
await page.locator('[data-theme-choice="light"]').click();
if (await background() !== LIGHT) fail('light forced under a dark preference: ' + await background());
await page.locator('[data-theme-choice="auto"]').click();
if (await background() !== DARK) fail('automatic under a dark preference: ' + await background());
await page.emulateMedia({ colorScheme: 'light' });
if (await background() !== LIGHT) fail('automatic under a light preference');
step('theme: light and dark follow the preference, data-theme forces either way');

// -- act: one click at a time -------------------------------------------------------------
await page.locator('#name').fill('Delta');
await page.locator('#add-button').dblclick();
await waitRows(4);
await pause(600);
if (await rows() !== 4) fail('a double click added ' + (await rows() - 3) + ' items');
if ((await toast().textContent()) !== 'Added Delta.') fail('toast: ' + await toast().textContent());
step('a double click on Add adds one item, and the toast says so');

// -- the redraw guard: never under an open menu ---------------------------------------------
await row('Alpha').locator('details.ihm-menu > summary').click();
await api('POST', '/api/items', { name: 'Echo' });
await pause(1200);                                   // four polls
if (await rows() !== 4) fail('the list was redrawn under an open menu');
if (!(await row('Alpha').locator('details.ihm-menu').evaluate((d) => d.open))) fail('the menu closed by itself');
await page.keyboard.press('Escape');
await waitRows(5);
step('no redraw while a menu is open; the held-back change shows once it closes');

// -- confirm: the error is shown IN the dialog, and no redraw under it ------------------------
await row('Charlie').locator('details.ihm-menu > summary').click();
await row('Charlie').getByRole('button', { name: 'Delete…' }).click();
const dialog = page.locator('dialog.ihm-dialog[open]');
await dialog.waitFor();
if ((await dialog.locator('h2').textContent()) !== 'Delete Charlie?') fail('dialog title');
const focused = await page.evaluate(() => document.activeElement.textContent);
if (focused !== 'Cancel') fail('an irreversible confirmation must focus Cancel, focused: ' + focused);
await api('POST', '/api/items', { name: 'Foxtrot' });
await pause(1200);
if (await rows() !== 5) fail('the list was redrawn under an open dialog');
await dialog.getByRole('button', { name: 'Delete' }).click();
const problem = dialog.locator('.hint.problem');
await problem.waitFor({ timeout: 5000 }).catch(() => fail('no error in the dialog'));
if ((await problem.textContent()) !== 'Charlie is locked: it cannot be deleted.') {
  fail('error in the dialog: ' + await problem.textContent());
}
if (!(await page.locator('dialog.ihm-dialog[open]').count())) fail('the dialog closed on an error');
for (const scheme of ['light', 'dark']) {
  await page.emulateMedia({ colorScheme: scheme });
  await page.screenshot({ path: join(t.outDir, 'confirm-error-' + scheme + '.png') });
}
await page.emulateMedia({ colorScheme: 'light' });
if (await page.locator('.ihm-toast.show.error').count()) fail('the error went to the toast, under the dialog');
await dialog.getByRole('button', { name: 'Cancel' }).click();
await waitRows(6);
step('a refused deletion is said inside the dialog, which stays open; redraw waits for it');

// -- confirm: success closes and says it ---------------------------------------------------------
await row('Delta').locator('details.ihm-menu > summary').click();
await row('Delta').getByRole('button', { name: 'Delete…' }).click();
await page.locator('dialog.ihm-dialog[open]').getByRole('button', { name: 'Delete' }).click();
await waitRows(5);
if (await page.locator('dialog.ihm-dialog').count()) fail('the dialog stayed after success');
if ((await toast().textContent()) !== 'Deleted Delta.') fail('toast after delete');
step('a confirmed deletion closes the dialog and says so');

// -- the picker, at the keyboard ------------------------------------------------------------------
await row('Alpha').getByRole('button', { name: 'Tone…' }).click();
const picker = page.locator('dialog.ihm-dialog[open]');
await picker.locator('.option').first().waitFor();
if (await picker.locator('.option').count() !== 7) fail('the picker offers ' + await picker.locator('.option').count());
for (const scheme of ['light', 'dark']) {
  await page.emulateMedia({ colorScheme: scheme });
  await page.screenshot({ path: join(t.outDir, 'picker-' + scheme + '.png') });
}
await page.emulateMedia({ colorScheme: 'light' });
await page.keyboard.press('ArrowDown');
await page.keyboard.press('ArrowDown');
const active = await picker.locator('.option.active .sub').textContent();
if (active !== 'error') fail('two arrows down should reach the third tone, got ' + active);
await page.keyboard.type('irr');
if (await picker.locator('.option').count() !== 1) fail('the filter keeps ' + await picker.locator('.option').count());
await page.keyboard.press('Enter');
await page.waitForFunction(() => !document.querySelector('dialog.ihm-dialog'));
if ((await toast().textContent()) !== 'Alpha is now irreversible.') fail('picker toast');
await page.waitForFunction(() => [...document.querySelectorAll('article.row')].some((r) =>
  r.textContent.includes('Alpha') && r.classList.contains('is-irreversible')));
step('the picker filters as one types, moves with the arrows and chooses with Enter');

// -- folding, remembered ----------------------------------------------------------------------------
const section = page.locator('section.section');
await section.locator('.section-head .fold-toggle').click();
if (!(await section.evaluate((s) => s.classList.contains('folded')))) fail('the section did not fold');
if (await section.locator('.section-body').isVisible()) fail('a folded section still shows its body');
const stored = await page.evaluate(() => localStorage.getItem('hello.folded.section:items'));
if (stored !== '1') fail('fold not stored under the UI prefix: ' + stored);
await page.reload();
await page.locator('section.section.folded').waitFor({ timeout: 5000 }).catch(() => fail('fold lost on reload'));
await page.locator('section.section .section-head h2').click();     // the header is the handle
if (await page.locator('section.section.folded').count()) fail('a click on the header did not unfold');
if (await page.evaluate(() => localStorage.getItem('hello.folded.section:items')) !== null) {
  fail('unfolding left the key in storage');
}
step('a section folds, stays folded across a reload, and unfolding clears the storage');

// -- without localStorage --------------------------------------------------------------------------
const blind = await t.newPage();
await blind.addInitScript(() => {
  Object.defineProperty(window, 'localStorage', { get() { throw new Error('storage blocked'); } });
});
await blind.goto(base + '/?every=300');
await blind.locator('article.row').first().waitFor();
await blind.locator('section.section .section-head .fold-toggle').click();
if (!(await blind.locator('section.section.folded').count())) fail('no fold without localStorage');
await blind.close();
step('folding works without localStorage, without a page error');

// -- the terminal route ------------------------------------------------------------------------------
await row('Bravo').locator('details.ihm-menu > summary').click();
await row('Bravo').getByRole('button', { name: 'Open a terminal' }).click();
await page.waitForFunction(() => (document.querySelector('.ihm-toast.show .message') || {}).textContent
  === 'A terminal tab greets Bravo (fake-wt).', null, { timeout: 5000 }).catch(() => fail('terminal toast'));
step('a menu action opens a terminal tab through the daemon');

// -- the daemon unreachable, then back ---------------------------------------------------------------
await page.route('**/plinth/status', (route) => route.abort());
const down = page.locator('[data-ihm-banner="down"]');
await down.waitFor({ state: 'visible', timeout: 5000 }).catch(() => fail('no unreachable banner'));
if (!(await down.textContent()).includes('unreachable')) fail('banner text: ' + await down.textContent());
if (!(await page.locator('[data-ihm-daemon].down').count())) fail('the daemon line does not say down');
await page.unroute('**/plinth/status');
await down.waitFor({ state: 'hidden', timeout: 5000 }).catch(() => fail('the banner stayed'));
step('an unreachable daemon is said in a banner, which leaves once it answers');

// -- the daemon stale: a module of plinth changes on disk -------------------------------------------
if (await page.locator('[data-ihm-banner="stale"]:visible').count()) fail('stale before any change');
appendFileSync(watched, '\n# a newer plinth\n');
const stale = page.locator('[data-ihm-banner="stale"]');
await stale.waitFor({ state: 'visible', timeout: 5000 }).catch(() => fail('no stale banner'));
if (!(await stale.textContent()).includes('older code')) fail('stale text: ' + await stale.textContent());
step('a daemon running older code than the disk is said in a banner');

await t.shots('hello-after');
await t.finish();
