// Renders demo.html to ../demo.jpg-ready PNG: `node render.js demo@2x.png 2`
// (needs Playwright with Chromium). Background: Claude Monet, "Charing Cross
// Bridge, London" (1901), Art Institute of Chicago, public domain (CC0) -
// https://www.artic.edu/artworks/16544 - cropped to 16:10 in monet-charing-cross.jpg.
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: +process.argv[3] || 2 });
  await page.goto('file://' + __dirname + '/demo.html');
  await page.waitForFunction(() => document.body.dataset.ready === '1', null, { timeout: 60000 });
  await page.screenshot({ path: process.argv[2] || 'demo@2x.png' });
  await browser.close();
})();
