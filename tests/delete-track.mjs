import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {chromium} from 'playwright';
const server = spawn(process.env.RIFF_PYTHON || 'python3', ['-u', 'tests/review_browser_server.py'],
  {env: {...process.env, RIFF_MULTI_TRACK_FIXTURE: '1'}});
let browser, logs = '';
server.stderr.on('data', bytes => logs += bytes);
try {
  const fixture = await new Promise((resolve, reject) => {
    let line = ''; server.stdout.on('data', bytes => { line += bytes; if (line.includes('\n')) resolve(JSON.parse(line.split('\n')[0])); });
    server.once('exit', code => reject(new Error(`Fixture ${code}: ${logs}`)));
  });
  browser = await chromium.launch({headless: true});
  const page = await browser.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${fixture.url}/?recording=${fixture.track_id}#library`);
  await page.locator(`#library-grid [data-detail="${fixture.track_id}"]`).click();
  await page.setViewportSize({width: 320, height: 844});
  assert(await page.locator('#track-dialog').evaluate(element => element.scrollWidth <= element.clientWidth));
  const draft = await page.evaluate(() => formRecipe());
  page.once('dialog', dialog => dialog.dismiss());
  await page.locator('#delete-track').click();
  assert.equal((await page.request.get(`${fixture.url}/api/tracks/${fixture.track_id}`)).status(), 200);
  await page.route('**/api/tracks/' + fixture.track_id, async route => {
    if (route.request().method() === 'DELETE') return route.fulfill({status: 400, json: {error: 'Deletion failed safely'}});
    return route.continue();
  });
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#delete-track').click();
  await page.locator('#detail-error').getByText('Deletion failed safely').waitFor();
  assert(await page.locator('#track-dialog').isVisible());
  assert(await page.locator('#delete-track').isEnabled());
  await page.unroute('**/api/tracks/' + fixture.track_id);
  page.once('dialog', dialog => { assert.match(dialog.message(), /Reedlight/); return dialog.accept(); });
  await page.locator('#delete-track').click();
  await page.waitForFunction(() => selected === null && !document.querySelector('#track-dialog').open);
  assert.equal(await page.locator(`#library-grid [data-detail="${fixture.track_id}"]`).count(), 0);
  assert.equal((await page.request.get(`${fixture.url}/api/tracks/${fixture.track_id}/audio`)).status(), 404);
  assert(await page.locator('#play').isDisabled());
  assert.deepEqual(await page.evaluate(() => formRecipe()), draft);
  const remaining = await page.locator('#library-grid [data-detail]').getAttribute('data-detail');
  await page.locator(`#library-grid [data-play="${remaining}"]`).click();
  await page.waitForFunction(() => audio.currentTime > .1);
  await page.locator(`#library-grid [data-detail="${remaining}"]`).click();
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#delete-track').click();
  await page.waitForFunction(() => state.tracks.length === 0 && audio.paused && selected === null);
  await page.reload();
  await page.locator('#library-empty').waitFor();
  assert.equal(await page.locator('#library-grid [data-detail]').count(), 0);
  assert.deepEqual(errors, []);
  console.log('PASS Library deletion: cancel, failure recovery, selected/playing/last song removal, draft preservation and reload');
} finally {
  await browser?.close();
  const stopped = once(server, 'exit'); server.kill('SIGTERM'); await stopped;
}
