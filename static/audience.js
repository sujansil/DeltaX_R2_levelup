(() => {
  "use strict";

  const ENDPOINT = "/api/v1/public/audience";
  const POLL_MS = 3000;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const scoreAnimations = new Map();
  let initialized = false;
  let lastPublicationAt = null;
  let previousPublication = { leaderboard: [], card_usage: [] };
  let questionSignature = "";
  let currentQuestion = null;
  let consecutiveFailures = 0;
  let clockOffset = 0;

  const $ = (id) => document.getElementById(id);
  const number = (value) => new Intl.NumberFormat().format(Number(value) || 0);
  const teamKey = (row) => String(row.id ?? row.team_id ?? "");
  const rowsByTeam = (rows) => new Map((rows || []).map((row) => [teamKey(row), row]));

  document.addEventListener("DOMContentLoaded", () => {
    startChaosField();
    fetchAudience();
    window.setInterval(fetchAudience, POLL_MS);
    window.setInterval(renderClock, 1000);
  });

  async function fetchAudience() {
    try {
      const response = await fetch(ENDPOINT, { cache: "no-store" });
      if (!response.ok) throw new Error(`Audience state ${response.status}`);
      const payload = await response.json();
      consecutiveFailures = 0;
      setConnection("live", "LIVE");
      applyPayload(payload);
      $("last-sync").textContent = `SYNC ${new Date().toLocaleTimeString([], { hour12: false })}`;
    } catch (_error) {
      consecutiveFailures += 1;
      if (consecutiveFailures >= 2) setConnection("degraded", "RECONNECTING");
    }
  }

  function applyPayload(payload) {
    const publication = payload.publication || emptyPublication();
    if (payload.server_now) clockOffset = Date.parse(payload.server_now) - Date.now();
    renderClock();
    renderPhase(payload);

    const nextQuestionSignature = JSON.stringify([payload.phase, payload.paused_at, payload.question]);
    if (nextQuestionSignature !== questionSignature) {
      renderQuestion(payload.phase, payload.question, !!payload.paused, payload.paused_at);
      currentQuestion = payload.question || null;
      questionSignature = nextQuestionSignature;
    }

    const publicationChanged = initialized && publication.published_at !== lastPublicationAt;
    if (!initialized || publicationChanged) {
      renderPublication(publication, previousPublication, publicationChanged);
      previousPublication = clonePublication(publication);
      lastPublicationAt = publication.published_at || null;
    }
    renderSolution(publication.solution, currentQuestion, publicationChanged);
    initialized = true;
  }

  function renderPhase(payload) {
    const phase = payload.phase || "idle";
    document.body.dataset.phase = phase;
    $("phase-label").textContent = phase === "idle" ? "STANDBY" : phase.toUpperCase();
    $("question-context").textContent = payload.question?.index
      ? `CURRENT QUESTION: Q${payload.question.index}`
      : "CURRENT QUESTION";
    $("question-type").textContent = payload.question?.type?.replaceAll("_", " ") || "—";
  }

  function renderQuestion(phase, question, paused, pausedAt) {
    const target = $("question-content");
    $("pause-banner").hidden = !paused;
    $("pause-banner").title = pausedAt ? `Paused ${formatDateTime(pausedAt)}` : "Question paused";

    if (phase === "idle" || !question) {
      target.innerHTML = `
        <div class="standby-state phase-enter">
          <span class="orbit-mark" aria-hidden="true"></span>
          <p>ROUND STANDBY</p>
          <h1 id="question-heading">Awaiting the next signal.</h1>
        </div>`;
      return;
    }
    if (phase === "topic") {
      target.innerHTML = `
        <div class="topic-state phase-enter">
          <p class="topic-kicker">NEXT DOMAIN</p>
          <div class="topic-number">Q${escapeHtml(question.index)}</div>
          <h1 id="question-heading">${escapeHtml(question.topic || "Untitled topic")}</h1>
          <div class="topic-trace"></div>
        </div>`;
      return;
    }

    const options = Array.isArray(question.options) ? question.options : [];
    target.innerHTML = `
      <article class="question-view phase-enter">
        <div class="question-meta">
          <span class="question-number">Q${escapeHtml(question.index)}</span>
          <span class="question-topic">${escapeHtml(question.topic || "")}</span>
        </div>
        <p class="question-kicker">${phase === "closed" ? "QUESTION CLOSED" : paused ? "QUESTION HELD" : "QUESTION LIVE"}</p>
        <h1 id="question-heading" class="question-prompt">${escapeHtml(question.prompt || "")}</h1>
        ${question.media_url ? `<img class="question-media" src="${escapeHtml(question.media_url)}" alt="Question media" />` : ""}
        <div class="options-list">${options.map((option, index) => `
          <div class="option-row" data-option-id="${escapeHtml(option.id)}">
            <span class="option-key">${optionLabel(index)}</span>
            <span class="option-text">${escapeHtml(option.text || "")}</span>
            ${option.media_url ? `<img class="option-media" src="${escapeHtml(option.media_url)}" alt="Option ${optionLabel(index)} media" />` : ""}
          </div>`).join("")}</div>
        <aside id="published-hint" class="published-hint" hidden></aside>
      </article>`;
    target.querySelectorAll("img").forEach((image) => {
      image.addEventListener("error", () => { image.hidden = true; }, { once: true });
    });
  }

  function renderPublication(next, previous, animate) {
    const context = next.question_index == null
      ? "PUBLISHED SNAPSHOT"
      : `PUBLISHED STANDINGS: AFTER Q${next.question_index}`;
    $("publication-context").textContent = next.published_at ? context : "NOT YET PUBLISHED";
    $("published-time").textContent = next.published_at
      ? `${next.question_index == null ? "LAST PUBLISHED" : `Q${next.question_index}`} · ${formatTime(next.published_at)}`
      : "—";
    renderLeaderboard(next.leaderboard || [], previous.leaderboard || [], animate);
    renderCards(next.card_usage || [], previous.card_usage || [], animate);
  }

  function renderSolution(solution, question, animate) {
    const matching = solution && question
      && Number(solution.question_index) === Number(question.index);
    const optionRows = [...document.querySelectorAll(".option-row")];
    const displayedInline = matching && optionRows.length > 0;
    const correct = new Set((displayedInline ? solution.correct_option_ids : []).map(String));
    optionRows.forEach((row) => {
      const isCorrect = correct.has(String(row.dataset.optionId));
      row.classList.toggle("correct-option", isCorrect);
      row.classList.toggle("solution-reveal", isCorrect && animate && !reducedMotion.matches);
      const marker = row.querySelector(".correct-marker");
      if (isCorrect && !marker) {
        const nextMarker = document.createElement("span");
        nextMarker.className = "correct-marker";
        nextMarker.textContent = "✓ CORRECT";
        row.appendChild(nextMarker);
      } else if (!isCorrect) {
        marker?.remove();
      }
    });

    const hint = $("published-hint");
    if (hint) {
      const hintText = matching ? String(solution.hint || "").trim() : "";
      hint.hidden = !hintText;
      hint.classList.toggle("solution-reveal", !!hintText && animate && !reducedMotion.matches);
      hint.innerHTML = hintText
        ? `<span>HINT / INSIGHT</span><p>${escapeHtml(hintText)}</p>`
        : "";
    }

    const previous = $("previous-solution");
    const needsPublicationContext = solution && !displayedInline;
    previous.hidden = !needsPublicationContext;
    previous.innerHTML = needsPublicationContext ? previousSolutionMarkup(solution) : "";
  }

  function previousSolutionMarkup(solution) {
    const ids = (solution.correct_option_ids || []).map(escapeHtml).join(", ") || "—";
    const hint = String(solution.hint || "").trim();
    return `<span>LAST SOLUTION · Q${escapeHtml(solution.question_index)}</span>
      <strong>Correct option${(solution.correct_option_ids || []).length === 1 ? "" : "s"}: ${ids}</strong>
      ${hint ? `<p><b>HINT / INSIGHT</b> ${escapeHtml(hint)}</p>` : ""}`;
  }

  function renderLeaderboard(rows, previousRows, animate) {
    const target = $("leaderboard");
    if (!rows.length) {
      cancelAllScoreAnimations();
      target.innerHTML = `<div class="empty-state">STANDINGS NOT YET PUBLISHED</div>`;
      return;
    }

    const oldPositions = new Map(
      [...target.querySelectorAll(".leader-row")].map((row) => [row.dataset.teamId, row.getBoundingClientRect().top])
    );
    const previousByTeam = rowsByTeam(previousRows);
    const existing = new Map(
      [...target.querySelectorAll(".leader-row")].map((row) => [row.dataset.teamId, row])
    );
    const fragment = document.createDocumentFragment();

    rows.forEach((entry) => {
      const key = teamKey(entry);
      const old = previousByTeam.get(key);
      const changed = !!old && (Number(old.score) !== Number(entry.score) || Number(old.rank) !== Number(entry.rank));
      const row = existing.get(key) || createLeaderboardRow(key);
      row.querySelector(".rank").textContent = `#${entry.rank ?? "—"}`;
      row.querySelector(".team-name").textContent = entry.name || "Unnamed team";
      row.classList.toggle("changed", animate && changed);
      window.clearTimeout(row._changedTimer);
      if (animate && changed) row._changedTimer = window.setTimeout(() => row.classList.remove("changed"), 1200);

      const score = row.querySelector(".score");
      const oldScore = old ? Number(old.score) : Number(entry.score);
      if (animate && old && oldScore !== Number(entry.score) && !reducedMotion.matches) {
        const displayedScore = Number.isFinite(row._displayedScore) ? row._displayedScore : oldScore;
        animateScore(score, displayedScore, Number(entry.score));
        showDelta(row, Number(entry.score) - oldScore);
      } else {
        cancelScoreAnimation(key);
        row._displayedScore = Number(entry.score);
        score.textContent = number(entry.score);
      }
      fragment.appendChild(row);
    });
    target.replaceChildren(fragment);

    if (animate && !reducedMotion.matches) {
      target.querySelectorAll(".leader-row").forEach((row) => {
        const previousTop = oldPositions.get(row.dataset.teamId);
        if (previousTop == null) return;
        const deltaY = previousTop - row.getBoundingClientRect().top;
        if (!deltaY) return;
        row.style.transition = "none";
        row.style.transform = `translateY(${deltaY}px)`;
        requestAnimationFrame(() => requestAnimationFrame(() => {
          row.style.transition = "";
          row.style.transform = "";
        }));
      });
    }
  }

  function createLeaderboardRow(key) {
    const row = document.createElement("div");
    row.className = "leader-row";
    row.dataset.teamId = key;
    row.innerHTML = `<span class="rank">#—</span><span class="team-name"></span><span class="score-block"><span class="score">0</span></span>`;
    return row;
  }

  function animateScore(element, from, to) {
    const key = element.closest(".leader-row").dataset.teamId;
    cancelScoreAnimation(key);
    const duration = window.innerWidth <= 680 ? 430 : 720;
    const start = performance.now();
    const tick = (now) => {
      const progress = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      const displayed = Math.round(from + (to - from) * eased);
      element.closest(".leader-row")._displayedScore = displayed;
      element.textContent = number(displayed);
      if (progress < 1) scoreAnimations.set(key, requestAnimationFrame(tick));
      else {
        element.textContent = number(to);
        element.closest(".leader-row")._displayedScore = to;
        scoreAnimations.delete(key);
      }
    };
    scoreAnimations.set(key, requestAnimationFrame(tick));
  }

  function showDelta(row, delta) {
    row.querySelector(".score-delta")?.remove();
    const badge = document.createElement("span");
    badge.className = `score-delta${delta < 0 ? " negative" : ""}`;
    badge.textContent = `${delta > 0 ? "+" : ""}${number(delta)}`;
    row.querySelector(".score-block").prepend(badge);
    window.setTimeout(() => badge.remove(), 1500);
  }

  function compactSwapTeam(name) {
  if (!name) return "?";

  const value = String(name).trim();

  const match = value.match(/^Team\s+(.+)$/i);

  if (match) {
    return match[1];
  }

  return value;
}

  function renderCards(rows, previousRows, animate) {
    const target = $("card-board");
    if (!rows.length) {
      target.innerHTML = `<div class="empty-state">CARD USAGE NOT YET PUBLISHED</div>`;
      return;
    }
    const old = rowsByTeam(previousRows);
    const cards = [
      ["bounty_used", "BNT"], ["freeze_used", "FRZ"],
      ["peek_used", "PEEK"], ["swap_used", "SWP"],
    ];
    target.innerHTML = rows.map((team) => {
      const before = old.get(teamKey(team));
      return `<div class="card-row" data-team-id="${escapeHtml(teamKey(team))}">
        <span class="card-team">${escapeHtml(team.team_name || "Unnamed team")}</span>
        ${cards.map(([field, short]) => {
          const used = !!team[field];
          const revealed = animate && used && before && !before[field] && !reducedMotion.matches;
          let displayValue = used ? "✓" : "—";

          if(field == "swap_used" && used) {
            displayValue = compactSwapTeam(team.swap_target_team_name);
          }
          return `<span class="card-state ${used ? "used" : ""} ${revealed ? "just-revealed" : ""}" data-short="${short}">${escapeHtml(displayValue)}</span>`;
        }).join("")}
      </div>`;
    }).join("");
  }

  function startChaosField() {
    const canvas = $("chaos-canvas");
    const context = canvas.getContext("2d", { alpha: true });
    let width = 0;
    let height = 0;
    let trajectories = [];
    let frame = 0;

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      trajectories = buildTrajectories(width <= 680 ? 260 : 520);
      draw();
    }

    function draw() {
      context.clearRect(0, 0, width, height);
      const scale = Math.min(width, height) * (width <= 680 ? .012 : .016);
      const cx = width * (width <= 680 ? .52 : .7);
      const cy = height * .48;
      trajectories.forEach((points, pathIndex) => {
        context.beginPath();
        points.forEach((point, index) => {
          const drift = Math.sin(frame * .002 + pathIndex) * 4;
          const x = cx + (point[0] + point[2] * .17) * scale + drift;
          const y = cy + (point[1] - point[2] * .09) * scale;
          if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
        });
        context.strokeStyle = pathIndex === 1 ? "rgba(163,146,178,.16)" : "rgba(138,167,177,.13)";
        context.lineWidth = .75;
        context.stroke();
      });
    }

    function animate() {
      if (!reducedMotion.matches && document.body.dataset.phase !== "paused") frame += 1;
      draw();
      requestAnimationFrame(animate);
    }

    window.addEventListener("resize", resize, { passive: true });
    resize();
    if (!reducedMotion.matches) requestAnimationFrame(animate);
  }

  function buildTrajectories(count) {
    return [0, .012, -.014].map((offset) => {
      let x = .1 + offset;
      let y = 0;
      let z = 0;
      const points = [];
      for (let index = 0; index < count + 100; index += 1) {
        const dt = .006;
        x += 10 * (y - x) * dt;
        y += (x * (28 - z) - y) * dt;
        z += (x * y - (8 / 3) * z) * dt;
        if (index >= 100) points.push([x, y, z]);
      }
      return points;
    });
  }

  function cancelScoreAnimation(key) {
    const frame = scoreAnimations.get(key);
    if (frame) cancelAnimationFrame(frame);
    scoreAnimations.delete(key);
  }
  function cancelAllScoreAnimations() {
    [...scoreAnimations.keys()].forEach(cancelScoreAnimation);
  }
  function setConnection(state, label) {
    $("connection-state").dataset.state = state;
    $("connection-state").textContent = label;
  }
  function renderClock() {
    $("server-clock").textContent = new Date(Date.now() + clockOffset).toLocaleTimeString([], { hour12: false });
  }
  function emptyPublication() {
    return { published_at: null, phase: null, question_index: null, leaderboard: [], card_usage: [], solution: null };
  }
  function clonePublication(value) {
    return JSON.parse(JSON.stringify(value || emptyPublication()));
  }
  function formatTime(value) {
    return value ? new Date(value).toLocaleTimeString([], { hour12: false }) : "—";
  }
  function formatDateTime(value) {
    return value ? new Date(value).toLocaleString([], { hour12: false }) : "—";
  }
  function optionLabel(index) {
    return index < 26 ? String.fromCharCode(65 + index) : String(index + 1);
  }
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[char]);
  }

  window.__audienceTest = {
    emptyPublication,
    optionLabel,
    rowsByTeam,
    publicationChanged: (initializedState, previousAt, nextAt) => initializedState && previousAt !== nextAt,
    cardWasRevealed: (before, after, field) => !!after?.[field] && !before?.[field],
    solutionMatchesQuestion: (solution, question) => !!solution && !!question
      && Number(solution.question_index) === Number(question.index),
  };
})();
