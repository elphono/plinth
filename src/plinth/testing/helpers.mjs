// Helpers for the browser checks of a UI built on plinth.
//
// A check is a Node script that plinth.testing.run_browser_check runs as
//
//   node <script> <playwright-dir> <base-url> <out-dir> <args...>
//
// with PLINTH_HELPERS set to the file URL of this module:
//
//   const { start } = await import(process.env.PLINTH_HELPERS);
//   const t = await start();
//   await t.page.goto(t.base + '/');
//   t.step('the page is up');
//   await t.shots('index');          // light, dark, phone light, phone dark
//   await t.finish();                // fails if the page threw or reached outside
//
// What it collects while the page runs, and finish() turns into a failure:
// uncaught page errors, console errors (except the browser's own "Failed to
// load resource" line, which every error answer of the API produces), and
// any request that leaves the daemon: a local UI works offline.
import { createRequire } from 'node:module';
import { join } from 'node:path';

export async function start(options = {}) {
  const [pwDir, base, outDir, ...args] = process.argv.slice(2);
  if (!pwDir || !base || !outDir) {
    console.error('FAIL usage: node <script> <playwright-dir> <base-url> <out-dir> [args...]');
    process.exit(2);
  }
  const require = createRequire(join(pwDir, 'package.json'));
  const { chromium } = require('playwright');
  const browser = await chromium.launch();
  const errors = [];
  const watch = (page) => {
    page.on('pageerror', (e) => errors.push('page error: ' + e.message));
    page.on('console', (m) => {
      if (m.type() === 'error' && !m.text().startsWith('Failed to load resource')) {
        errors.push('console error: ' + m.text());
      }
    });
    page.on('request', (r) => {
      const url = r.url();
      if (!url.startsWith(base) && !url.startsWith('data:') && !url.startsWith('blob:')) {
        errors.push('request outside the daemon: ' + url);
      }
    });
    return page;
  };
  const newPage = async (contextOptions = {}) => {
    const context = await browser.newContext({
      viewport: options.viewport || { width: 1100, height: 900 }, ...contextOptions });
    return watch(await context.newPage());
  };
  const page = await newPage();

  const fail = (message) => {
    console.error('FAIL ' + message);
    process.exit(1);          // Playwright closes the browser on exit
  };
  const step = (message) => console.log('OK   ' + message);

  // Four screenshots of the current page, full height: light and dark at the
  // desktop width, then at 390 px, where a horizontal scroll fails the check.
  async function shots(name, target = page) {
    const size = target.viewportSize();
    await target.emulateMedia({ colorScheme: 'light' });
    await target.screenshot({ path: join(outDir, name + '-light.png'), fullPage: true });
    await target.emulateMedia({ colorScheme: 'dark' });
    await target.screenshot({ path: join(outDir, name + '-dark.png'), fullPage: true });
    await target.setViewportSize({ width: 390, height: 844 });
    const wide = await target.evaluate(() => document.documentElement.scrollWidth);
    if (wide > 390) fail(name + ': horizontal scroll at 390 px (' + wide + ' px wide)');
    await target.screenshot({ path: join(outDir, name + '-phone-dark.png'), fullPage: true });
    await target.emulateMedia({ colorScheme: 'light' });
    await target.screenshot({ path: join(outDir, name + '-phone.png'), fullPage: true });
    await target.setViewportSize(size);
    step(name + ': screenshots taken, no horizontal scroll at 390 px');
  }

  async function finish() {
    if (errors.length) fail('the page reported: ' + errors.join(' | '));
    await browser.close();
    console.log('DONE');
  }

  return { browser, page, newPage, base, outDir, args, errors, fail, step, shots, finish };
}
