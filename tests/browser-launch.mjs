import { chromium } from "playwright";

// Browser checks wait on the page's own signals: navigation load, visible
// elements, page predicates, downloads and responses. How long a loaded CI host
// takes to produce them must not decide the result, so the launch and every
// context opened from this browser have no wall-clock limit. A crashed browser
// or fixture server rejects the pending wait at once; a real hang is stopped by
// the CI job timeout.
export async function launch(options = {}) {
  const browser = await chromium.launch({ timeout: 0, ...options });
  // Browser.newPage opens its context through this method, so pages created
  // directly from the browser get the same setting.
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async (...args) => {
    const context = await newContext(...args);
    context.setDefaultTimeout(0);
    return context;
  };
  return browser;
}
