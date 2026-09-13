"use strict";
(async () => {
  // Keep the release page usable while a desktop build is being published.
  try {
    const response = await fetch("https://api.github.com/repos/Flip-Engineering/riff/releases/latest", {
      headers: { Accept: "application/vnd.github+json" }, credentials: "omit",
    });
    if (!response.ok) return;
    const release = await response.json();
    if (!/^v\d+\.\d+\.\d+$/.test(release.tag_name) || release.draft) return;
    const asset = release.assets?.find(item => item.name === "Riff-Setup-macos-arm64.zip" && item.state === "uploaded");
    if (!asset) return;
    const expected = `https://github.com/Flip-Engineering/riff/releases/download/${release.tag_name}/Riff-Setup-macos-arm64.zip`;
    if (asset.browser_download_url !== expected) return;
    for (const link of document.querySelectorAll("[data-riff-download]")) {
      link.href = expected;
      link.replaceChildren(document.createTextNode("Download for Mac "));
      const arrow = document.createElement("span");
      arrow.textContent = "↗";
      link.append(arrow);
    }
    document.getElementById("download-platforms").textContent = "macOS 15+ · Apple Silicon · Desktop preview";
  } catch {
    // The existing release link remains useful when GitHub cannot be reached.
  }
})();
