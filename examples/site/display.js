/* SPDX-License-Identifier: MIT
 * Display only: all tone values are compiled by the single Python rule source.
 */
(() => {
  "use strict";
  const button = document.getElementById("tone-mode");
  if (!button) return;
  const rows = document.querySelectorAll("#tone-examples [data-citation][data-spoken]");
  button.addEventListener("click", () => {
    const spoken = button.getAttribute("aria-pressed") !== "true";
    for (const row of rows) {
      row.textContent = spoken ? row.dataset.spoken : row.dataset.citation;
    }
    button.setAttribute("aria-pressed", String(spoken));
  });
})();
