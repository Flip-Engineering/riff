import assert from 'node:assert/strict';
import {chromium} from 'playwright';
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const original = await (await page.request.get(process.env.RIFF_URL + '/api/state')).json();
  const source = original.tracks[0];
  const tracks = Array.from({length:1200}, (_, i) => ({...source,
    id: i ? i.toString(16).padStart(32,'0') : source.id,
    title: `Recording ${String(i).padStart(4,'0')}`, created: 1800000000-i,
    seed:String(i), notes:i===1199?'rare final needle':'', favorite:i===1199,
    archived:i===1198, tags:['jazz'], style:'Warm jazz quartet'}));
  const jobs = tracks.map((track,i)=>({id:track.id,title:track.title,created:track.created,status:'done',track_id:track.id})).reverse();
  let revision = 'fixture-1', conditional = 0;
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/state*', async route => {
    const data = {...original, tracks, jobs, catalog_revision:revision};
    if (new URL(route.request().url()).searchParams.get('catalog_revision') === revision) {
      for (const field of ['tracks','jobs','plans','presets']) delete data[field];
      conditional++;
    }
    await route.fulfill({json:data});
  });
  await page.goto(process.env.RIFF_URL + '/#studio');
  await page.locator('#play:not([disabled])').waitFor();
  await page.evaluate(()=>clearTimeout(pollTimer));
  assert.equal(await page.locator('.library-card').count(),0,'Studio does not build hidden library cards');
  await page.locator('[data-view="library"]').first().click();
  assert.equal(await page.locator('.library-card').count(),24);
  assert.equal(await page.locator('.history-item').count(),24);
  await page.evaluate(()=>{window.firstLibraryCard=document.querySelector('.library-card');});
  await page.evaluate(()=>refresh());
  assert(conditional>0,'Poll sends the known catalog revision');
  assert(await page.evaluate(()=>firstLibraryCard===document.querySelector('.library-card')),'Unchanged polls preserve cards');
  revision='fixture-2';jobs[0].status='failed';
  await page.evaluate(()=>refresh());
  assert(await page.evaluate(()=>firstLibraryCard===document.querySelector('.library-card')),'Job-only updates preserve artwork and cards');
  // Search operates on the entire catalog, including recordings never mounted.
  await page.locator('#search').fill('rare final needle');
  assert.equal(await page.locator('.library-card').count(),1);
  assert.equal(await page.locator('.library-card-title > button').first().innerText(),'Recording 1199');
  assert(await page.locator('#library-more').isHidden());
  await page.locator('#search').fill('');
  await page.locator('#sort').selectOption('oldest');
  assert.equal(await page.locator('.library-card-title > button').first().innerText(),'Recording 1199');
  await page.locator('[data-filter="archive"]').click();
  assert.equal(await page.locator('.library-card').count(),1);
  assert.match(await page.locator('.library-card').innerText(),/Recording 1198/);
  await page.locator('[data-filter="favorites"]').click();
  assert.equal(await page.locator('.library-card').count(),1);
  assert.match(await page.locator('.library-card').innerText(),/Recording 1199/);
  await page.locator('[data-filter="all"]').click();
  await page.locator('#sort').selectOption('newest');
  await page.locator('#library-more').scrollIntoViewIfNeeded();
  await page.waitForFunction(()=>document.querySelectorAll('.library-card').length>=48);
  assert((await page.locator('.library-card').count())<=72,'Approaching the end adds a bounded batch');
  await page.locator('#search').fill('Recording 0000');
  await page.locator('.library-card [data-play]').click();
  await page.waitForFunction(()=>!audio.paused && audio.currentTime>0);
  await page.evaluate(()=>audio.pause());
  await page.locator('#history-more').click();
  assert.equal(await page.locator('.history-item').count(),48);
  assert.deepEqual(errors,[]);
  console.log('PASS 1,200-recording mobile library: bounded initial DOM/history, unchanged and job-only poll preservation, full-catalog search/sort/favorites/archive, incremental scrolling, history and actual playback');
} finally {await browser.close();}
