import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {chromium} from 'playwright';
const server = spawn(process.env.RIFF_PYTHON || 'python3', ['-u', 'tests/review_browser_server.py'],
  {env: {...process.env, RIFF_MULTI_TRACK_FIXTURE: '1'}});
let browser, logs = '';
server.stderr.on('data', bytes => logs += bytes);
const visibleTitles = page => page.locator('#library-grid .library-card-title > button:first-child').allTextContents();
try {
  const fixture = await new Promise((resolve, reject) => {
    let line = ''; server.stdout.on('data', bytes => { line += bytes; if (line.includes('\n')) resolve(JSON.parse(line.split('\n')[0])); });
    server.once('exit', code => reject(new Error(`Fixture ${code}: ${logs}`)));
  });
  browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1280, height: 1000}});
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${fixture.url}/#library`);
  const reed = page.locator('.library-card', {has: page.getByRole('button', {name: 'Reedlight', exact: true})});
  await reed.waitFor();
  assert.deepEqual(await reed.locator('.tag-chip').allTextContents(), ['jazz', 'folk']);

  // A genre chip filters the library by its exact tag and a second click takes the filter back out.
  await reed.getByRole('button', {name: 'Show jazz recordings'}).click();
  assert.equal(await page.locator('#search').inputValue(), '#jazz');
  let titles = await visibleTitles(page);
  assert(titles.includes('Reedlight'));
  assert(!titles.includes('Playback comparison'));
  assert.equal(await reed.getByRole('button', {name: 'Show jazz recordings'}).getAttribute('aria-pressed'), 'true');
  await reed.getByRole('button', {name: 'Show jazz recordings'}).click();
  assert.equal(await page.locator('#search').inputValue(), '');
  assert((await visibleTitles(page)).includes('Playback comparison'));

  // Every word must match somewhere, in any order.
  await page.locator('#search').fill('folk warm');
  titles = await visibleTitles(page);
  assert(titles.includes('Playback comparison'));
  assert(!titles.includes('Reedlight'));

  // Notes are searchable too.
  const saved = await page.request.patch(`${fixture.url}/api/tracks/${fixture.track_id}`,
    {headers: {'X-Riff-Request': '1'}, data: {notes: 'album candidate'}});
  assert.equal(saved.status(), 200);
  await page.evaluate(() => refresh());
  await page.locator('#search').fill('candidate album');
  assert.deepEqual(await visibleTitles(page), ['Reedlight']);
  await page.locator('#search').fill('nothing-matches-this');
  assert(await page.locator('#library-empty').isVisible());
  assert.deepEqual(errors, []);
  console.log('PASS Library search matches title, description, notes and genre tags; genre chips toggle exact tag filters');
} finally {
  await browser?.close();
  server.kill('SIGTERM');
}
