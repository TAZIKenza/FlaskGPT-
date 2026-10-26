// ============================================================ FlaskGPT - front-end
// L'historique vit maintenant côté serveur (SQLite) : le navigateur n'envoie plus que
// { conversation_id, prompt, source_ids, ... } et Flask retrouve le reste en base.

const SEP = "\x1e"; // séparateur envoyé par Flask : texte de la réponse | JSON de métadonnées
const state = { conversationId: null, busy: false, selectedSources: new Set() };

const $ = (selector, root = document) => root.querySelector(selector);
const output = $("#gpt-output");
const converter = new showdown.Converter({ tables: true, simplifiedAutoLink: true });

// Échappe le texte venant du serveur/utilisateur avant de l'insérer dans du HTML.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ------------------------------------------------------------ réseau + erreurs (fonctionnalité 4)
async function api(url, options = {}) {
    let response;
    try {
        response = await fetch(url, options);
    } catch {
        throw new Error("Impossible de joindre le serveur. Vérifie que Flask est lancé et ta connexion internet.");
    }
    if (!response.ok) {
        let message = `Erreur ${response.status}`;
        try { message = (await response.json()).error.message; } catch { /* réponse non JSON */ }
        throw new Error(message);
    }
    return response;
}
const apiJson = async (url, options) => (await api(url, options)).json();
const jsonOptions = (method, body) => ({
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

function showError(message) {
    $("#error-text").textContent = message;
    $("#error-banner").classList.remove("hidden");
    $("#error-banner").classList.add("flex");
}
function hideError() {
    $("#error-banner").classList.add("hidden");
    $("#error-banner").classList.remove("flex");
}

// ------------------------------------------------------------ affichage des messages
// Classes écrites en toutes lettres pour que Tailwind les détecte dans ce fichier.
const RATING_STYLES = {
    good: { border: "border-l-green-500", chip: "bg-green-100 text-green-800", label: "Satisfaisante" },
    mid: { border: "border-l-amber-400", chip: "bg-amber-100 text-amber-800", label: "Moyenne" },
    bad: { border: "border-l-red-500", chip: "bg-red-100 text-red-800", label: "Insuffisante" },
};
const BORDER_CLASSES = ["border-l-slate-200", "border-l-green-500", "border-l-amber-400", "border-l-red-500"];
const ratingLevel = (r) => (r >= 4 ? "good" : r === 3 ? "mid" : "bad");

function createMessageElement(role) {
    const clone = $("#message-template").content.firstElementChild.cloneNode(true);
    $(".role", clone).textContent = role === "user" ? "Vous" : "Assistant";
    if (role === "user") clone.classList.replace("bg-white", "bg-slate-100");
    output.appendChild(clone);
    return clone;
}

// Markdown -> HTML sécurisé, avec les citations [S1.1] transformées en liens cliquables.
function renderMarkdown(element, text, highlight = false) {
    let html = converter.makeHtml(text);
    html = html.replace(/\[(S\d+\.\d+)\]/g,
        '<a href="#" data-cite="$1" class="mx-0.5 rounded bg-indigo-100 px-1 text-xs font-semibold text-indigo-700 no-underline">$1</a>');
    element.innerHTML = DOMPurify.sanitize(html);
    if (highlight && window.hljs) {
        element.querySelectorAll("pre code").forEach((block) => hljs.highlightElement(block));
    }
}

function paintRating(article, rating) {
    const stars = $(".stars", article);
    stars.querySelectorAll("button").forEach((btn, i) => {
        btn.classList.toggle("text-amber-400", rating && i < rating);
        btn.classList.toggle("text-slate-300", !(rating && i < rating));
    });
    article.classList.remove(...BORDER_CLASSES);
    const chip = $(".rating-chip", article);
    chip.className = "rating-chip rounded-full px-2 py-0.5 text-xs font-medium";
    if (rating) {
        const style = RATING_STYLES[ratingLevel(rating)];
        article.classList.add(style.border);
        chip.className += " " + style.chip;
        chip.textContent = `${style.label} (${rating}/5)`;
    } else {
        article.classList.add("border-l-slate-200");
        chip.classList.add("hidden");
    }
}

// Affiche sous une réponse : métriques, étoiles de notation, sources.
function fillExtras(article, meta) {
    const id = meta.message_id ?? meta.id;
    $(".extras", article).classList.remove("hidden");

    const seconds = (meta.response_time_ms / 1000).toFixed(1);
    const speed = meta.response_time_ms ? Math.round(meta.completion_tokens / (meta.response_time_ms / 1000)) : 0;
    $(".metrics", article).textContent =
        `⏱ ${seconds} s · ${meta.total_tokens} tokens (prompt ${meta.prompt_tokens} / réponse ${meta.completion_tokens})` +
        `${meta.tokens_estimated ? " estimés" : ""} · ${speed} tokens/s`;

    const stars = $(".stars", article);
    stars.innerHTML = "";
    for (let i = 1; i <= 5; i++) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = "★";
        btn.title = `${i}/5`;
        btn.className = "text-xl leading-none text-slate-300 hover:text-amber-500";
        btn.addEventListener("click", async () => {
            try {
                const current = Number(article.dataset.rating || 0);
                const next = current === i ? null : i; // re-cliquer sur la même étoile enlève la note
                await api(`/api/messages/${id}/rating`, jsonOptions("PUT", { rating: next }));
                article.dataset.rating = next ?? "";
                paintRating(article, next);
            } catch (err) { showError(err.message); }
        });
        stars.appendChild(btn);
    }
    article.dataset.rating = meta.rating ?? "";
    paintRating(article, meta.rating);

    const details = $(".sources", article);
    if (meta.sources && meta.sources.length) {
        details.classList.remove("hidden");
        $("summary", details).textContent = `Sources utilisées (${meta.sources.length} extrait(s))`;
        $(".sources-list", details).innerHTML = meta.sources.map((s) => `
            <div class="excerpt rounded-lg border border-slate-200 bg-slate-50 p-3" data-ref="${esc(s.ref)}">
                <div class="mb-1 text-xs font-semibold text-slate-600">[${esc(s.ref)}] ${esc(s.source_name)}
                    <span class="font-normal text-slate-400">· pertinence ${esc(s.score)}</span></div>
                <div class="whitespace-pre-wrap text-slate-700">${esc(s.text)}</div>
            </div>`).join("");
    }
}

function renderStoredMessage(m) {
    const article = createMessageElement(m.role);
    if (m.role === "user") {
        $(".message", article).textContent = m.content;
    } else {
        renderMarkdown($(".message", article), m.content, true);
        fillExtras(article, m);
    }
}

// Clic sur une citation [S1.1] : ouvre les sources et fait clignoter l'extrait correspondant.
output.addEventListener("click", (event) => {
    const link = event.target.closest("[data-cite]");
    if (!link) return;
    event.preventDefault();
    const article = link.closest("article");
    const details = $(".sources", article);
    details.open = true;
    const excerpt = details.querySelector(`[data-ref="${link.dataset.cite}"]`);
    if (excerpt) {
        excerpt.scrollIntoView({ behavior: "smooth", block: "center" });
        excerpt.classList.remove("flash");
        void excerpt.offsetWidth; // relance l'animation
        excerpt.classList.add("flash");
    }
});

// ------------------------------------------------------------ conversations (fonctionnalités 1, 2, 3)
async function loadConversations() {
    const list = await apiJson("/api/conversations");
    const ul = $("#conversation-list");
    if (!list.length) {
        ul.innerHTML = '<li class="text-sm text-slate-400">Aucune conversation.</li>';
        return;
    }
    ul.innerHTML = list.map((c) => `
        <li><button data-id="${c.id}" class="conv-item w-full truncate rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-200 ${
            c.id === state.conversationId ? "bg-slate-200 font-semibold" : ""}">
            ${esc(c.title)}
            <span class="block text-xs font-normal text-slate-400">${esc(c.domain_label)} · ${esc(c.created_at.slice(0, 16).replace("T", " "))}</span>
        </button></li>`).join("");
}

function resetChat() {
    state.conversationId = null;
    output.innerHTML = "";
    hideError();
    loadConversations().catch(() => {});
}

async function openConversation(id) {
    try {
        const data = await apiJson(`/api/conversations/${id}`);
        state.conversationId = id;
        output.innerHTML = "";
        $("#domain").value = data.conversation.domain;
        data.messages.forEach(renderStoredMessage);
        hideError();
        await loadConversations();
        window.scrollTo({ top: document.body.scrollHeight });
    } catch (err) { showError(err.message); }
}

$("#conversation-list").addEventListener("click", (event) => {
    const item = event.target.closest(".conv-item");
    if (item) openConversation(Number(item.dataset.id));
});

// Fonctionnalité 1 : "Nouvelle conversation" repart d'une page vide (la conversation précédente reste dans l'historique).
$("#btn-new").addEventListener("click", resetChat);

// ... et "Effacer" supprime définitivement la conversation en cours.
$("#btn-delete").addEventListener("click", async () => {
    if (!state.conversationId) { resetChat(); return; }
    if (!confirm("Supprimer définitivement cette conversation ?")) return;
    try {
        await api(`/api/conversations/${state.conversationId}`, { method: "DELETE" });
        resetChat();
    } catch (err) { showError(err.message); }
});

// Fonctionnalité 2 : le domaine choisi est enregistré sur la conversation en cours.
$("#domain").addEventListener("change", async (event) => {
    if (!state.conversationId) return; // sinon il sera utilisé à la création
    try {
        await api(`/api/conversations/${state.conversationId}`, jsonOptions("PATCH", { domain: event.target.value }));
        await loadConversations();
    } catch (err) { showError(err.message); }
});

// ------------------------------------------------------------ envoi d'un message + streaming
async function readStream(reader, article) {
    const decoder = new TextDecoder("utf-8");
    const target = $(".message", article);
    let text = "";
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            text += decoder.decode(value, { stream: true });
            renderMarkdown(target, text.split(SEP)[0]);
            article.scrollIntoView({ block: "nearest" });
        }
        text += decoder.decode();
    } finally {
        reader.releaseLock();
    }
    const [answer, rawMeta] = text.split(SEP);
    renderMarkdown(target, answer, true);
    const meta = rawMeta ? JSON.parse(rawMeta) : null;
    if (!meta || meta.error) {
        const message = meta?.error?.message ?? "La réponse a été interrompue.";
        article.classList.add("border-l-red-500");
        showError(message);
        return;
    }
    fillExtras(article, meta);
}

