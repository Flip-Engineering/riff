"use strict";
(async () => {
  const api = "https://api.github.com/repos/Flip-Engineering/riff/releases";
  const options = { headers: { Accept: "application/vnd.github+json" }, credentials: "omit" };
  const installer = release => {
    if (!release || typeof release.tag_name !== "string" || !/^v\d+\.\d+\.\d+$/.test(release.tag_name) ||
        release.draft !== false || release.prerelease || typeof release.published_at !== "string" ||
        !Number.isFinite(Date.parse(release.published_at))) return null;
    const expected = `https://github.com/Flip-Engineering/riff/releases/download/${release.tag_name}/Riff-Setup-macos-arm64.zip`;
    return Array.isArray(release.assets) && release.assets.some(asset =>
      asset?.name === "Riff-Setup-macos-arm64.zip" && asset.state === "uploaded" &&
      asset.browser_download_url === expected) ? expected : null;
  };
  // The HTML starts with the last independently verified graphical installer.
  try {
    const response = await fetch(`${api}/latest`, options);
    if (!response.ok) return;
    const release = await response.json();
    let target = installer(release);
    if (!target) {
      const previous = await fetch(`${api}?per_page=100`, options);
      if (!previous.ok) return;
      const releases = await previous.json();
      if (!Array.isArray(releases)) return;
      target = releases.filter(item => installer(item))
        .sort((a, b) => Date.parse(b.published_at) - Date.parse(a.published_at))
        .map(installer)[0];
    }
    if (!target) return;
    for (const link of document.querySelectorAll("[data-riff-download]")) {
      link.href = target;
      link.replaceChildren(document.createTextNode("Download for Mac "));
      const arrow = document.createElement("span");
      arrow.textContent = "↗";
      link.append(arrow);
    }
    document.getElementById("download-platforms").textContent = "macOS 15+ · Apple Silicon · Desktop preview";
  } catch {
    // Keep the verified installer available when GitHub cannot be reached.
  }
})();
