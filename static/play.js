const a = auth();
if (!a || a.role !== "team") location.href = "/";

let state = null;
let hb = null;

async function refresh() {
  state = await api("/api/v1/play/state");
  render();
}

function remainingMs() {
  if (!state || state.phase !== "live" || !state.me || !state.me.deadline_at) return 0;
  return Math.max(0, Date.parse(state.me.deadline_at) - Date.parse(state.server_now) - (Date.now() - lastSync));
}
let lastSync = Date.now();

function render() {
  lastSync = Date.now();
  document.getElementById("teamName").textContent = state.team.name;
  document.getElementById("score").textContent = "Score " + state.team.score;
  document.getElementById("roleBadge").textContent = state.i_am_controller ? "CONTROLLER" : "VIEW ONLY";
  document.getElementById("phase").textContent = state.phase;
  document.getElementById("qidx").textContent = state.question ? state.question.index : "-";
  document.getElementById("topic").textContent = state.question ? state.question.topic : "";
  document.getElementById("potential").textContent = state.me ? state.me.potential_points : "-";
  document.getElementById("ks").textContent = state.me && state.me.kingslayer_eligible ? "KingSlayer eligible this question (+500 if correct)" : "";
  const p = document.getElementById("prompt");
  p.textContent = state.question && state.question.prompt ? state.question.prompt : "(waiting for question)";
  const box = document.getElementById("answerBox");
  box.innerHTML = "";
  if (state.phase === "live" && state.question) {
    const t = state.question.type;
    if (t === "mcq_single" || t === "mcq_multi") {
      (state.question.options || []).forEach((opt) => {
        const id = "opt_" + opt.id;
        const el = document.createElement("label");
        const inp = document.createElement("input");
        inp.type = t === "mcq_multi" ? "checkbox" : "radio";
        inp.name = "opt";
        inp.value = opt.id;
        inp.id = id;
        el.append(inp, " " + opt.text);
        box.append(el, document.createElement("br"));
      });
    } else {
      const inp = document.createElement("input");
      inp.id = "val";
      inp.placeholder = t === "numeric" ? "number" : "text";
      box.append(inp);
    }
  }
  document.getElementById("hint").textContent = state.me && state.me.hint ? "HINT: " + state.me.hint : "";
  document.getElementById("me").textContent = JSON.stringify(state.me, null, 2);
  const board = document.getElementById("board");
  board.innerHTML = "";
  (state.leaderboard || []).forEach((r) => {
    const li = document.createElement("li");
    li.textContent = `#${r.rank} ${r.name} — ${r.score}`;
    board.append(li);
  });
  const sel = document.getElementById("swapTarget");
  sel.innerHTML = "";
  (state.leaderboard || []).forEach((r) => {
    if (r.id === state.team.id) return;
    const o = document.createElement("option");
    o.value = r.id;
    o.textContent = r.name;
    sel.append(o);
  });
  const ctrl = state.i_am_controller;
  const topic = state.phase === "topic";
  const live = state.phase === "live";
  const locked = state.me && state.me.locked;
  document.getElementById("bountyBtn").disabled = !(ctrl && topic && state.me && !state.me.bounty);
  document.getElementById("freezeBtn").disabled = !(ctrl && live && state.me && !state.me.freeze);
  document.getElementById("peekBtn").disabled = !(ctrl && live && state.me && !state.me.peek);
  document.getElementById("lockBtn").disabled = !(ctrl && live && !locked);
  document.getElementById("swapBtn").disabled = !(ctrl && live && locked && state.team.swap_available && state.me && !state.me.swap_used);
}

function collectAnswer() {
  const t = state.question.type;
  if (t === "mcq_single") {
    const el = document.querySelector('input[name="opt"]:checked');
    return { option_id: el ? el.value : "" };
  }
  if (t === "mcq_multi") {
    return { option_ids: [...document.querySelectorAll('input[name="opt"]:checked')].map((x) => x.value) };
  }
  return { value: document.getElementById("val").value };
}

document.getElementById("claimBtn").onclick = async () => {
  try {
    await api("/api/v1/play/controller/claim", { method: "POST" });
    if (hb) clearInterval(hb);
    hb = setInterval(() => api("/api/v1/play/controller/heartbeat", { method: "POST" }).catch(() => {}), 15000);
    await refresh();
  } catch (e) { document.getElementById("notice").textContent = e.message; }
};
document.getElementById("bountyBtn").onclick = () => act("/api/v1/play/bounty");
document.getElementById("freezeBtn").onclick = () => act("/api/v1/play/freeze");
document.getElementById("peekBtn").onclick = () => act("/api/v1/play/peek");
document.getElementById("lockBtn").onclick = async () => {
  try {
    await api("/api/v1/play/lock", { method: "POST", body: JSON.stringify({ answer: collectAnswer() }) });
    await refresh();
  } catch (e) { document.getElementById("notice").textContent = e.message; }
};
document.getElementById("swapBtn").onclick = async () => {
  try {
    await api("/api/v1/play/swap", { method: "POST", body: JSON.stringify({ target_team_id: document.getElementById("swapTarget").value }) });
    await refresh();
  } catch (e) { document.getElementById("notice").textContent = e.message; }
};

async function act(path) {
  try {
    await api(path, { method: "POST" });
    await refresh();
  } catch (e) { document.getElementById("notice").textContent = e.message; }
}

setInterval(() => {
  if (!state) return;
  const ms = remainingMs();
  document.getElementById("timer").textContent = state.phase === "live" ? (ms / 1000).toFixed(1) + "s" : "-";
}, 100);
setInterval(refresh, 2000);
connectWs(() => refresh());
refresh().catch((e) => { document.getElementById("notice").textContent = e.message; });
