const a = auth();
if (!a || a.role !== "admin") location.href = "/";

const err = document.getElementById("err");

async function loadQuestions() {
  const qs = await api("/api/v1/admin/questions");
  const sel = document.getElementById("qselect");
  sel.innerHTML = "";
  qs.forEach((q) => {
    const o = document.createElement("option");
    o.value = q.id;
    o.textContent = `${q.index}. ${q.topic}`;
    sel.append(o);
  });
}

async function refresh() {
  const rnd = await api("/api/v1/admin/round");
  document.getElementById("phase").textContent = rnd.phase;
  document.getElementById("matrix").textContent = JSON.stringify(rnd.matrix, null, 2);
  const board = document.getElementById("board");
  board.innerHTML = "";
  (rnd.leaderboard || []).forEach((r) => {
    const li = document.createElement("li");
    li.textContent = `#${r.rank} ${r.name} — ${r.score}`;
    board.append(li);
  });
  const audit = await api("/api/v1/admin/audit");
  document.getElementById("audit").textContent = JSON.stringify(audit.slice(0, 30), null, 2);
}

document.getElementById("showTopic").onclick = async () => {
  try {
    await api("/api/v1/admin/round/show-topic/" + document.getElementById("qselect").value, { method: "POST" });
    await refresh();
  } catch (e) { err.textContent = e.message; }
};
document.getElementById("startQ").onclick = async () => {
  try { await api("/api/v1/admin/round/start", { method: "POST" }); await refresh(); }
  catch (e) { err.textContent = e.message; }
};
document.getElementById("closeQ").onclick = async () => {
  try { await api("/api/v1/admin/round/close", { method: "POST" }); await refresh(); }
  catch (e) { err.textContent = e.message; }
};

document.getElementById("teamForm").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const score = fd.get("score");
  try {
    await api("/api/v1/admin/teams", {
      method: "POST",
      body: JSON.stringify({
        name: fd.get("name"),
        username: fd.get("username"),
        password: fd.get("password"),
        score: score === "" ? null : Number(score),
      }),
    });
    e.target.reset();
    await refresh();
  } catch (ex) { err.textContent = ex.message; }
};

document.getElementById("qForm").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const optRaw = fd.get("options");
  try {
    await api("/api/v1/admin/questions", {
      method: "POST",
      body: JSON.stringify({
        index: Number(fd.get("index")),
        topic: fd.get("topic"),
        prompt: fd.get("prompt"),
        qtype: fd.get("qtype"),
        options: optRaw ? JSON.parse(optRaw) : null,
        answer_key: JSON.parse(fd.get("answer_key")),
        hint: fd.get("hint") || null,
      }),
    });
    e.target.reset();
    await loadQuestions();
  } catch (ex) { err.textContent = ex.message; }
};

loadQuestions().then(refresh);
setInterval(refresh, 2000);
connectWs(() => refresh());
