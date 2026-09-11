"use strict";
(() => {
  let theme = "dark";
  try { theme = localStorage.getItem("riff.theme") || "dark"; } catch {}
  function apply(value) {
    theme = value === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#1c1216" : "#f5eee6");
    try { localStorage.setItem("riff.theme", theme); } catch {}
  }
  apply(theme);
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelector("#theme-toggle")?.addEventListener("click", () => apply(theme === "dark" ? "light" : "dark"));
  });
})();
