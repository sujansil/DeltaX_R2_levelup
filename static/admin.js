(() => {
  const authState = auth();
  if (!authState || authState.role !== "admin" || !authState.access_token) {
    window.location.href = "/";
    return;
  }

  let roundState = null;
  let questions = [];
  let teams = [];
  let auditRows = [];
  let auditMode = "team";
  let selectedQuestionId = null;
  let selectedTeamId = null;
  let selectedSessions = null;
  let publicationState = null;
  let sessionCache = new Map();
  let sessionLookupDegraded = false;
  let serverClockAnchor = null;
  let activityFeed = [];
  let refreshTimer = null;
  let toastTimer = null;

  const $ = (id) => document.getElementById(id);

  document.addEventListener("DOMContentLoaded", init);
  window.addEventListener("levelup:session-invalid", () => logout(true));

  async function init() {
    bindControls();
    connectWs(onWsMessage);
    await refreshCore();
    await Promise.all([refreshAudit(), refreshHealth(), refreshSessionSummaries()]);
    requestAnimationFrame(tickClock);
    setInterval(refreshHealth, 10_000);
    setInterval(refreshSessionSummaries, 15_000);
  }

  function bindControls() {
    $("next-button").addEventListener("click", nextQuestion);
    $("start-button").addEventListener("click", startQuestion);
    $("pause-button").addEventListener("click", pauseQuestion);
    $("resume-button").addEventListener("click", resumeQuestion);
    $("close-button").addEventListener("click", closeQuestion);
    $("publish-button").addEventListener("click", publishLeaderboard);
    $("recovery-export-button").addEventListener("click", downloadRecoverySnapshot);
    $("view-question-button").addEventListener("click", () => openQuestionViewer(selectedQuestionId || roundState?.current_question_id));
    $("restart-open").addEventListener("click", () => openModal("restart-modal"));
    $("restart-confirm").addEventListener("click", restartRound);
    $("create-team-open").addEventListener("click", () => openModal("create-team-modal"));
    $("create-team-form").addEventListener("submit", createTeam);
    $("audit-refresh").addEventListener("click", refreshAudit);
    $("audit-all").addEventListener("click", () => { auditMode = auditMode === "all" ? "team" : "all"; refreshAudit(); });
    $("release-controller-button").addEventListener("click", releaseController);
    $("revoke-all-button").addEventListener("click", revokeAllSessions);
    $("admin-logout").addEventListener("click", () => logout(false));
    document.querySelectorAll("[data-close-modal]").forEach((el) => {
      el.addEventListener("click", () => closeModal(el.dataset.closeModal));
    });
  }

  function onWsMessage(message) {
    if (!message) return;
    if (message.type === "admin_notice") {
      addActivity(message.message || message.kind || "admin notice");
    } else if (message.type) {
      addActivity(message.type.replaceAll("_", " "));
    }
    scheduleRefresh();
  }

  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => refreshCore({ quiet: true }), 120);
  }

  async function refreshCore({ quiet = false } = {}) {
    try {
      const [round, qList, teamList, audience] = await Promise.all([
        api("/api/v1/admin/round"),
        api("/api/v1/admin/questions"),
        api("/api/v1/admin/teams"),
        api("/api/v1/public/audience").catch(() => null),
      ]);
      roundState = round;
      questions = qList || [];
      teams = teamList || [];
      publicationState = audience?.publication || null;
      syncServerClock(round.server_now);
      if (round.current_question_id) selectedQuestionId = round.current_question_id;
      if (!selectedTeamId && teams.length) selectedTeamId = teams[0].id;
      renderAll();
      if (selectedTeamId) await refreshSelectedSessions({ quiet: true });
    } catch (err) {
      if (err.status === 401) return logout(true);
      if (!quiet) showToast(err.message || "Refresh failed", true);
    }
  }

  function renderAll() {
    renderHeader();
    renderRoundControl();
    renderPublicationContext();
    renderQuestionQueue();
    renderMatrix();
    renderInspector();
    renderSessions();
    renderHealthSummary();
    renderActivity();
  }

  function renderHeader() {
    const phase = roundState?.phase || "idle";
    $("header-phase").textContent = phase.toUpperCase();
    $("header-question").textContent = roundState?.question?.index ? `Q${roundState.question.index}` : "—";
    $("round-version").textContent = `v${roundState?.version ?? "—"}`;
    $("current-question-label").textContent = roundState?.question?.index ? `Q${roundState.question.index}` : "NONE";

    document.querySelectorAll("#phase-strip [data-phase]").forEach((el) => {
      el.classList.toggle("active", el.dataset.phase === phase);
    });
  }

  function renderRoundControl() {
    const phase = roundState?.phase || "idle";
    const selected = questions.find((q) => q.id === selectedQuestionId);
    $("selected-question-label").textContent = selected ? `Q${selected.index}` : "NONE";
    $("start-button").disabled = phase !== "topic";
    $("pause-button").disabled = phase !== "live";
    $("resume-button").disabled = phase !== "paused";
    $("pause-button").classList.toggle("phase-primary", phase === "live");
    $("resume-button").classList.toggle("phase-primary", phase === "paused");
    $("close-button").disabled = phase !== "live";
    $("restart-open").disabled = phase === "live" || phase === "paused";
    const next = getNextQuestion();
    $("next-button").disabled = phase === "live" || phase === "paused" || phase === "topic" || !next;
    $("next-button").textContent = next ? `NEXT → Q${next.index}` : "NO NEXT QUESTION";
    $("view-question-button").disabled = !(selectedQuestionId || roundState?.current_question_id);
    $("paused-at-row").hidden = phase !== "paused" || !roundState?.paused_at;
    $("paused-at-label").textContent = formatTime(roundState?.paused_at);
  }

  function renderPublicationContext() {
    const currentQuestion = roundState?.question?.index;
    const currentPhase = String(roundState?.phase || "idle").toUpperCase();
    $("publication-current").textContent = `${currentQuestion ? `Q${currentQuestion} · ` : ""}${currentPhase}`;
    if (!publicationState?.published_at) {
      $("publication-last").textContent = "NOT YET PUBLISHED";
      $("publication-note").textContent = "Publish creates the audience standings + card-usage snapshot.";
      return;
    }
    const publishedQuestion = publicationState.question_index == null ? "NO QUESTION" : `Q${publicationState.question_index}`;
    $("publication-last").textContent = `${publishedQuestion} · ${formatTime(publicationState.published_at)}`;
    const differentQuestion = currentQuestion != null && publicationState.question_index != null
      && Number(currentQuestion) !== Number(publicationState.question_index);
    $("publication-note").textContent = differentQuestion
      ? "Current question differs; audience remains on the last published snapshot."
      : "Audience snapshot includes standings + card usage.";
  }

  function renderQuestionQueue() {
    $("question-count").textContent = `${questions.length} loaded`;
    const target = $("question-queue");
    if (!questions.length) {
      target.innerHTML = `<div class="inspector-empty">No questions in database.</div>`;
      return;
    }
    target.innerHTML = [...questions].sort((a, b) => a.index - b.index).map((q) => `
      <div class="queue-item ${q.id === selectedQuestionId ? "selected" : ""}" data-question-id="${q.id}">
        <div class="queue-index">Q${q.index}</div>
        <div class="queue-copy">
          <div class="queue-topic">${escapeHtml(q.topic)}</div>
          <div class="queue-prompt">${escapeHtml(q.prompt)}</div>
        </div>
        <div class="queue-actions">
          <button class="mini" data-action="view" title="View full question">VIEW</button>
          <button class="mini" data-action="topic" title="Show topic">TOPIC</button>
          <button class="mini" data-action="up" title="Move up">↑</button>
          <button class="mini" data-action="down" title="Move down">↓</button>
        </div>
      </div>`).join("");

    target.querySelectorAll(".queue-item").forEach((row) => {
      row.addEventListener("click", (ev) => {
        const id = row.dataset.questionId;
        if (ev.target.dataset.action === "view") return openQuestionViewer(id);
        if (ev.target.dataset.action === "topic") return showTopic(id);
        if (ev.target.dataset.action === "up") return moveQuestion(id, -1);
        if (ev.target.dataset.action === "down") return moveQuestion(id, 1);
        selectedQuestionId = id;
        renderQuestionQueue();
        renderRoundControl();
      });
    });
  }

  function renderMatrix() {
    const matrix = roundState?.matrix || null;
    const matrixByTeam = new Map((matrix?.teams || []).map((row) => [row.team_id, row]));
    const sortedTeams = [...teams].sort((a, b) => Number(b.score || 0) - Number(a.score || 0) || String(a.name).localeCompare(String(b.name)));
    const ranks = new Map(sortedTeams.map((team, i) => [team.id, i + 1]));

    $("matrix-topic").textContent = matrix?.question ? `Q${matrix.question.index} // ${matrix.question.topic}` : "NO ACTIVE QUESTION";
    $("matrix-answer").textContent = matrix?.question ? `answer: ${answerSummary(matrix.question.answer_key)}` : "answer: —";

    const target = $("team-matrix");
    if (!sortedTeams.length) {
      target.innerHTML = `<div class="inspector-empty">No teams created.</div>`;
      return;
    }

    target.innerHTML = sortedTeams.map((team) => {
      const row = matrixByTeam.get(team.id) || {};
      const sessions = sessionCache.get(team.id);
      const validCount = sessions?.valid_session_count ?? sessions?.active_count ?? "—";
      const recentCount = sessions?.recent_session_count ?? "—";
      const controllerAvailable = sessions?.controller_available !== false;
      const ctrl = sessions?.controller_session_id ? true : false;
      const controllerMark = controllerAvailable ? (ctrl ? "●" : "○") : "?";
      const cards = [row.bounty ? "B" : "-", row.freeze ? "F" : "-", row.peek ? "P" : "-", row.swap_used ? "S" : "-"].join(" ");
      let result = "PENDING";
      let resultClass = "result-pending";
      if (row.graded) {
        if (row.is_correct === true) { result = `+${row.points_delta ?? 0}`; resultClass = "result-ok"; }
        else if (row.is_correct === false) { result = `${row.points_delta ?? 0}`; resultClass = "result-bad"; }
        else { result = `${row.points_delta ?? 0}`; }
      }
      return `
        <div class="matrix-row ${team.id === selectedTeamId ? "selected" : ""}" data-team-id="${team.id}">
          <span class="rank">#${ranks.get(team.id)}</span>
          <span class="team-name">${escapeHtml(team.name)}</span>
          <span class="score">${formatNumber(team.score)}</span>
          <span class="cell-center team-time" data-deadline="${escapeHtml(row.deadline_at || "")}" data-locked="${row.locked ? "1" : "0"}" data-graded="${row.graded ? "1" : "0"}" data-freeze="${row.freeze ? "1" : "0"}">${formatTeamTime(row)}</span>
          <span class="cell-center" title="recent / valid sessions">${recentCount}/${validCount}</span>
          <span class="cell-center"><i class="flag ${ctrl ? "on ctrl" : ""}" title="${controllerAvailable ? "controller lease" : "controller state unavailable"}">${controllerMark}</i></span>
          <span class="cell-center"><i class="flag ${row.locked ? "on" : ""}">${row.locked ? "YES" : "NO"}</i></span>
          <span>${escapeHtml(cards)}</span>
          <span class="${resultClass}">${escapeHtml(result)}</span>
        </div>`;
    }).join("");

    target.querySelectorAll(".matrix-row").forEach((row) => row.addEventListener("click", () => selectTeam(row.dataset.teamId)));
  }

  function renderInspector() {
    const target = $("team-inspector");
    const team = teams.find((t) => t.id === selectedTeamId);
    const row = roundState?.matrix?.teams?.find((t) => t.team_id === selectedTeamId);
    if (!team) {
      $("selected-team-name").textContent = "select a team";
      target.innerHTML = `<div class="inspector-empty">Select a team from TEAM_MATRIX.</div>`;
      return;
    }

    $("selected-team-name").textContent = team.name;
    const answer = row?.answer ? JSON.stringify(row.answer) : "—";
    const state = row?.graded ? (row.is_correct === true ? "CORRECT" : row.is_correct === false ? "WRONG" : "UNANSWERED") : (row?.locked ? "LOCKED" : "OPEN");
    target.innerHTML = `
      <div class="inspector-stats">
        <div><span>SCORE</span><b>${formatNumber(team.score)}</b></div>
        <div><span>ANSWER</span><b title="${escapeHtml(answer)}">${escapeHtml(shorten(answer, 17))}</b></div>
        <div><span>STATE</span><b>${escapeHtml(state)}</b></div>
      </div>
      <div class="inspector-actions">
        <input id="manual-score-input" type="number" value="${Number(team.score || 0)}" aria-label="Manual score" />
        <button id="manual-score-set" class="cmd" type="button">SET SCORE</button>
        <button id="team-refresh-sessions" class="cmd" type="button">SESSIONS</button>
      </div>
      <div class="inspector-actions">
        <span></span>
        <button id="grade-correct" class="cmd positive" type="button" ${roundState?.current_question_id ? "" : "disabled"}>MARK CORRECT</button>
        <button id="grade-wrong" class="cmd negative" type="button" ${roundState?.current_question_id ? "" : "disabled"}>MARK WRONG</button>
      </div>`;

    $("manual-score-set").addEventListener("click", setManualScore);
    $("team-refresh-sessions").addEventListener("click", () => refreshSelectedSessions());
    $("grade-correct").addEventListener("click", () => overrideGrade(true));
    $("grade-wrong").addEventListener("click", () => overrideGrade(false));
  }

  function renderSessions() {
    const team = teams.find((t) => t.id === selectedTeamId);
    const data = selectedSessions || (selectedTeamId ? sessionCache.get(selectedTeamId) : null);
    $("session-team-copy").textContent = team ? `${team.name} // authenticated device registry` : "Select a team from the matrix.";
    const validCount = data?.valid_session_count ?? data?.active_count ?? 0;
    const recentCount = data?.recent_session_count ?? 0;
    $("session-count-label").textContent = `${recentCount} recent · ${validCount} / 4 valid`;
    $("release-controller-button").disabled = !team || !data?.controller_session_id;
    $("revoke-all-button").disabled = !team || !data?.active_count;

    const target = $("session-list");
    if (!data?.sessions?.length) {
      target.innerHTML = `<div class="inspector-empty">No session history for this team.</div>`;
      return;
    }

    target.innerHTML = data.sessions.map((s, i) => {
      const status = String(s.status || (s.active ? "active" : "inactive")).toUpperCase();
      let controllerLabel = "";
      if (data.controller_available === false) controllerLabel = " · CONTROLLER UNKNOWN";
      else if (s.is_controller && (s.status === "revoked" || s.status === "expired")) controllerLabel = " · CONTROLLER LEASE STALE";
      else if (s.is_controller) controllerLabel = " · CONTROLLER";
      return `
      <div class="session-card status-${escapeHtml(String(s.status || "inactive").toLowerCase())} ${s.is_controller ? "controller" : ""}">
        <div class="session-head">
          <b>${escapeHtml(s.device_label || `DEVICE ${i + 1}`)}</b>
          <span data-status="${escapeHtml(String(s.status || "inactive").toLowerCase())}">${escapeHtml(status + controllerLabel)}</span>
        </div>
        <div class="session-meta">
          <span>SID ${escapeHtml(shorten(s.session_id, 18))}</span>
          <span>LOGIN ${formatDateTime(s.login_time)}</span>
          <span>LAST ${formatDateTime(s.last_seen)}</span>
          ${s.user_agent ? `<span title="${escapeHtml(s.user_agent)}">UA ${escapeHtml(shorten(s.user_agent, 45))}</span>` : ""}
        </div>
        ${s.valid ? `<div class="session-controls"><button class="mini" data-revoke-session="${s.session_id}">REVOKE</button></div>` : ""}
      </div>`;
    }).join("");

    target.querySelectorAll("[data-revoke-session]").forEach((btn) => btn.addEventListener("click", () => revokeSession(btn.dataset.revokeSession)));
  }

  function renderHealthSummary() {
    const values = [...sessionCache.values()];
    const valid = values.reduce((sum, item) => sum + Number(item.valid_session_count ?? item.active_count ?? 0), 0);
    const recent = values.reduce((sum, item) => sum + Number(item.recent_session_count ?? 0), 0);
    const controllers = values.reduce((sum, item) => sum + (item.controller_session_id ? 1 : 0), 0);
    const controllerUnknown = sessionLookupDegraded || values.some((item) => item.controller_available === false);
    $("health-recent-sessions").textContent = String(recent);
    $("health-sessions").textContent = String(valid);
    $("health-controllers").textContent = controllerUnknown ? "UNKNOWN" : String(controllers);
    $("health-controllers").style.color = controllerUnknown ? "var(--amber)" : "var(--green)";
  }

  function renderActivity() {
    const target = $("activity-feed");
    if (!activityFeed.length) {
      target.innerHTML = `<div class="activity-row">No live signals yet.</div>`;
      return;
    }
    target.innerHTML = activityFeed.slice(0, 12).map((item) => `<div class="activity-row"><time>${item.time}</time>${escapeHtml(item.text)}</div>`).join("");
  }

  function addActivity(text) {
    activityFeed.unshift({ time: new Date().toLocaleTimeString([], { hour12: false }), text });
    activityFeed = activityFeed.slice(0, 40);
    renderActivity();
  }

  async function refreshAudit() {
    try {
      const team = teams.find((t) => t.id === selectedTeamId);
      if (auditMode === "team" && selectedTeamId) {
        auditRows = await api(`/api/v1/admin/teams/${selectedTeamId}/audit`);
        $("audit-title").textContent = `TEAM_LOG // ${team?.name || "TEAM"}`;
        $("audit-all").textContent = "ALL EVENT";
      } else {
        auditRows = await api("/api/v1/admin/audit");
        $("audit-title").textContent = "AUDIT_STREAM // ALL EVENT";
        $("audit-all").textContent = selectedTeamId ? "SELECTED TEAM" : "TEAM LOG";
      }
      renderAuditRows();
    } catch (err) {
      showToast(`Audit unavailable: ${err.message}`, true);
    }
  }

  function renderAuditRows() {
    const target = $("audit-log");
    const rows = auditRows || [];
    target.innerHTML = rows.map((row) => `
      <div class="audit-row">
        <time title="${escapeHtml(formatDateTime(row.at))}">${formatTime(row.at)}</time>
        <span class="audit-kind">${escapeHtml(row.kind || "event")}</span>
        <span class="audit-copy" title="${escapeHtml(JSON.stringify(row.payload || {}))}">${escapeHtml(row.actor || "system")} ${row.question_id ? `// QID ${shorten(row.question_id, 8)}` : ""} ${escapeHtml(JSON.stringify(row.payload || {}))}</span>
      </div>`).join("") || `<div class="inspector-empty">No audit entries.</div>`;
    target.scrollTop = target.scrollHeight;
  }

  async function refreshHealth() {
    let apiOk = false;
    let readyOk = false;
    try { apiOk = !!(await api("/api/health")).ok; } catch (_) { apiOk = false; }
    try { readyOk = !!(await api("/api/ready")).ok; } catch (_) { readyOk = false; }
    $("health-api").textContent = apiOk ? "ONLINE" : "DOWN";
    $("health-api").style.color = apiOk ? "var(--green)" : "var(--red)";
    $("health-ready").textContent = readyOk ? "READY" : "DEGRADED";
    $("health-ready").style.color = readyOk ? "var(--green)" : "var(--red)";
    $("ready-status").dataset.state = readyOk ? "ready" : "bad";
    $("ready-status").textContent = readyOk ? "STACK READY" : "STACK DEGRADED";
  }

  async function refreshSessionSummaries() {
    if (!teams.length) return;
    const results = await Promise.all(teams.map(async (team) => {
      try { return [team.id, await api(`/api/v1/admin/teams/${team.id}/sessions`)]; }
      catch { return [team.id, null]; }
    }));
    sessionLookupDegraded = results.some(([, data]) => data === null);
    results.forEach(([id, data]) => { if (data) sessionCache.set(id, data); });
    if (selectedTeamId && sessionCache.has(selectedTeamId)) selectedSessions = sessionCache.get(selectedTeamId);
    renderMatrix();
    renderSessions();
    renderHealthSummary();
  }

  async function refreshSelectedSessions({ quiet = false } = {}) {
    if (!selectedTeamId) return;
    try {
      selectedSessions = await api(`/api/v1/admin/teams/${selectedTeamId}/sessions`);
      sessionCache.set(selectedTeamId, selectedSessions);
      renderSessions();
      renderMatrix();
      renderHealthSummary();
    } catch (err) {
      if (!quiet) showToast(err.message, true);
    }
  }

  function selectTeam(id) {
    selectedTeamId = id;
    selectedSessions = sessionCache.get(id) || null;
    renderMatrix();
    renderInspector();
    renderSessions();
    auditMode = "team";
    refreshSelectedSessions({ quiet: true });
    refreshAudit();
  }

  async function showTopic(questionId) {
    await action(async () => {
      await api(`/api/v1/admin/round/show-topic/${questionId}`, { method: "POST" });
      selectedQuestionId = questionId;
      addActivity(`topic shown for ${questionId.slice(0, 8)}`);
      await refreshCore({ quiet: true });
    }, "Topic shown");
  }

  async function startQuestion() {
    await action(async () => {
      await api("/api/v1/admin/round/start", { method: "POST" });
      await refreshCore({ quiet: true });
    }, "Question started");
  }

  async function pauseQuestion() {
    await action(async () => {
      await api("/api/v1/admin/round/pause", { method: "POST" });
      await refreshCore({ quiet: true });
    }, "Question paused");
  }

  async function resumeQuestion() {
    await action(async () => {
      await api("/api/v1/admin/round/resume", { method: "POST" });
      await refreshCore({ quiet: true });
    }, "Question resumed");
  }

  async function closeQuestion() {
    if (!confirm("Close and grade the live question now?")) return;
    await action(async () => {
      await api("/api/v1/admin/round/close", { method: "POST" });
      await refreshCore({ quiet: true });
    }, "Question closed and graded");
  }

  async function publishLeaderboard() {
    await action(async () => {
      await api("/api/v1/admin/round/publish-leaderboard", { method: "POST" });
      await refreshCore({ quiet: true });
    }, "Published standings + card usage to the audience");
  }

  async function downloadRecoverySnapshot() {
    const button = $("recovery-export-button");
    button.disabled = true;
    try {
      const currentAuth = auth();
      const response = await fetch("/api/v1/admin/export/event", {
        headers: { Authorization: `Bearer ${currentAuth.access_token}` },
      });
      if (!response.ok) {
        let message = `Recovery export failed (${response.status})`;
        try {
          const body = await response.json();
          message = body?.message || body?.detail?.message || (typeof body?.detail === "string" ? body.detail : message);
        } catch (_) { /* keep concise status error */ }
        const error = new Error(message);
        error.status = response.status;
        throw error;
      }
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i);
      const filename = match ? decodeURIComponent(match[1].trim()) : `levelup-recovery-${new Date().toISOString().replaceAll(":", "-")}.json`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      showToast("Recovery snapshot downloaded");
    } catch (err) {
      if (err.status === 401) return logout(true);
      showToast(err.message || "Recovery export failed", true);
    } finally {
      button.disabled = false;
    }
  }

  function getNextQuestion() {
    const ordered = [...questions].sort((a, b) => a.index - b.index);
    if (!ordered.length) return null;
    const currentId = roundState?.current_question_id;
    if (!currentId) return ordered[0];
    const currentIndex = ordered.findIndex((q) => q.id === currentId);
    if (currentIndex < 0) return ordered[0];
    return ordered[currentIndex + 1] || null;
  }

  async function nextQuestion() {
    const next = getNextQuestion();
    if (!next) return showToast("No next question is loaded", true);
    await showTopic(next.id);
  }

  function openQuestionViewer(questionId) {
    const q = questions.find((item) => item.id === questionId);
    if (!q) return showToast("Question not found", true);
    selectedQuestionId = q.id;
    const options = Array.isArray(q.options)
      ? q.options.map((opt, i) => `<div class="question-option"><b>${escapeHtml(opt.id || opt.key || String.fromCharCode(65 + i))}</b><span>${escapeHtml(opt.text ?? opt.label ?? JSON.stringify(opt))}</span></div>`).join("")
      : q.options ? `<pre>${escapeHtml(JSON.stringify(q.options, null, 2))}</pre>` : `<div class="question-muted">No options.</div>`;
    $("question-modal-title").textContent = `Q${q.index} // ${q.topic}`;
    $("question-modal-body").innerHTML = `
      <div class="question-meta"><span>${escapeHtml(q.type || "")}</span><span>ANSWER: ${escapeHtml(answerSummary(q.answer_key))}</span></div>
      ${q.media_url ? `<img class="question-media" src="${escapeHtml(q.media_url)}" alt="Question media" />` : ""}
      <div class="question-prompt-full">${escapeHtml(q.prompt || "")}</div>
      <div class="question-options-full">${options}</div>
      <div class="question-detail"><b>ANSWER KEY</b><pre>${escapeHtml(JSON.stringify(q.answer_key || {}, null, 2))}</pre></div>
      <div class="question-detail"><b>HINT / PEEK</b><div>${escapeHtml(q.hint || "—")}</div></div>`;
    renderQuestionQueue();
    renderRoundControl();
    openModal("question-modal");
  }

  async function moveQuestion(questionId, direction) {
    const ordered = [...questions].sort((a, b) => a.index - b.index);
    const index = ordered.findIndex((q) => q.id === questionId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= ordered.length) return;
    const [item] = ordered.splice(index, 1);
    ordered.splice(target, 0, item);
    await action(async () => {
      await api("/api/v1/admin/questions/reorder", { method: "POST", body: JSON.stringify({ question_ids: ordered.map((q) => q.id) }) });
      await refreshCore({ quiet: true });
    }, "Question order updated");
  }

  async function setManualScore() {
    const input = $("manual-score-input");
    const value = Number(input.value);
    if (!Number.isFinite(value) || !selectedTeamId) return;
    if (!confirm(`Set this team's total score to ${value}?`)) return;
    await action(async () => {
      await api(`/api/v1/admin/teams/${selectedTeamId}/score`, { method: "PATCH", body: JSON.stringify({ score: Math.trunc(value) }) });
      await refreshCore({ quiet: true });
    }, "Score updated; audience standings unchanged until Publish");
  }

  async function overrideGrade(correct) {
    if (!selectedTeamId || !roundState?.current_question_id) return;
    if (!confirm(`Re-grade selected team as ${correct ? "CORRECT" : "WRONG"}?`)) return;
    await action(async () => {
      await api("/api/v1/admin/grade/override", {
        method: "POST",
        body: JSON.stringify({ team_id: selectedTeamId, question_id: roundState.current_question_id, correct }),
      });
      await refreshCore({ quiet: true });
    }, `Grade overridden: ${correct ? "correct" : "wrong"}`);
  }

  async function revokeSession(sessionId) {
    if (!selectedTeamId || !confirm("Revoke this device session immediately?")) return;
    await action(async () => {
      await api(`/api/v1/admin/teams/${selectedTeamId}/sessions/${sessionId}/revoke`, { method: "POST" });
      await refreshSelectedSessions({ quiet: true });
    }, "Session revoked");
  }

  async function revokeAllSessions() {
    if (!selectedTeamId || !confirm("Revoke ALL active sessions for this team?")) return;
    await action(async () => {
      await api(`/api/v1/admin/teams/${selectedTeamId}/sessions/revoke-all`, { method: "POST" });
      await refreshSelectedSessions({ quiet: true });
    }, "All team sessions revoked");
  }

  async function releaseController() {
    if (!selectedTeamId) return;
    await action(async () => {
      await api(`/api/v1/admin/teams/${selectedTeamId}/release-controller`, { method: "POST" });
      await refreshSelectedSessions({ quiet: true });
    }, "Controller released");
  }

  async function restartRound() {
    const confirmation = $("restart-confirmation").value;
    if (!roundState) return;
    await action(async () => {
      await api("/api/v1/admin/round/restart", {
        method: "POST",
        body: JSON.stringify({ confirmation, expected_round_version: roundState.version }),
      });
      $("restart-confirmation").value = "";
      closeModal("restart-modal");
      sessionCache.clear();
      await refreshCore({ quiet: true });
      await refreshSessionSummaries();
    }, "Round restarted");
  }

  async function createTeam(event) {
    event.preventDefault();
    const fd = new FormData(event.currentTarget);
    await action(async () => {
      await api("/api/v1/admin/teams", {
        method: "POST",
        body: JSON.stringify({
          name: String(fd.get("name") || "").trim(),
          username: String(fd.get("username") || "").trim(),
          password: String(fd.get("password") || ""),
          score: Number(fd.get("score") || 0),
        }),
      });
      event.currentTarget.reset();
      closeModal("create-team-modal");
      await refreshCore({ quiet: true });
      await refreshSessionSummaries();
    }, "Team created");
  }

  async function action(fn, success) {
    try {
      await fn();
      if (success) showToast(success);
    } catch (err) {
      if (err.status === 401) return logout(true);
      showToast(err.message || "Operation failed", true);
    }
  }

  function tickClock() {
    if (roundState?.server_now) {
      const now = serverNowMs(roundState.server_now);
      $("header-clock").textContent = new Date(now).toLocaleTimeString([], { hour12: false });
      $("header-countdown").textContent = computeCountdown(now);
      updateTeamTimers(now);
    }
    requestAnimationFrame(tickClock);
  }

  function formatTeamTime(row) {
    if (row?.graded) return "DONE";
    if (row?.locked) return "LOCKED";
    if (!row?.deadline_at || (roundState?.phase !== "live" && roundState?.phase !== "paused")) return "—";
    const now = roundState?.phase === "paused" && roundState?.paused_at
      ? new Date(roundState.paused_at).getTime()
      : roundState?.server_now ? serverNowMs(roundState.server_now) : Date.now();
    return formatRemaining(new Date(row.deadline_at).getTime() - now, !!row.freeze);
  }

  function updateTeamTimers(now) {
    document.querySelectorAll(".team-time").forEach((el) => {
      if (el.dataset.graded === "1") { el.textContent = "DONE"; return; }
      if (el.dataset.locked === "1") { el.textContent = "LOCKED"; return; }
      if (!el.dataset.deadline || (roundState?.phase !== "live" && roundState?.phase !== "paused")) { el.textContent = "—"; return; }
      const reference = roundState?.phase === "paused" && roundState?.paused_at
        ? new Date(roundState.paused_at).getTime()
        : now;
      el.textContent = formatRemaining(new Date(el.dataset.deadline).getTime() - reference, el.dataset.freeze === "1");
      el.classList.toggle("freeze-time", el.dataset.freeze === "1");
    });
  }

  function formatRemaining(ms, freeze) {
    const sec = Math.max(0, Math.ceil(ms / 1000));
    const text = `${sec}s`;
    return freeze ? `${text} F` : text;
  }

  function computeCountdown(now) {
    if (!roundState?.global_deadline_ms) return "—";
    const reference = roundState?.phase === "paused" && roundState?.paused_at
      ? new Date(roundState.paused_at).getTime()
      : now;
    const ms = Math.max(0, Number(roundState.global_deadline_ms) - reference);
    const sec = Math.ceil(ms / 1000);
    return `${sec}s`;
  }

  function syncServerClock(serverNow) {
    const baseMs = new Date(serverNow || Date.now()).getTime();
    serverClockAnchor = { value: serverNow, baseMs, clientMs: Date.now() };
  }

  function serverNowMs(serverNow) {
    if (!serverClockAnchor || serverClockAnchor.value !== serverNow) syncServerClock(serverNow);
    return serverClockAnchor.baseMs + (Date.now() - serverClockAnchor.clientMs);
  }

  function openModal(id) { $(id).classList.add("open"); $(id).setAttribute("aria-hidden", "false"); }
  function closeModal(id) { $(id).classList.remove("open"); $(id).setAttribute("aria-hidden", "true"); }

  async function logout(forced) {
    if (!forced) {
      try { await api("/api/v1/auth/logout", { method: "POST" }); } catch (_) { /* continue */ }
    }
    clearAuth();
    window.location.href = "/";
  }

  function showToast(message, error = false) {
    const el = $("admin-toast");
    el.textContent = message;
    el.classList.toggle("error", error);
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  function answerSummary(answerKey) {
    if (!answerKey) return "—";
    if (answerKey.option_id) return String(answerKey.option_id).toUpperCase();
    if (Array.isArray(answerKey.option_ids)) return answerKey.option_ids.join(", ").toUpperCase();
    return shorten(JSON.stringify(answerKey), 40);
  }

  function formatNumber(value) { return Number(value || 0).toLocaleString(); }
  function formatTime(value) { return value ? new Date(value).toLocaleTimeString([], { hour12: false }) : "—"; }
  function formatDateTime(value) { return value ? new Date(value).toLocaleString([], { hour12: false }) : "—"; }
  function shorten(value, n) { const s = String(value ?? ""); return s.length > n ? `${s.slice(0, n)}…` : s; }
  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
})();
