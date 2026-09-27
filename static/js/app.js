// ------------------------------------------------------------------
// OmniaMind — utilitaires JS partagés par toutes les pages
// ------------------------------------------------------------------

// Ré-exécute le rendu des icônes Lucide après une injection dynamique
// de HTML (toast, résumé de session, badge débloqué...).
function refreshIcons() {
  if (window.lucide) lucide.createIcons();
}

function toast(message, opts = {}) {
  const stack = document.getElementById("toast-stack");
  if (!stack) return;
  const el = document.createElement("div");
  el.className = "toast-in glass rounded-xl px-4 py-3 text-sm font-semibold shadow-lg text-slate-100 flex items-center gap-2";
  el.innerHTML = `<i data-lucide="${opts.icon || "check-circle-2"}" class="w-4 h-4 shrink-0 text-omnia-orange"></i><span>${message}</span>`;
  stack.appendChild(el);
  refreshIcons();
  setTimeout(() => {
    el.style.transition = "opacity .3s ease, transform .3s ease";
    el.style.opacity = "0";
    el.style.transform = "translateX(20px)";
    setTimeout(() => el.remove(), 300);
  }, 2600);
}

function fireConfetti(strength = 1) {
  if (typeof confetti !== "function") return;
  const colors = ["#ff5f3d", "#ff3d6a", "#ffb84d", "#ffffff"];
  confetti({ particleCount: 90 * strength, spread: 80, origin: { y: 0.6 }, colors });
  setTimeout(() => confetti({ particleCount: 60 * strength, spread: 120, origin: { y: 0.5 }, colors }), 180);
}

/**
 * Réinjecte une carte ratée plus loin dans la file de la session en cours
 * (espacement massé) au lieu de la laisser disparaître jusqu'à la prochaine
 * visite. Plafonné à 2 réinjections par carte pour ne jamais boucler
 * indéfiniment sur une carte très difficile. `requeueMap` est un objet
 * {clé: nombre de réinjections déjà faites} tenu par la page appelante.
 */
function requeueAfterMiss(queue, idx, item, requeueMap, key) {
  const count = requeueMap[key] || 0;
  if (count >= 2) return false;
  requeueMap[key] = count + 1;
  const gap = 5 + Math.floor(Math.random() * 3); // 5 à 7 cartes plus tard
  const insertAt = Math.min(queue.length, idx + 1 + gap);
  queue.splice(insertAt, 0, item);
  return true;
}

// ------------------------------------------------------------------
// Synthèse vocale multilingue. Le moteur TTS du navigateur lit dans la
// langue qu'on lui donne explicitement (utter.lang) — sans ça, il utilise
// la langue de l'interface pour tout, d'où "I don't mind" prononcé à la
// française. On détecte la langue probable du texte (ou on force celle
// choisie pour le deck) avant de parler.
// ------------------------------------------------------------------
const LANG_TAGS = { fr: "fr-FR", en: "en-US", es: "es-ES", de: "de-DE", it: "it-IT" };

// Mots-outils très fréquents par langue, entourés d'espaces pour éviter
// les faux positifs sur des sous-chaînes ("est" dans "reste", etc.).
const LANG_HINTS = {
  en: [" the ", " and ", " is ", " you ", " don't ", " doesn't ", " i'm ", " it's ", " can't ", " with ", " that ", " this ", " what ", " who ", " how ", " are ", " your "],
  fr: [" le ", " la ", " les ", " et ", " est ", " vous ", " ne ", " pas ", " avec ", " que ", " qui ", " quoi ", " comment ", " être ", " des ", " une ", " un "],
  es: [" el ", " la ", " los ", " es ", " y ", " qué ", " cómo ", " porque ", " está ", " para ", " con "],
  de: [" der ", " die ", " das ", " und ", " ist ", " nicht ", " mit ", " warum ", " für "],
};

function detectSpeechLang(text) {
  const t = ` ${(text || "").toLowerCase()} `;
  let best = "fr", bestScore = 0;
  for (const [lang, hints] of Object.entries(LANG_HINTS)) {
    let score = 0;
    for (const h of hints) if (t.includes(h)) score++;
    if (score > bestScore) { bestScore = score; best = lang; }
  }
  return best; // "fr" par défaut si aucun signal net (texte court, nom propre...)
}

function _loadVoices() {
  return new Promise((resolve) => {
    const existing = speechSynthesis.getVoices();
    if (existing.length) { resolve(existing); return; }
    speechSynthesis.onvoiceschanged = () => resolve(speechSynthesis.getVoices());
    setTimeout(() => resolve(speechSynthesis.getVoices()), 500); // filet de sécurité
  });
}

/**
 * Lit un texte à voix haute avec le bon accent. `deckLang` est le réglage
 * du deck ("auto", "fr", "en"...) : "auto" détecte automatiquement à
 * partir du texte lui-même (utile pour un deck qui mélange les langues,
 * comme un mot anglais associé à sa définition en français), sinon la
 * langue choisie est forcée. Retourne une promesse résolue quand la
 * lecture est terminée, pour pouvoir enchaîner plusieurs textes (Podcast).
 */
