// Sem scripts inline: a CSP do painel só permite JS servido pelo próprio painel.
// As ações de um post são enviadas por fetch e atualizam apenas o card — a página não recarrega
// e os filtros (aba, nicho, categoria, página) continuam onde estavam.

function aviso(texto, tipo) {
  const el = document.getElementById("aviso");
  if (!el) return;
  el.textContent = texto;
  el.className = "msg " + (tipo || "ok");
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.hidden = true; }, 4000);
}

function pinta(card, dados) {
  const set = (sel, valor) => { const n = card.querySelector(sel); if (n !== null && valor !== undefined) n.textContent = valor; };
  card.querySelector(".bubble").innerHTML = dados.text_html;
  set(".st", dados.status);
  set(".pc", dados.price_checked);
  set(".sc", dados.score);
  const nota = card.querySelector(".note");
  if (nota) nota.textContent = dados.note ? "📝 " + dados.note : "";
  const stale = card.querySelector(".stale");
  if (stale) stale.hidden = !dados.stale;
  const aprovar = card.querySelector('form[action$="/approve"]');
  if (aprovar && dados.status !== "pending") aprovar.hidden = true;
}

function contadores(dados) {
  if (!dados.counts) return;
  document.querySelectorAll("[data-cat]").forEach((chip) => {
    const n = dados.counts[chip.dataset.cat] || 0;
    chip.textContent = chip.dataset.nome + " (" + n + ")";
    chip.classList.toggle("vazia", n === 0 && !chip.classList.contains("on"));
  });
  const todas = document.querySelector("[data-cat-todas]");
  if (todas) todas.textContent = "todas (" + (dados.total || 0) + ")";
}

function some(card) {
  card.style.transition = "opacity .25s";
  card.style.opacity = "0";
  setTimeout(() => card.remove(), 250);
}

document.addEventListener("submit", async (ev) => {
  const form = ev.target;
  if (!form.classList.contains("js")) return;      // formulários comuns continuam com POST normal
  ev.preventDefault();
  const card = form.closest(".card");
  const botoes = form.querySelectorAll("button");
  botoes.forEach((b) => (b.disabled = true));
  try {
    const r = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Requested-With": "fetch" },
      credentials: "same-origin",
    });
    const dados = await r.json().catch(() => ({}));
    if (!r.ok) { aviso(dados.erro || "Não foi possível concluir a ação.", "bad"); return; }
    contadores(dados);
    if (dados.removed) { aviso("Post saiu da fila: " + dados.status_pt + ".", "ok"); some(card); return; }
    pinta(card, dados);
    aviso("Alteração salva.", "ok");
  } catch (e) {
    aviso("Falha de conexão com o painel.", "bad");
  } finally {
    botoes.forEach((b) => (b.disabled = false));
  }
});

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
