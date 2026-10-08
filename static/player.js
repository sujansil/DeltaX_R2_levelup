(() => {
  const authState = auth();
  if (!authState || authState.role !== "team" || !authState.access_token) {
    window.location.href = "/";
    return;
  }

  const CARD_COSTS = { bounty: 2000, freeze: 750, peek: 750 };
  const SCORE_RULES = {
    normal: { start: 2000, decay: 20, floor: 500 },
    bounty: { start: 6000, decay: 60, floor: 1500 },
    decayStartMs: 60_000,
    freezeBonusMs: 30_000,
  };

  let gameState = null;
  let selectedChoice = null;
  let selectedMulti = new Set();
  let currentQuestionId = null;
  let serverClockAnchor = null;
  let heartbeatHandle = null;
  let pollHandle = null;
  let timerFrame = null;
  let timeoutRefreshQueued = false;
  let toastTimer = null;

  const $ = (id) => document.getElementById(id);

  document.addEventListener("DOMContentLoaded", init);
  window.addEventListener("levelup:session-invalid", () => showSessionEnded());

  async function init() {
    bindControls();
    setConnection("connecting", "CONNECTING");

    const ws = connectWs((message) => {
      if (!message) return;
      const refreshTypes = new Set([
        "state", "state_tick", "leaderboard", "controller", "controller_released",
        "locked", "swap_queued", "swap_applied", "swap_notice", "card"
      ]);
      if (refreshTypes.has(message.type)) refreshState({ quiet: true });
    });
    if (ws) {
      ws.onopen = () => setConnection("online", "LIVE LINK");
      ws.onerror = () => setConnection("offline", "RECONNECTING");
    }

    await refreshState();
    pollHandle = window.setInterval(() => refreshState({ quiet: true }), 5000);
    startTimerLoop();
  }

  function bindControls() {
    $("mobile-board-button").addEventListener("click", () => toggleDrawer(true));
    document.querySelectorAll("[data-close-drawer]").forEach((el) => el.addEventListener("click", () => toggleDrawer(false)));
    document.querySelectorAll("[data-close-swap]").forEach((el) => el.addEventListener("click", () => toggleSwap(false)));

    $("submit-button").addEventListener("click", submitAnswer);
    $("controller-button").addEventListener("click", claimController);
    $("release-button").addEventListener("click", releaseController);
    $("mobile-controller-button").addEventListener("click", async () => {
      if (gameState?.i_am_controller) await releaseController();
      else await claimController();
    });
    $("logout-button").addEventListener("click", logout);
    $("session-login-button").addEventListener("click", () => {
      clearAuth();
      window.location.href = "/";
    });

    linkCardButtons("bounty", useBounty);
    linkCardButtons("freeze", useFreeze);
    linkCardButtons("peek", usePeek);
    linkCardButtons("swap", () => openSwap());
    $("confirm-swap-button").addEventListener("click", confirmSwap);
  }

  function linkCardButtons(card, handler) {
    const desktop = $(`${card}-button`);
    const mobile = $(`mobile-${card}-button`);
    desktop?.addEventListener("click", handler);
    mobile?.addEventListener("click", handler);
  }

  async function refreshState({ quiet = false } = {}) {
    try {
      const next = await api("/api/v1/play/state");
      gameState = next;
      syncServerClock(next.server_now);
      restoreSelection(next.question, next.me || {});
      renderState(next);
      syncControllerHeartbeat(next);
      setConnection("online", "LIVE LINK");
    } catch (err) {
      if (err.status === 401) {
        showSessionEnded();
        return;
      }
      setConnection("offline", "RECONNECTING");
      if (!quiet) showToast(err.message || "Unable to reach server", true);
    }
  }

  function renderState(state) {
    const phase = state?.phase || "idle";
    const q = state?.question || null;
    const me = state?.me || {};
    const team = state?.team || {};

    setPhase(phase);
    $("team-name").textContent = team.name || authState.team_name || "TEAM";
    const teamScore = formatNumber(team.score || 0);
    $("team-score").textContent = teamScore;
    $("mobile-team-score").textContent = teamScore;
    $("question-number").textContent = q?.index ? `Q${q.index}` : "—";
    $("question-topic").textContent = q?.topic || "—";
    $("topic-title").textContent = q?.topic || "Awaiting topic";

    const ownRank = findOwnRank(state.leaderboard || [], team.id);
    $("team-rank").textContent = ownRank ? `#${ownRank}` : "—";
    renderLeaderboard(state.leaderboard || [], team.id, "desktop-leaderboard");
    renderLeaderboard(state.leaderboard || [], team.id, "mobile-leaderboard");
    renderController(state);
    renderCards(state);
    renderSwapTargets(state);

    if (q) renderQuestion(q, me, state);
    else clearQuestion();

    if (phase === "closed") renderResult(me, team);
    updateDynamicReadouts();
  }

  function setPhase(phase) {
    ["idle", "topic", "live", "closed"].forEach((name) => {
      $("screen-" + name)?.classList.toggle("active", name === phase);
    });
    $("phase-pill").dataset.phase = phase;
    $("phase-pill").textContent = phase.toUpperCase();
    const copy = { idle: "WAITING", topic: "TOPIC WINDOW", live: "QUESTION LIVE", closed: "QUESTION CLOSED" };
    $("phase-copy").textContent = copy[phase] || "WAITING";
  }

  function renderQuestion(q, me, state) {
    $("question-text").textContent = q.prompt || (state.phase === "topic" ? "Question hidden until start." : "Waiting for question...");

    const img = $("question-media");
    if (q.media_url && (state.phase === "live" || state.phase === "closed")) {
      img.src = q.media_url;
      img.classList.remove("hidden");
    } else {
      img.removeAttribute("src");
      img.classList.add("hidden");
    }

    if (me.hint && state.phase === "live") {
      $("hint-text").textContent = me.hint;
      $("hint-box").classList.remove("hidden");
    } else {
      $("hint-text").textContent = "";
      $("hint-box").classList.add("hidden");
    }

    if (state.phase === "live") renderAnswers(q, me, state);
    else $("answers-container").innerHTML = "";

    updateSubmitState(q, me, state);
  }

  function clearQuestion() {
    $("question-text").textContent = "No active question.";
    $("answers-container").innerHTML = "";
    $("question-media").classList.add("hidden");
    $("hint-box").classList.add("hidden");
    $("submit-button").disabled = true;
  }

  function renderAnswers(question, me, state) {
    const options = Array.isArray(question.options) ? question.options : [];
    const qtype = question.type;
    const canInteract = !!state.i_am_controller && !me.locked && state.phase === "live";

    $("answers-container").innerHTML = options.map((option, index) => {
      const id = option.id || String.fromCharCode(97 + index);
      const selected = qtype === "mcq_multi" ? selectedMulti.has(id) : selectedChoice === id;
      const media = option.media_url
        ? `<img class="option-media" src="${escapeHtml(option.media_url)}" alt="Option ${escapeHtml(id)} image" />`
        : "";
      return `
        <button class="answer-option ${selected ? "selected" : ""} ${me.locked && selected ? "locked" : ""}"
          type="button" data-option-id="${escapeHtml(id)}" ${canInteract ? "" : "disabled"}>
          <span class="option-key">${escapeHtml(String(id).toUpperCase())}</span>
          <span class="option-text">${escapeHtml(option.text || "")}</span>
          ${media}
        </button>`;
    }).join("");

    $("answers-container").querySelectorAll(".answer-option").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.optionId;
        if (qtype === "mcq_multi") {
          if (selectedMulti.has(id)) selectedMulti.delete(id); else selectedMulti.add(id);
        } else {
          selectedChoice = id;
        }
        renderAnswers(question, me, state);
        updateSubmitState(question, me, state);
      });
    });
  }

  function updateSubmitState(question, me, state) {
    const btn = $("submit-button");
    if (state.phase !== "live") {
      btn.disabled = true;
      return;
    }
    const selected = question?.type === "mcq_multi" ? selectedMulti.size > 0 : !!selectedChoice;
    btn.disabled = !state.i_am_controller || !!me.locked || !selected;
    btn.textContent = me.locked ? "ANSWER LOCKED" : "LOCK ANSWER";

    const status = $("answer-status");
    if (me.locked) status.textContent = "Submission recorded. Your potential score is frozen.";
    else if (!state.i_am_controller) status.textContent = "View-only device. Claim control to answer.";
    else if (!selected) status.textContent = "Choose an option.";
    else status.textContent = "Ready to lock. This cannot be changed afterward.";
  }

  function renderController(state) {
    const mine = !!state.i_am_controller;
    const occupied = !!state.controller_session_id;
    $("controller-badge").dataset.active = String(mine);
    $("controller-badge").textContent = mine ? "YOU CONTROL" : "VIEW ONLY";

    const claim = $("controller-button");
    const release = $("release-button");
    const mobileControl = $("mobile-controller-button");
    mobileControl.dataset.active = String(mine);
    if (mine) {
      $("controller-copy").textContent = "This device owns the team control token.";
      claim.textContent = "CONTROL ACTIVE";
      claim.disabled = true;
      release.disabled = false;
      mobileControl.textContent = "RELEASE CONTROL";
      mobileControl.disabled = false;
    } else if (occupied) {
      $("controller-copy").textContent = "Another authenticated device currently controls team actions.";
      claim.textContent = "CONTROL IN USE";
      claim.disabled = true;
      release.disabled = true;
      mobileControl.textContent = "CONTROL IN USE";
      mobileControl.disabled = true;
    } else {
      $("controller-copy").textContent = "No device currently controls this team.";
      claim.textContent = "CLAIM CONTROL";
      claim.disabled = false;
      release.disabled = true;
      mobileControl.textContent = "CLAIM CONTROL";
      mobileControl.disabled = false;
    }
  }

  function renderCards(state) {
    const me = state?.me || {};
    const team = state?.team || {};
    // Power-up availability is governed by gameplay phase, controller ownership,
    // score, and whether the card has already been used. `reveal_cards` is a
    // legacy/admin reveal flag and must not gate gameplay access.
    $("power-lock-copy").textContent = "AVAILABLE";

    const specs = {
      bounty: {
        used: !!me.bounty,
        enabled: state.i_am_controller && state.phase === "topic" && !me.bounty && Number(team.score || 0) >= CARD_COSTS.bounty,
      },
      freeze: {
        used: !!me.freeze,
        enabled: state.i_am_controller && state.phase === "live" && !me.freeze && Number(team.score || 0) >= CARD_COSTS.freeze,
      },
      peek: {
        used: !!me.peek,
        enabled: state.i_am_controller && state.phase === "live" && !me.peek && Number(team.score || 0) >= CARD_COSTS.peek,
      },
      swap: {
        used: !!me.swap_used,
        unavailable: !me.swap_used && team.swap_available === false,
        enabled: state.i_am_controller && state.phase === "live" && !!me.locked && !me.swap_used && team.swap_available !== false,
      },
    };

    Object.entries(specs).forEach(([name, spec]) => {
      [$(`${name}-button`), $(`mobile-${name}-button`)].filter(Boolean).forEach((btn) => {
        btn.disabled = !spec.enabled;
        btn.classList.toggle("used", spec.used);
        btn.classList.toggle("unavailable", !!spec.unavailable);
        btn.dataset.used = String(spec.used);
        btn.dataset.unavailable = String(!!spec.unavailable);
        btn.title = spec.used ? "Already used" : (spec.unavailable ? "Unavailable" : "");
      });
    });
  }

  function renderLeaderboard(rows, ownTeamId, targetId) {
    const target = $(targetId);
    if (!target) return;
    if (!rows.length) {
      target.innerHTML = `<div class="board-row"><span class="board-rank">—</span><span class="board-name">No published standings yet.</span><span></span></div>`;
      return;
    }
    target.innerHTML = [...rows]
      .sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999) || String(a.name).localeCompare(String(b.name)))
      .slice(0, 16)
      .map((row, index) => {
        const own = row.id === ownTeamId;
        return `
          <div class="board-row ${own ? "you" : ""}">
            <span class="board-rank">#${row.rank ?? index + 1}</span>
            <span class="board-name">${escapeHtml(row.name)}${own ? '<small class="board-you">YOUR TEAM</small>' : ""}</span>
            <span class="board-score">${formatNumber(row.score || 0)}</span>
          </div>`;
      }).join("");
  }

  function renderSwapTargets(state) {
    const ownId = state?.team?.id;
    const rows = (state?.leaderboard || []).filter((t) => t.id !== ownId);
    $("swap-target").innerHTML = rows.length
      ? rows.map((t) => `<option value="${escapeHtml(t.id)}">#${t.rank ?? "—"} ${escapeHtml(t.name)}</option>`).join("")
      : `<option value="">No other teams available</option>`;
  }

  function renderResult(me, team) {
    const symbol = $("result-symbol");
    symbol.className = "result-symbol";
    let title = "Awaiting next topic.";
    let copy = "The question has closed.";
    let glyph = "·";

    if (me?.graded) {
      if (me.is_correct === true) {
        title = "Correct.";
        copy = "The scoring engine has committed your result.";
        glyph = "+";
        symbol.classList.add("correct");
      } else if (me.is_correct === false) {
        title = "Incorrect.";
        copy = "The result is committed. Prepare for the next topic.";
        glyph = "×";
        symbol.classList.add("wrong");
      } else {
        title = "No scored answer.";
        copy = "No valid locked answer was graded for this question.";
        glyph = "—";
      }
    }

    symbol.textContent = glyph;
    $("result-title").textContent = title;
    $("result-copy").textContent = copy;
    const delta = Number(me?.points_delta || 0);
    $("result-delta").textContent = `${delta > 0 ? "+" : ""}${formatNumber(delta)}`;
    $("result-total").textContent = formatNumber(team?.score || 0);
  }

  function startTimerLoop() {
    if (timerFrame) cancelAnimationFrame(timerFrame);
    const tick = () => {
      updateDynamicReadouts();
      timerFrame = requestAnimationFrame(tick);
    };
    timerFrame = requestAnimationFrame(tick);
  }

  function updateDynamicReadouts() {
    const state = gameState;
    if (!state) return;
    const remainingMs = computeRemainingMs(state);
    const formatted = formatClock(remainingMs);
    $("top-timer").textContent = (state.phase === "live" || state.phase === "topic") ? formatted : "—";
    $("live-timer").textContent = formatted;
    $("topic-timer").textContent = formatted;

    const totalMs = state.phase === "topic"
      ? 45_000
      : 150_000 + (state?.me?.freeze ? SCORE_RULES.freezeBonusMs : 0);
    const pct = totalMs > 0 ? Math.max(0, Math.min(100, remainingMs / totalMs * 100)) : 0;
    $("timer-bar").style.width = `${pct}%`;

    updatePotential(state);

    if ((state.phase === "live" || state.phase === "topic") && remainingMs <= 0 && !timeoutRefreshQueued) {
      timeoutRefreshQueued = true;
      window.setTimeout(async () => {
        await refreshState({ quiet: true });
        timeoutRefreshQueued = false;
      }, 220);
    }
  }

  function updatePotential(state) {
    const me = state?.me || {};
    const panel = document.querySelector(".potential-panel");
    if (!panel) return;

    if (state.phase !== "live") {
      $("potential-value").textContent = "—";
      $("potential-bar").style.width = "0%";
      panel.classList.remove("locked");
      return;
    }

    const points = calculateDisplayedPotential(state);
    const rules = me.bounty ? SCORE_RULES.bounty : SCORE_RULES.normal;
    const range = rules.start - rules.floor;
    const pct = range > 0 ? Math.max(0, Math.min(100, (points - rules.floor) / range * 100)) : 0;

    $("potential-value").textContent = formatNumber(points);
    $("potential-bar").style.width = `${pct}%`;
    panel.classList.toggle("locked", !!me.locked);
    $("potential-label").textContent = me.locked ? "LOCKED VALUE" : "POTENTIAL POINTS";
    $("potential-note").textContent = me.locked
      ? `Frozen at ${formatNumber(points)} potential points when this answer was locked.`
      : (me.freeze ? "Freeze is active: effective scoring time is paused/shifted by the engine." : "Locks at the value when your answer is submitted.");
  }

  function calculateDisplayedPotential(state) {
    const me = state?.me || {};
    if (me.locked && me.potential_points !== undefined && me.potential_points !== null) {
      return Number(me.potential_points);
    }
    if (!state.live_started_at) return Number(me.potential_points || (me.bounty ? 6000 : 2000));

    const liveStart = new Date(state.live_started_at).getTime();
    const now = serverNowMs(state.server_now);
    let elapsed = Math.max(0, Math.floor(now - liveStart));
    if (me.locked_elapsed_ms !== null && me.locked_elapsed_ms !== undefined) elapsed = Number(me.locked_elapsed_ms);

    let scoringElapsed = elapsed;
    if (me.freeze && me.freeze_elapsed_ms !== null && me.freeze_elapsed_ms !== undefined) {
      scoringElapsed = Math.max(Number(me.freeze_elapsed_ms), elapsed - SCORE_RULES.freezeBonusMs);
    }

    const rules = me.bounty ? SCORE_RULES.bounty : SCORE_RULES.normal;
    if (scoringElapsed <= SCORE_RULES.decayStartMs) return rules.start;
    const decay = Math.floor(rules.decay * (scoringElapsed - SCORE_RULES.decayStartMs) / 1000);
    return Math.max(rules.floor, rules.start - decay);
  }

  function computeRemainingMs(state) {
    if (!state?.deadline_ms || (state.phase !== "live" && state.phase !== "topic")) return 0;
    return Math.max(0, Number(state.deadline_ms) - serverNowMs(state.server_now));
  }

  function syncServerClock(serverNow) {
    const base = new Date(serverNow || Date.now()).getTime();
    if (Number.isNaN(base)) return;
    serverClockAnchor = { baseValue: serverNow, baseMs: base, clientMs: Date.now() };
  }

  function serverNowMs(serverNow) {
    if (!serverClockAnchor || serverClockAnchor.baseValue !== serverNow) syncServerClock(serverNow);
    if (!serverClockAnchor) return Date.now();
    return serverClockAnchor.baseMs + (Date.now() - serverClockAnchor.clientMs);
  }

  function restoreSelection(question, me = {}) {
    if (!question) {
      selectedChoice = null;
      selectedMulti = new Set();
      currentQuestionId = null;
      return;
    }
    if (currentQuestionId && currentQuestionId !== question.id) {
      selectedChoice = null;
      selectedMulti = new Set();
    }
    currentQuestionId = question.id;
    if (me.locked && me.answer) {
      if (question.type === "mcq_single") selectedChoice = me.answer.option_id || null;
      if (question.type === "mcq_multi") selectedMulti = new Set(Array.isArray(me.answer.option_ids) ? me.answer.option_ids : []);
    }
  }

  async function submitAnswer() {
    const q = gameState?.question;
    if (!q || gameState?.me?.locked) return;
    const answer = q.type === "mcq_multi"
      ? { option_ids: [...selectedMulti].sort() }
      : { option_id: selectedChoice };
    if ((q.type === "mcq_multi" && answer.option_ids.length === 0) || (q.type === "mcq_single" && !answer.option_id)) return;
    await runAction("Locking answer", async () => {
      await api("/api/v1/play/lock", { method: "POST", body: JSON.stringify({ answer }) });
      await refreshState({ quiet: true });
      showToast("Answer locked.");
    });
  }

  async function claimController() {
    await runAction("Claiming control", async () => {
      await api("/api/v1/play/controller/claim", { method: "POST" });
      await refreshState({ quiet: true });
      showToast("Control claimed on this device.");
    });
  }

  async function releaseController() {
    await runAction("Releasing control", async () => {
      await api("/api/v1/play/controller/release", { method: "POST" });
      await refreshState({ quiet: true });
      showToast("Control released.");
    });
  }

  async function useBounty() { await cardAction("bounty", "/api/v1/play/bounty", "Bounty armed for this question."); }
  async function useFreeze() { await cardAction("freeze", "/api/v1/play/freeze", "Freeze activated."); }
  async function usePeek() { await cardAction("peek", "/api/v1/play/peek", "Hint unlocked."); }

  async function cardAction(name, path, success) {
    await runAction(`Using ${name}`, async () => {
      await api(path, { method: "POST" });
      await refreshState({ quiet: true });
      showToast(success);
    });
  }

  function openSwap() {
    if ($("swap-button").disabled) return;
    toggleSwap(true);
  }

  async function confirmSwap() {
    const target = $("swap-target").value;
    if (!target) return;
    await runAction("Queuing swap", async () => {
      await api("/api/v1/play/swap", { method: "POST", body: JSON.stringify({ target_team_id: target }) });
      toggleSwap(false);
      await refreshState({ quiet: true });
      showToast("Swap queued.");
    });
  }

  async function runAction(_label, fn) {
    try {
      await fn();
    } catch (err) {
      if (err.status === 401) return showSessionEnded();
      showToast(err.message || "Action failed", true);
      await refreshState({ quiet: true });
    }
  }

  function syncControllerHeartbeat(state) {
    if (heartbeatHandle) {
      clearInterval(heartbeatHandle);
      heartbeatHandle = null;
    }
    if (!state?.i_am_controller) return;
    heartbeatHandle = window.setInterval(async () => {
      try {
        await api("/api/v1/play/controller/heartbeat", { method: "POST" });
      } catch {
        await refreshState({ quiet: true });
      }
    }, 25_000);
  }

  async function logout() {
    try { await api("/api/v1/auth/logout", { method: "POST" }); } catch (_) { /* local cleanup still proceeds */ }
    clearAuth();
    window.location.href = "/";
  }

  function showSessionEnded() {
    clearInterval(pollHandle);
    clearInterval(heartbeatHandle);
    $("session-modal").classList.add("open");
    $("session-modal").setAttribute("aria-hidden", "false");
  }

  function toggleDrawer(open) {
    $("leaderboard-drawer").classList.toggle("open", open);
    $("leaderboard-drawer").setAttribute("aria-hidden", String(!open));
  }

  function toggleSwap(open) {
    $("swap-modal").classList.toggle("open", open);
    $("swap-modal").setAttribute("aria-hidden", String(!open));
  }

  function setConnection(state, label) {
    const el = $("connection-status");
    el.dataset.state = state;
    el.textContent = label;
  }

  function showToast(message, error = false) {
    const el = $("toast");
    el.textContent = message;
    el.classList.toggle("error", error);
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2500);
  }

  function findOwnRank(rows, teamId) {
    return rows.find((row) => row.id === teamId)?.rank ?? null;
  }

  function formatClock(ms) {
    const total = Math.max(0, Math.ceil(Number(ms || 0) / 1000));
    const min = Math.floor(total / 60);
    const sec = total % 60;
    return `${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }

  function formatNumber(value) {
    return Number(value || 0).toLocaleString();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
})();