async function speakText(text, deckLang = "auto", rate = 0.95) {
  if (!("speechSynthesis" in window) || !text) return;
  speechSynthesis.cancel();
  const lang = (deckLang && deckLang !== "auto") ? deckLang : detectSpeechLang(text);
  const tag = LANG_TAGS[lang] || LANG_TAGS.fr;
  const voices = await _loadVoices();
  return new Promise((resolve) => {
    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = tag;
    utter.rate = rate;
    const voice = voices.find(v => v.lang === tag) || voices.find(v => v.lang.startsWith(tag.split("-")[0]));
    if (voice) utter.voice = voice;
    utter.onend = () => resolve();
    utter.onerror = () => resolve();
    speechSynthesis.speak(utter);
  });
}

function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : "";
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}

function showLevelUp(newLevel) {
  const overlay = document.getElementById("level-up-overlay");
  const nb = document.getElementById("new-lvl-nb");
  if (!overlay) return;
  if (nb) nb.textContent = `Niveau ${newLevel}`;
  overlay.classList.remove("hidden");
  overlay.classList.add("flex");
  fireConfetti(1.6);
}

// ------------------------------------------------------------------
// File d'attente des succès (badges) débloqués. Alimentée soit par
// le rendu serveur (PENDING_BADGES au chargement, cf. base.html),
// soit par les réponses JSON des actions (fin de session, favori...).
// Les badges s'affichent un par un pour ne jamais se chevaucher.
// ------------------------------------------------------------------
let badgeQueue = [];
let badgeShowing = false;

function enqueueBadges(badges) {
  if (!badges || !badges.length) return;
  badgeQueue.push(...badges);
  if (!badgeShowing) showNextBadge();
}

function showNextBadge() {
  const overlay = document.getElementById("badge-unlock-overlay");
  if (!overlay || badgeQueue.length === 0) { badgeShowing = false; return; }
  badgeShowing = true;
  const badge = badgeQueue.shift();
  const icon = document.getElementById("badge-icon");
  icon.setAttribute("data-lucide", badge.icon || "award");
  document.getElementById("badge-label").textContent = badge.label;
  document.getElementById("badge-desc").textContent = badge.desc;
  overlay.classList.remove("hidden");
  overlay.classList.add("flex");
  const pop = icon.closest(".badge-pop") || icon.parentElement;
  if (pop) { pop.classList.remove("badge-pop"); void pop.offsetWidth; pop.classList.add("badge-pop"); }
  refreshIcons();
  fireConfetti(1.2);
}

function closeBadgeOverlay() {
  const overlay = document.getElementById("badge-unlock-overlay");
  if (!overlay) return;
  overlay.classList.add("hidden");
  overlay.classList.remove("flex");
  setTimeout(showNextBadge, 250);
}

// Animation "roll-up" d'un compteur numérique (utilisé en fin de session)
function rollUp(el, target, duration = 900, suffix = "", decimals = 0) {
  const start = 0;
  const startTime = performance.now();
  function frame(now) {
    const p = Math.min(1, (now - startTime) / duration);
    const eased = 1 - Math.pow(1 - p, 3);
    const value = start + (target - start) * eased;
    el.textContent = value.toFixed(decimals) + suffix;
    if (p < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

/**
 * Appelé à la fin de n'importe quel mode de révision.
 * Envoie le score, applique XP/streak côté serveur, déclenche
 * confettis + level up + succès débloqués si besoin. Retourne la
 * réponse JSON du serveur.
 */
async function completeStudySession(deckId, mode, score) {
  const data = await postJSON("/api/study/complete", { deck_id: deckId, mode, score });
  if (data.level_up) {
    setTimeout(() => showLevelUp(data.lvl.level), 500);
  }
  if (data.new_badges && data.new_badges.length) {
    setTimeout(() => enqueueBadges(data.new_badges), data.level_up ? 3000 : 500);
  }
  return data;
}

function toggleFavorite(deckId, btn) {
  postJSON(`/favorite/${deckId}/toggle`).then((data) => {
    if (!btn) return;
    btn.classList.toggle("text-omnia-red", data.favorited);
    const icon = btn.querySelector('[data-lucide="heart"]');
    if (icon) icon.classList.toggle("fill-current", data.favorited);
    // Certains boutons (deck_detail) ont un libellé texte après l'icône ;
    // d'autres (explore) ne contiennent que l'icône : on met à jour le
    // libellé uniquement s'il existe, sans jamais toucher à l'icône.
    for (const node of btn.childNodes) {
      if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
        node.textContent = data.favorited ? "Favori" : "Ajouter aux favoris";
      }
    }
    if (data.favorited) toast("Ajouté aux favoris", { icon: "heart" });
    if (data.new_badges && data.new_badges.length) enqueueBadges(data.new_badges);
  });
}

// ------------------------------------------------------------------
// Effet ripple sur tous les boutons ".btn-ripple" (délégation globale)
// ------------------------------------------------------------------
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".btn-ripple");
  if (!btn) return;
  const rect = btn.getBoundingClientRect();
  const ripple = document.createElement("span");
  ripple.className = "ripple";
  ripple.style.left = `${e.clientX - rect.left}px`;
  ripple.style.top = `${e.clientY - rect.top}px`;
  btn.appendChild(ripple);
  setTimeout(() => ripple.remove(), 650);
});