function selectedSourceOptions() {
    return {
        source_ids: [...state.selectedSources],
        excerpts: Number($("#opt-excerpts").value),
        max_lines: Number($("#opt-lines").value),
    };
}

const form = $("#prompt-form");
const button = $("button", form);

function setBusy(busy) {
    state.busy = busy;
    button.disabled = busy;
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const prompt = form.elements.prompt.value.trim();
    if (!prompt || state.busy) return;
    setBusy(true);
    hideError();

    let userArticle, answerArticle;
    try {
        // 1re question : on crée la conversation en base avec le domaine choisi.
        if (!state.conversationId) {
            const conv = await apiJson("/api/conversations", jsonOptions("POST", { domain: $("#domain").value }));
            state.conversationId = conv.id;
        }
        userArticle = createMessageElement("user");
        $(".message", userArticle).textContent = prompt;
        form.reset();
        answerArticle = createMessageElement("assistant");
        $(".message", answerArticle).textContent = "…";

        const response = await api("/prompt", jsonOptions("POST", {
            conversation_id: state.conversationId, prompt, ...selectedSourceOptions(),
        }));
        await readStream(response.body.getReader(), answerArticle);
        await loadConversations();
    } catch (err) {
        // Échec avant le début de la réponse : on retire les blocs et on rend la question à l'utilisateur.
        userArticle?.remove();
        answerArticle?.remove();
        form.elements.prompt.value = prompt;
        showError(err.message);
    } finally {
        setBusy(false);
    }
});

