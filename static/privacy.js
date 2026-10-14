const financialPrivacy = (() => {
  const preferenceKey = "hide-financial-values";
  const toggle = document.querySelector("#privacy-toggle");
  const shell = document.querySelector(".shell");
  const textValues = new WeakMap();
  const attributeValues = new WeakMap();
  const inputTypes = new WeakMap();
  const chartStates = new WeakMap();
  const chartSelector = ".chart-area, .category-month-chart, .bar-track, .return-track, .budget-track, .reserve-progress-track";
  let hidden = false;
  try { hidden = window.localStorage.getItem(preferenceKey) === "true"; } catch (_) { /* Preference is optional. */ }

  function mask(text) {
    return text
      .replace(/(?:[+−-]\s*)?R\$\s*(?:[+−-]\s*)?\d[\d.,]*(?:\s*(?:milhões|milhão|mil|mi|bi))?/gi, "R$ ••••")
      .replace(/[+−-]?\d+(?:[.,]\d+)?\s*%/g, "••••%")
      .replace(/\b\d+(?:[.,]\d+)?\s+cotas\b/gi, "•••• cotas");
  }

  function updateValue(key, read, write, saved, masker = mask) {
    const current = read();
    const previous = saved.get(key);
    if (!hidden) {
      if (previous && current === previous.masked) write(previous.original);
      saved.delete(key);
      return;
    }
    if (previous && current === previous.masked) return;
    const masked = masker(current);
    if (masked === current) { saved.delete(key); return; }
    saved.set(key, { original: current, masked });
    write(masked);
  }

  function updateText(node) {
    const privateValue = node.parentElement?.closest("[data-private-value]");
    updateValue(node, () => node.nodeValue, value => { node.nodeValue = value; }, textValues,
      privateValue ? value => value.trim() ? "••••" : value : mask);
  }

  function updateAttributes(element) {
    let saved = attributeValues.get(element);
    if (!saved) { saved = new Map(); attributeValues.set(element, saved); }
    for (const key of ["title", "aria-label"]) {
      if (!element.hasAttribute(key)) { saved.delete(key); continue; }
      updateValue(key, () => element.getAttribute(key), value => element.setAttribute(key, value), saved);
    }
  }

  function updateInputs() {
    for (const input of shell.querySelectorAll('input[inputmode="decimal"]')) {
      if (hidden && !inputTypes.has(input)) {
        inputTypes.set(input, { type: input.getAttribute("type"), autocomplete: input.getAttribute("autocomplete") });
        input.type = "password";
        input.autocomplete = "off";
      } else if (!hidden && inputTypes.has(input)) {
        const original = inputTypes.get(input);
        for (const [name, value] of Object.entries(original)) {
          if (value === null) input.removeAttribute(name); else input.setAttribute(name, value);
        }
        inputTypes.delete(input);
      }
    }
  }

  function updateCharts() {
    for (const chart of shell.querySelectorAll(chartSelector)) {
      chart.setAttribute("data-privacy-chart", "");
      if (hidden && !chartStates.has(chart)) {
        chartStates.set(chart, { aria: chart.getAttribute("aria-hidden"), inert: chart.inert });
        chart.setAttribute("aria-hidden", "true");
        chart.inert = true;
      } else if (!hidden && chartStates.has(chart)) {
        const original = chartStates.get(chart);
        if (original.aria === null) chart.removeAttribute("aria-hidden");
        else chart.setAttribute("aria-hidden", original.aria);
        chart.inert = original.inert;
        chartStates.delete(chart);
      }
    }
  }

  const observation = { subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ["title", "aria-label", "data-private-value"] };
  const observer = new MutationObserver(apply);
  function apply() {
    // Pause our own mutations to avoid an observer loop. New API renders and
    // hover/focus details are handled before the next browser paint.
    observer.disconnect();
    try {
      const walker = document.createTreeWalker(shell, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) updateText(walker.currentNode);
      for (const element of shell.querySelectorAll("[title], [aria-label]")) updateAttributes(element);
      updateInputs();
      updateCharts();
    } finally { observer.observe(shell, observation); }
  }

  function setHidden(value) {
    hidden = Boolean(value);
    document.body.classList.toggle("values-hidden", hidden);
    toggle.setAttribute("aria-pressed", String(hidden));
    const label = hidden ? "Mostrar valores" : "Ocultar valores";
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
    document.querySelector("#privacy-label").textContent = label;
    toggle.querySelector(".eye-open").toggleAttribute("hidden", hidden);
    toggle.querySelector(".eye-closed").toggleAttribute("hidden", !hidden);
    try { window.localStorage.setItem(preferenceKey, String(hidden)); } catch (_) { /* Keep working without storage. */ }
    apply();
  }

  // Native confirmations can contain amounts even when the DOM is masked.
  const originalConfirm = window.confirm.bind(window);
  window.confirm = message => originalConfirm(hidden ? mask(String(message)) : message);
  toggle.addEventListener("click", () => setHidden(!hidden));
  setHidden(hidden);
  return { hidden: () => hidden, mask, apply };
})();
