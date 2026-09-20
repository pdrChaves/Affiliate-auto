// Sem scripts inline: a CSP do painel só permite JS servido pelo próprio painel.
document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-copy]");
  if (!btn) return;
  const ta = document.getElementById(btn.dataset.copy);
  const out = document.getElementById("copy-status");
  try {
    await navigator.clipboard.writeText(ta.value);
  } catch (e) {
    ta.select();
    document.execCommand("copy");
  }
  if (out) out.textContent = "Copiado!";
});