// Entrée = envoyer, Maj+Entrée = retour à la ligne.
form.elements.prompt.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
    }
});

// ------------------------------------------------------------ export + résumé PDF (fonctionnalités 5 et 6)
async function downloadFile(url) {
    const response = await api(url);
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const name = (/filename="?([^";]+)"?/.exec(disposition) || [])[1] || "export";
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    link.click();
    URL.revokeObjectURL(link.href);
}

async function withButton(btn, label, task) {
    if (!state.conversationId) { showError("Ouvre ou démarre d'abord une conversation."); return; }
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = label;
    try { await task(); } catch (err) { showError(err.message); }
    finally { btn.disabled = false; btn.textContent = original; }
}

$("#btn-export").addEventListener("click", (event) =>
    withButton(event.currentTarget, "Export…", () =>
        downloadFile(`/api/conversations/${state.conversationId}/export?format=${$("#export-format").value}`)));

$("#btn-summary").addEventListener("click", (event) =>
    withButton(event.currentTarget, "Génération…", () =>
        downloadFile(`/api/conversations/${state.conversationId}/summary.pdf`)));

// ------------------------------------------------------------ panneaux
function togglePanel(name) {
    const panel = $(`#panel-${name}`);
    const wasHidden = panel.classList.contains("hidden");
    document.querySelectorAll(".panel").forEach((p) => p.classList.add("hidden"));
    if (!wasHidden) return;
    panel.classList.remove("hidden");
    if (name === "stats") loadStats();
    if (name === "sources") loadSources();
}
document.querySelectorAll(".panel-btn").forEach((btn) =>
    btn.addEventListener("click", () => togglePanel(btn.dataset.panel)));
