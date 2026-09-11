import { spawn } from "node:child_process";
import { once } from "node:events";
import { copyFileSync } from "node:fs";
import { chromium } from "playwright";
const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"]);
server.stderr.on("data", () => {});
const fixture = await new Promise((resolve, reject) => {
 let output=""; server.stdout.on("data", data => { output += data; if(output.includes("\n")) resolve(JSON.parse(output.split("\n")[0])); });
 server.once("exit", code => reject(new Error(`Fixture exited ${code}`)));
});
const browser = await chromium.launch({headless:true, executablePath:process.env.RIFF_BROWSER_EXECUTABLE});
try {
 const page = await browser.newPage({viewport:{width:1365,height:1050},reducedMotion:"reduce"});
 await page.goto(fixture.url);
 await page.locator("#play:not([disabled])").waitFor();
 await page.locator('[name="creation-mode"][value="lyrics"]').check();
 await page.locator('#title').fill('Reedlight');
 await page.locator('#lyrics').fill('[Verse]\nReeds lean low where the silver runs.\nWe carry the quiet into the sun.\nA little light, a little room.\nA song takes shape in the afternoon.');
 await page.getByRole('button',{name:'Reed room',exact:true}).click();
 await page.locator('#duration').fill('28');
 await page.screenshot({path:'docs/studio.png'});
 await page.locator('.creative-controls > summary').click();
 await page.locator('#score-open').click();
 await page.locator('#new-score').click();
 await page.screenshot({path:'docs/composition.png'});
 await page.getByLabel('Close composition',{exact:true}).click();
 await page.setViewportSize({width:390,height:844});
 await page.evaluate(()=>scrollTo(0,0));
 await page.screenshot({path:'docs/studio-mobile.png',fullPage:true});
 for (const name of ['studio.png','composition.png']) copyFileSync(`docs/${name}`,`site/${name}`);
} finally {await browser.close();const exit=once(server,'exit');server.kill('SIGTERM');await exit;}
