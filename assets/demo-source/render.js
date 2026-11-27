// Renders demo.html to ../demo.jpg-ready PNG: `node render.js demo@2x.png 2`
// (needs Playwright with Chromium). The oil-paint background is generated in
// the page with a fixed seed, so every render is identical.
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: +process.argv[3] || 2 });
  await page.goto('file://' + __dirname + '/demo.html');
  await page.waitForFunction(() => document.body.dataset.ready === '1', null, { timeout: 60000 });
  await page.screenshot({ path: process.argv[2] || 'demo@2x.png' });
  await browser.close();
})();