$("#error-close").addEventListener("click", hideError);

// ------------------------------------------------------------ sources (fonctionnalité 10)
async function loadSources() {
    try {
        const list = await apiJson("/api/sources");
        $("#source-list").innerHTML = list.length ? list.map((s) => `
            <li class="flex items-center gap-2">
                <label class="flex min-w-0 flex-1 items-center gap-2">
                    <input type="checkbox" class="source-check" value="${s.id}" ${state.selectedSources.has(s.id) ? "checked" : ""}>
                    <span class="truncate">${esc(s.name)}</span>
                    <span class="text-xs text-slate-400">${s.chars} car.</span>
                </label>
                <button data-del="${s.id}" class="text-slate-400 hover:text-red-600" title="Supprimer">✕</button>
            </li>`).join("") : '<li class="text-slate-400">Aucune source importée.</li>';
    } catch (err) { showError(err.message); }
}

$("#source-list").addEventListener("change", (event) => {
    if (!event.target.classList.contains("source-check")) return;
    const id = Number(event.target.value);
    event.target.checked ? state.selectedSources.add(id) : state.selectedSources.delete(id);
});

$("#source-list").addEventListener("click", async (event) => {
    const id = event.target.dataset.del;
    if (!id) return;
    try {
        await api(`/api/sources/${id}`, { method: "DELETE" });
        state.selectedSources.delete(Number(id));
        await loadSources();
    } catch (err) { showError(err.message); }
});

$("#source-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    try {
        const created = await apiJson("/api/sources", { method: "POST", body });
        state.selectedSources.add(created.id); // une source importée est cochée par défaut
        await loadSources();
    } catch (err) { showError(err.message); }
    event.target.value = "";
});

// ------------------------------------------------------------ qualité des données (fonctionnalité 7)
const scoreStyle = (v) => v >= 75
    ? { box: "bg-green-100 text-green-800", bar: "bg-green-500" }
    : v >= 50
        ? { box: "bg-amber-100 text-amber-800", bar: "bg-amber-400" }
        : { box: "bg-red-100 text-red-800", bar: "bg-red-500" };

function renderReport(r) {
    const top = scoreStyle(r.global_score);
    const cards = Object.entries(r.scores).map(([key, v]) => `
        <div class="rounded-xl border border-slate-200 p-3">
            <div class="text-xs text-slate-500">${esc(r.labels[key])}</div>
            <div class="text-xl font-bold">${v}<span class="text-sm font-normal text-slate-400">/100</span></div>
            <div class="mt-1 h-2 rounded bg-slate-200"><div class="h-2 rounded ${scoreStyle(v).bar}" style="width:${v}%"></div></div>
        </div>`).join("");
    const rows = r.columns_report.map((c) => `
        <tr class="border-t border-slate-100 align-top">
            <td class="py-2 pr-3 font-medium">${esc(c.name)}</td>
            <td class="py-2 pr-3 text-slate-500">${esc(c.dtype)}</td>
            <td class="py-2 pr-3">${c.missing} (${c.missing_pct}%)</td>
            <td class="py-2 pr-3">${c.unique}</td>
            <td class="py-2">${c.issues.length ? "<ul class='list-disc pl-4'>" + c.issues.map((i) => `<li>${esc(i)}</li>`).join("") + "</ul>" : '<span class="text-green-700">OK</span>'}</td>
        </tr>`).join("");
    $("#report").innerHTML = `
        <div class="flex flex-wrap items-center gap-4">
            <div class="rounded-2xl px-5 py-3 text-center ${top.box}">
                <div class="text-3xl font-bold">${r.global_score}</div><div class="text-xs">${esc(r.label)}</div>
            </div>
            <div class="text-slate-600">${esc(r.filename)} · ${r.rows} lignes · ${r.columns} colonnes ·
                ${r.missing_cells} cellule(s) vide(s) · ${r.duplicate_rows} ligne(s) dupliquée(s)</div>
        </div>
        <div class="mt-4 grid grid-cols-2 gap-3 md:grid-cols-5">${cards}</div>
        <div class="mt-4 overflow-x-auto"><table class="w-full text-left text-sm">
            <thead class="text-xs uppercase text-slate-400"><tr><th class="pr-3">Colonne</th><th class="pr-3">Type</th>
            <th class="pr-3">Manquantes</th><th class="pr-3">Uniques</th><th>Problèmes détectés</th></tr></thead>
            <tbody>${rows}</tbody></table></div>
        <h3 class="mt-4 font-semibold">Recommandations</h3>
        <ul class="list-disc pl-5">${r.recommendations.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        <button id="btn-discuss" class="mt-4 rounded-lg border border-indigo-300 px-3 py-2 text-indigo-700 hover:bg-indigo-50">
            Discuter de ce rapport avec l'assistant</button>`;
    $("#btn-discuss").addEventListener("click", async () => {
        $("#domain").value = "gouvernance";
        $("#domain").dispatchEvent(new Event("change"));
        form.elements.prompt.value = r.summary_text;
        form.elements.prompt.focus();
    });
}

