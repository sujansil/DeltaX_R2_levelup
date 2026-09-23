const a = auth();
if (!a || a.role !== "admin") location.href = "/";

const err = document.getElementById("err");

function logout() {
  localStorage.removeItem("auth"); // Adjust this key if your common.js uses a different one
  location.href = "/";
}

async function loadQuestions() {
  try {
    const qs = await api("/api/v1/admin/questions");
    const sel = document.getElementById("qselect");
    sel.innerHTML = "";
    qs.forEach((q) => {
      const o = document.createElement("option");
      o.value = q.id;
      o.textContent = `${q.index}. ${q.topic}`;
      sel.append(o);
    });
  } catch (e) { err.textContent = "Failed to load questions: " + e.message; }
}

async function refresh() {
  try {
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
  } catch (e) { err.textContent = "Failed to refresh state: " + e.message; }
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
    alert("Team Created Successfully");
  } catch (ex) { err.textContent = ex.message; }
};

document.getElementById("qForm").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  
  // Bundle the individual text/image inputs into a clean JSON object for the backend
  const optionsPayload = {
    A: { text: fd.get("optA_text"), image: fd.get("optA_img") || null },
    B: { text: fd.get("optB_text"), image: fd.get("optB_img") || null },
    C: { text: fd.get("optC_text"), image: fd.get("optC_img") || null },
    D: { text: fd.get("optD_text"), image: fd.get("optD_img") || null }
  };

  // Convert the single answer key into an array if your backend expects it
  // (Change to just fd.get("answer_key") if your backend expects a plain string)
  const answerKeyPayload = [fd.get("answer_key")];

  try {
    await api("/api/v1/admin/questions", {
      method: "POST",
      body: JSON.stringify({
        index: Number(fd.get("index")),
        topic: fd.get("topic"),
        prompt: fd.get("prompt"),
        qtype: fd.get("qtype"),
        options: optionsPayload,
        answer_key: answerKeyPayload,
        hint: fd.get("hint") || null,
      }),
    });
    e.target.reset();
    await loadQuestions();
    alert("Question Uploaded Successfully");
  } catch (ex) { err.textContent = ex.message; }
};

// Initialize sequence
loadQuestions().then(refresh);
setInterval(refresh, 2000);
// connectWs(() => refresh()); // Uncomment if you are using WebSocket for the admin panel