$("#btn-analyze").addEventListener("click", async (event) => {
    const file = $("#data-file").files[0];
    if (!file) { showError("Choisis d'abord un fichier à analyser."); return; }
    const btn = event.currentTarget;
    const body = new FormData();
    body.append("file", file);
    btn.disabled = true;
    btn.textContent = "Analyse…";
    try {
        renderReport(await apiJson("/api/analyze", { method: "POST", body }));
        hideError();
    } catch (err) { showError(err.message); }
    finally { btn.disabled = false; btn.textContent = "Analyser"; }
});

// ------------------------------------------------------------ performances (fonctionnalité 8)
const fmtTime = (ms) => (ms == null ? "-" : (ms / 1000).toFixed(1) + " s");

function statCards(s, title) {
    const items = [["Réponses", s.answers], ["Temps moyen", fmtTime(s.avg_time_ms)],
        ["Tokens totaux", s.total_tokens], ["Note moyenne", s.avg_rating ? s.avg_rating + "/5" : "-"]];
    return `<h3 class="mb-2 font-semibold">${esc(title)}</h3>
        <div class="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">${items.map(([k, v]) => `
            <div class="rounded-xl border border-slate-200 p-3"><div class="text-xs text-slate-500">${k}</div>
            <div class="text-xl font-bold">${esc(v)}</div></div>`).join("")}</div>`;
}

async function loadStats() {
    const box = $("#stats-content");
    try {
        const url = "/api/stats" + (state.conversationId ? `?conversation_id=${state.conversationId}` : "");
        const data = await apiJson(url);
        let html = "";
        if (data.conversation) {
            const msgs = data.conversation.messages;
            const maxTime = Math.max(1, ...msgs.map((m) => m.response_time_ms || 0));
            const maxTok = Math.max(1, ...msgs.map((m) => m.total_tokens || 0));
            html += statCards(data.conversation, "Conversation en cours");
            html += `<table class="mb-6 w-full text-left text-sm"><thead class="text-xs uppercase text-slate-400"><tr>
                <th class="pr-3">#</th><th class="pr-3">Temps</th><th class="pr-3">Tokens</th><th class="pr-3">Tokens/s</th><th>Note</th></tr></thead><tbody>` +
                msgs.map((m, i) => {
                    const speed = m.response_time_ms ? Math.round(m.completion_tokens / (m.response_time_ms / 1000)) : 0;
                    const style = m.rating ? RATING_STYLES[ratingLevel(m.rating)].chip : "";
                    return `<tr class="border-t border-slate-100">
                        <td class="py-2 pr-3">${i + 1}</td>
                        <td class="py-2 pr-3"><div class="mb-1">${fmtTime(m.response_time_ms)}</div>
                            <div class="h-1.5 rounded bg-slate-200"><div class="h-1.5 rounded bg-indigo-500" style="width:${(m.response_time_ms || 0) / maxTime * 100}%"></div></div></td>
                        <td class="py-2 pr-3"><div class="mb-1">${m.total_tokens ?? "-"}${m.tokens_estimated ? " (est.)" : ""}</div>
                            <div class="h-1.5 rounded bg-slate-200"><div class="h-1.5 rounded bg-sky-500" style="width:${(m.total_tokens || 0) / maxTok * 100}%"></div></div></td>
                        <td class="py-2 pr-3">${speed}</td>
                        <td class="py-2">${m.rating ? `<span class="rounded-full px-2 py-0.5 text-xs ${style}">${m.rating}/5</span>` : "-"}</td></tr>`;
                }).join("") + "</tbody></table>";
        } else {
            html += '<p class="mb-4 text-slate-500">Ouvre une conversation pour voir le détail réponse par réponse.</p>';
        }
        html += statCards(data.global, `Toutes conversations (${data.global.conversations})`);
        box.innerHTML = html;
    } catch (err) { showError(err.message); }
}

// ------------------------------------------------------------ démarrage
loadConversations().catch((err) => showError(err.message));
