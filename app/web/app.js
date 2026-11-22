/* TastyPop Studio 대시보드 */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
let STATE = { type: "asmr3d", project: null, health: null, projects: [], poll: null, media: null };

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await r.json(); } catch (e) { data = { ok: false, error: "응답 오류" }; }
  if (!r.ok) throw new Error(data.detail || data.error || ("오류 " + r.status));
  return data;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2600);
}
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function copy(text) {
  navigator.clipboard.writeText(text).then(() => toast("복사했습니다 ✓")).catch(() => {
    const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta);
    ta.select(); document.execCommand("copy"); ta.remove(); toast("복사했습니다 ✓");
  });
}

/* ---------------- 탭 ---------------- */
function showTab(name) {
  $$("nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$("main section").forEach(s => s.classList.toggle("active", s.id === "tab-" + name));
  if (name === "projects") { loadProjects(); loadInbox(); }
  if (name === "upload") loadCopyTab();
  if (name === "youtube") loadYt();
  if (name === "strategy") loadStrategy();
  if (name === "comments") loadComments();
  if (name === "settings") loadSettings();
  localStorage.setItem("tp_tab", name);
}
$$("nav button").forEach(b => b.onclick = () => showTab(b.dataset.tab));

/* ---------------- 상태 ---------------- */
async function loadHealth() {
  try {
    const h = await api("/api/health");
    STATE.health = h;
    $("#verLabel").textContent = "v" + h.version;
    const pill = $("#envPill");
    const ok = h.ok;
    pill.innerHTML = `<span class="dot ${ok ? "ok" : "warn"}"></span>${ok ? "정상" : "확인 필요"} · ${h.encoder}`;
    const rows = (h.env.items || []).map(i => `${i.ok ? "✅" : "⚠️"} ${i.name} — ${esc(i.detail || "")}`).join("<br>");
    const prov = (h.providers || []).map(p => `${p.ok ? "✅" : "⬜"} ${p.name} (${p.use})`).join("<br>");
    $("#envDetail").innerHTML = rows + "<hr style='border-color:#2b313c'>" + prov +
      `<br><br>콘텐츠 타입: ${Object.values(h.content_types).map(c => c.label).join(" / ")}`;
  } catch (e) { $("#envPill").innerHTML = `<span class="dot err"></span>서버 오류`; }
}

/* ---------------- 제작 ---------------- */
function setTopic(t) { $("#inpTopic").value = t; }
function setPreset(v) {
  $("#inpSeconds").value = v;
  $("#inpSecondsRange").value = v;
  $("#secLabel").textContent = v;
}
$$(".typecard").forEach(c => c.onclick = () => {
  $$(".typecard").forEach(x => x.classList.remove("sel"));
  c.classList.add("sel"); STATE.type = c.dataset.type;
  setPreset(STATE.type === "asmr3d" ? 18 : 18.5);
});
$("#inpHook").addEventListener("input", () => {
  const t = encodeURIComponent($("#inpHook").value || "Would you eat this?");
  $("#previewHook").src = `/api/text_overlay?text=${t}&kind=hook`;
});

let LAST_PICK = null;
async function loadTopics(cat) {
  if (cat === "all" && !STATE.catsLoaded) { await loadTopicCats(); }
  const box = $("#topicList");
  box.innerHTML = "추천 불러오는 중…";
  try {
    let d;
    if (cat === "ai") d = await api("/api/topics/recommend?ai=true&count=5");
    else d = await api("/api/topics/recommend?count=4" + (cat && cat !== "all" ? "&category=" + cat : ""));
    const items = d.items || [];
    box.innerHTML = `<div class="hint" style="margin-bottom:8px">${esc(d.note || "")}</div>` + items.map((i, n) => `
      <div class="copyrow" style="cursor:pointer;margin-bottom:8px" onclick='pickTopic(${JSON.stringify(i).replace(/'/g, "&#39;")})'>
        <div class="row" style="justify-content:space-between">
          <b>${i.icon || "🍽️"} ${esc(i.topic)}</b><span class="pill">${esc(i.category_name || i.category || "추천")}</span>
        </div>
        <div class="hint" style="margin-top:4px">훅: <b>${esc(i.hook || "")}</b> · ${esc(i.concept || "")}</div>
        <div class="hint">타입: ${i.content_type === "drama4" ? "4인방 드라마" : "무대사 ASMR"} · 추천 사운드: ${(i.sfx || []).join(", ")}</div>
      </div>`).join("");
  } catch (e) { box.innerHTML = `<span class="hint">${esc(e.message)}</span>`; }
}
async function loadTopicCats() {
  try {
    const d = await api("/api/topics/categories");
    STATE.catsLoaded = true;
    const all = [{ id: "all", icon: "🎯", name: "전체" }].concat(d.categories || []);
    $("#topicCats").innerHTML = all.map(c => `<span class="pill" style="cursor:pointer"
      title="${esc(c.desc || "")}" onclick="loadTopics('${c.id}')">${c.icon} ${esc(c.name)}${c.count ? " " + c.count : ""}</span>`).join("");
  } catch (e) { }
}
function pickTopic(item) {
  LAST_PICK = item;
  setTopic(item.topic);
  if (item.hook) $("#inpHook").value = item.hook;
  if (item.hook) $("#previewHook").src = `/api/text_overlay?text=${encodeURIComponent(item.hook)}&kind=hook`;
  if (item.content_type) {
    STATE.type = item.content_type;
    $$(".typecard").forEach(x => x.classList.toggle("sel", x.dataset.type === STATE.type));
  }
  toast("주제·훅·사운드를 채웠습니다");
}

function collectOptions() {
  const o = {};
  for (const k of ["hook", "flash", "three_cut", "mid_device", "wink_cta", "cta", "loop", "whoosh"]) {
    o[k] = $("#op_" + k).checked;
  }
  return o;
}

async function createProject(start) {
  const topic = $("#inpTopic").value.trim();
  if (!topic) return toast("주제를 입력해 주세요");
  try {
    const body = {
      topic,
      content_type: STATE.type,
      mode: $("#selMode").value,
      ep: Number($("#inpEp").value) || null,
      seconds: Number($("#inpSeconds").value) || null,
      hook: $("#inpHook").value.trim() || null,
      options: collectOptions(),
      upload_method: $("#selUploadMethod").value,
      sfx: (LAST_PICK && LAST_PICK.topic === topic) ? LAST_PICK.sfx : null,
      auto_start: !!start,
      auto_script: start || $("#selMode").value === "auto",
    };
    const res = await api("/api/projects", { method: "POST", body });
    STATE.project = res.project.id;
    toast(start ? "제작을 시작합니다 ⚡" : "프로젝트를 만들었습니다");
    if (start) {
      $("#progressCard").style.display = "block";
      startPolling(res.project.id);
    }
    showTab("projects");
    openProject(res.project.id);
  } catch (e) { toast(e.message); }
}
$("#btnCreate").onclick = () => createProject(false);
$("#btnCreateRun").onclick = () => createProject(true);
$("#btnOpenProject").onclick = () => { if (STATE.project) { showTab("projects"); openProject(STATE.project); } };

/* ---------------- 프로젝트 목록 ---------------- */
async function loadProjects() {
  try {
    const d = await api("/api/projects");
    STATE.projects = d.projects || [];
    const rows = STATE.projects.map(p => `
      <tr>
        <td><b>Ep.${p.ep ?? "-"}</b></td>
        <td>${esc(p.topic)}</td>
        <td class="hint">${esc(p.content_label || p.content_type)}</td>
        <td><span class="pill ${p.has_video ? "ok" : (p.status === "draft" ? "" : "warn")}">${p.has_video ? "완성" : (p.status === "draft" ? "설계" : p.status)}</span></td>
        <td>${p.total_seconds || "-"}초</td>
        <td>${p.cuts}컷</td>
        <td><button class="btn small" onclick="openProject('${p.id}')">열기</button></td>
      </tr>`).join("");
    $("#projectRows").innerHTML = rows || `<tr><td colspan="7" class="hint">아직 프로젝트가 없습니다. [제작] 탭에서 첫 편을 만들어 보세요.</td></tr>`;
    fillProjectSelects();
  } catch (e) { $("#projectRows").innerHTML = `<tr><td colspan="7" class="hint">불러오기 실패: ${esc(e.message)}</td></tr>`; }
}
function fillProjectSelects() {
  const opts = STATE.projects.map(p => `<option value="${p.id}">Ep.${p.ep} · ${esc(p.topic)}${p.has_video ? "" : " (미완성)"}</option>`).join("");
  const a = $("#selCopyProject"), b = $("#selUpProject");
  if (a && a.innerHTML !== opts) { a.innerHTML = opts; if (STATE.project) a.value = STATE.project; }
  if (b && b.innerHTML !== opts) { b.innerHTML = opts; if (STATE.project) b.value = STATE.project; }
}

async function openProject(pid) {
  STATE.project = pid;
  api("/api/watch/active", { method: "POST", body: { pid } }).catch(() => { });   // 자동 수신 폴더 매칭용
  try {
    const p = await api("/api/projects/" + pid);
    $("#projectDetail").style.display = "block";
    $("#pdTitle").textContent = `Ep.${p.ep} · ${p.topic}`;
    $("#pdMeta").textContent = `${p.content_label} · ${p.total_seconds}초 · ${p.cuts.length}컷 · ${p.mode === "auto" ? "완전자동" : "0원 협업"} · ${p.created_at || ""}`;
    $("#pdHook").value = p.hook?.text || "";
    $("#pdCta1").value = p.cta?.like || "";
    $("#pdCta2").value = p.cta?.save || "";
    const cuts = p.cuts.map(c => {
      const kf = `/api/projects/${pid}/kf/${c.id}?v=${Date.now()}`;
      const line = c.line || {};
      return `<div class="copyrow" style="margin-bottom:8px">
        <div class="head">
          <b>C${String(c.id).padStart(2, "0")}</b>
          <span class="row" style="gap:8px">
            <input type="number" step="0.1" min="0.5" value="${c.dur}" style="width:78px;padding:5px 8px" id="dur_${c.id}">초
            <label style="margin:0;font-size:12.5px"><input type="checkbox" id="en_${c.id}" ${c.enabled !== false ? "checked" : ""} style="width:16px;height:16px"> 사용</label>
            <button class="btn small primary" onclick="saveCut('${pid}', ${c.id})">저장</button>
          </span>
        </div>
        <div class="row" style="align-items:flex-start;gap:10px">
          <img class="thumb" src="${kf}" style="width:52px;height:93px" onerror="this.style.visibility='hidden'">
          <div style="flex:1;min-width:220px">
            <input id="scene_${c.id}" value="${esc(c.scene || "")}" placeholder="장면 묘사(영어 권장)" style="font-size:12.5px;padding:7px 9px">
            ${p.content_type === "drama4" ? `<div class="row" style="margin-top:6px;gap:6px">
              <select id="sp_${c.id}" style="width:110px;padding:6px">${Object.entries(p.voices || {}).map(([k, v]) => `<option value="${k}" ${line.speaker === k ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select>
              <input id="ko_${c.id}" value="${esc(line.ko || "")}" placeholder="한국어 대사" style="flex:1;font-size:12.5px;padding:7px 9px">
              <input id="en_${c.id}" value="${esc(line.en || "")}" placeholder="English subtitle" style="flex:1;font-size:12.5px;padding:7px 9px">
            </div>` : ""}
            <div class="row" style="margin-top:6px;gap:6px">
              <input id="sfx_${c.id}" value="${esc((c.sfx || []).join(", "))}" placeholder="효과음: crunch_bite, cheese_stretch" style="flex:1;font-size:12.5px;padding:7px 9px">
              <input id="gain_${c.id}" type="number" value="${c.sfx_gain || 0}" step="1" title="효과음 볼륨(dB)" style="width:74px;padding:6px">dB
            </div>
          </div>
        </div>
      </div>`;
    }).join("");
    $("#pdCuts").innerHTML = cuts;
    $("#pdFiles").innerHTML = filesHtml(p);
    const thumb = $("#pdThumb");
    if (p.files_present?.thumbnail) { thumb.style.display = "block"; thumb.src = `/api/projects/${pid}/file/thumbnail?v=${Date.now()}`; }
    else thumb.style.display = "none";
    const um = p.upload_method || "youtube";
    const radio = document.querySelector(`input[name=upmethod][value="${um}"]`);
    if (radio) radio.checked = true;
    const revs = (p.revisions || []).slice(-3).map(r => `· ${esc(r.instruction)} → ${esc((r.applied || []).join(", "))}`).join("<br>");
    if (revs) $("#reviseNote").innerHTML = `<b>최근 수정</b><br>${revs}`;
    pollProject(pid);
    checkMedia(true);
    if (STATE.mediaTimer) clearInterval(STATE.mediaTimer);
    STATE.mediaTimer = setInterval(() => { if (STATE.project === pid) checkMedia(true); }, 5000);
    fillProjectSelects();
  } catch (e) { toast(e.message); }
}
function filesHtml(p) {
  const pid = p.id;
  const rows = [];
  if (p.files_present?.video) {
    rows.push(`<div class="copyrow"><div class="head"><b>완성 영상</b>
      <span><a class="btn small" href="/api/projects/${pid}/file/video" target="_blank">재생</a>
      <a class="btn small" href="/api/projects/${pid}/file/video" download>저장</a></span></div>
      <div class="body">${(p.render?.width || "")}x${(p.render?.height || "")} · ${p.render?.fps || ""}fps · ${p.render?.lufs || ""} LUFS</div></div>`);
  }
  if (p.files_present?.copy) {
    rows.push(`<div class="copyrow"><div class="head"><b>업로드 카피 파일</b><a class="btn small" href="/api/projects/${pid}/file/copy" target="_blank">열기</a></div>
      <div class="body">upload_copy.txt / instagram_copy.txt</div></div>`);
  }
  rows.push(`<div class="copyrow"><div class="head"><b>이미지 요청서</b><button class="btn small" onclick="copyRequest('${pid}')">복사</button></div>
    <div class="body">kf/ 폴더에 c01.png … 를 넣으면 바로 조립됩니다.</div></div>`);
  if (p.render?.ok !== undefined) {
    const r = p.render;
    rows.push(`<div class="copyrow"><div class="head"><b>품질 검증</b><span class="pill ${r.ok ? "ok" : "warn"}">${r.ok ? "통과" : "확인 필요"}</span></div>
      <div class="body">훅 소멸: ${r.hook?.ok ? "정확" : "확인"} · 무음 ${r.silence?.count ?? 0}구간 · 워터마크 0</div></div>`);
  }
  rows.push(`<div class="copyrow"><div class="head"><b>수익창출 증빙 백업 (evidence/)</b>
    <span class="pill ${p.evidence?.ok ? "ok" : ""}">${p.evidence?.ok ? "백업 완료 " + (p.evidence.files || 0) + "개" : "미백업"}</span></div>
    <div class="body">키프레임·원본 클립·타임라인 JSON·CC0 사운드 출처·해시 목록 자동 보관
      <button class="btn small" style="margin-left:8px" onclick="buildEvidence('${pid}')">지금 백업</button></div></div>`);
  return rows.join("");
}
async function buildEvidence(pid) {
  try { const r = await api(`/api/projects/${pid}/evidence`, { method: "POST" }); toast(`증빙 백업 완료 (${r.files}개 파일)`); openProject(pid); }
  catch (e) { toast(e.message); }
}
async function updateCut(pid, id, key, value) {
  await api(`/api/projects/${pid}`, { method: "PUT", body: { cuts: [{ id, [key]: value }] } });
  toast("컷 저장");
}
async function saveCut(pid, id) {
  const body = { cuts: [{
    id,
    dur: Number($("#dur_" + id).value),
    enabled: $("#en_" + id).checked,
    scene: $("#scene_" + id).value,
    sfx: $("#sfx_" + id).value.split(",").map(s => s.trim()).filter(Boolean),
    sfx_gain: Number($("#gain_" + id).value) || 0,
  }] };
  if ($("#ko_" + id)) {
    body.cuts[0].line = { speaker: $("#sp_" + id).value, ko: $("#ko_" + id).value.trim(), en: $("#en_" + id).value.trim() };
  }
  await api(`/api/projects/${pid}`, { method: "PUT", body });
  toast(`C${String(id).padStart(2, "0")} 저장했습니다`);
}
$("#pdSaveHook").onclick = async () => {
  if (!STATE.project) return;
  await api(`/api/projects/${STATE.project}`, { method: "PUT", body: { hook: { text: $("#pdHook").value } } });
  toast("훅 저장");
};
$("#pdSaveCta").onclick = async () => {
  if (!STATE.project) return;
  await api(`/api/projects/${STATE.project}`, { method: "PUT", body: { cta: { like: $("#pdCta1").value, save: $("#pdCta2").value } } });
  toast("CTA 저장");
};
$("#pdRender").onclick = async () => {
  if (!STATE.project) return;
  await api(`/api/projects/${STATE.project}/render`, { method: "POST" });
  toast("영상 조립을 시작합니다"); $("#progressCard").style.display = "block"; startPolling(STATE.project);
};

/* ---------------- 수정 지시 ---------------- */
$("#btnRevise").onclick = async () => {
  if (!STATE.project) return toast("프로젝트를 먼저 열어 주세요");
  const inst = $("#inpRevise").value.trim();
  if (!inst) return toast("수정 지시를 입력하세요");
  $("#reviseNote").textContent = "반영 중…";
  try {
    const r = await api(`/api/projects/${STATE.project}/revise`, { method: "POST", body: { instruction: inst } });
    $("#reviseNote").innerHTML = `✅ 반영(${r.engine}): ${esc((r.applied || []).join(" · "))}<br><span class="hint">${esc(r.note || "")}</span>`;
    toast("수정 지시를 반영했습니다");
    openProject(STATE.project);
  } catch (e) { $("#reviseNote").textContent = "실패: " + e.message; toast(e.message); }
};

/* ---------------- 이미지 자동 감지 ---------------- */
async function checkMedia(auto = true) {
  if (!STATE.project) return;
  try {
    const m = await api(`/api/projects/${STATE.project}/media?auto=${auto}`);
    const pill = $("#pdMediaPill");
    if (!pill) return;
    STATE.media = m;
    pill.className = "pill " + (m.ready ? "ok" : (m.found ? "warn" : ""));
    pill.innerHTML = `<span class="dot ${m.ready ? "ok" : (m.found ? "warn" : "")}"></span>${m.found}/${m.total}컷`
      + (m.clips ? ` · 🎬${m.clips}` : "");
    const missing = (m.missing || []).map(n => "C" + String(n).padStart(2, "0")).join(", ");
    const clipNote = m.clips
      ? (m.needs_render ? "<br>🎬 <b>클립이 더 새롭습니다 — 자동으로 다시 조립 중/대기 중</b> (final.mp4 덮어씀)"
                        : (m.has_video ? "<br>🎬 클립 버전으로 조립된 영상이 있습니다." : ""))
      : "";
    $("#pdMediaText").innerHTML = (m.ready
      ? `✅ 모두 준비됨. ${m.queued ? (m.has_video ? "미디어가 바뀌어 영상 재조립을 시작했습니다." : "영상 조립을 자동으로 시작했습니다.")
                                     : (m.has_video ? (m.needs_render ? "[🎬 영상 조립]을 누르면 다시 렌더됩니다." : "완성 영상이 있습니다.")
                                                    : "[🎬 영상 조립]을 누르면 바로 렌더됩니다.")}`
      : `kf/ 폴더에 이미지 ${m.found}/${m.total} 컷 감지 · 빠진 컷: <b>${missing || "없음"}</b><br>
         <span class="hint">kf1.png ~ kf10.png 또는 c01.png ~ c10.png 로 넣으면 자동 인식됩니다.</span>`) + clipNote;
    renderKfGallery(m);
    if (m.queued) startPolling(STATE.project);
  } catch (e) { }
}

function renderKfGallery(m) {
  const box = $("#pdKfGallery");
  if (!box) return;
  if (!m || !m.cuts) { box.innerHTML = ""; return; }
  box.innerHTML = m.cuts.map(r => {
    const num = String(r.id).padStart(2, "0");
    const pid = m.project_id || STATE.project;
    const url = r.url || (pid ? `/api/projects/${pid}/kf/${r.id}?v=${r.mtime || 0}` : "");
    const kindTag = r.kind === "clip" ? `<span class="kind clip">🎬 클립</span>`
                                       : (r.kind === "image" ? `<span class="kind">🖼️ 이미지</span>` : "");
    return `<div class="kfcard ${r.found ? "" : "missing"}">
      ${r.found
        ? `<img src="${esc(url)}" alt="C${num}" loading="lazy" onclick="openKf(${JSON.stringify(url)})" onerror="this.style.visibility='hidden'">`
        : `<div class="nope">아직 없음</div>`}
      ${r.found ? kindTag : ""}
      <div class="cap">
        <b title="누르면 수정 지시에 이 컷 번호가 들어갑니다" onclick="pickCut(${r.id})">C${num}</b>
        <span>${r.dur}초</span>
      </div>
    </div>`;
  }).join("");
}

function openKf(url) {
  if (!url) return;
  window.open(url, "_blank", "noopener");
}

function pickCut(id) {
  const inp = $("#inpImgRevise");
  if (!inp) return;
  const token = `${id}번 컷 `;
  if (!inp.value.includes(token)) inp.value = (inp.value.trim() ? inp.value.trim() + ", " : "") + token;
  inp.focus();
  inp.setSelectionRange(inp.value.length, inp.value.length);
  const card = $("#pdReviseCard");
  if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
}

function copyMediaReport() {
  const m = STATE.media;
  if (!m) return toast("이미지 감지 정보가 아직 없습니다");
  const lines = (m.cuts || []).map(r => `C${String(r.id).padStart(2, "0")} ${r.found ? "✅" : "⬜"} ${r.kind === "clip" ? "클립" : "이미지"} ${r.file || ""}`);
  copy(`[${STATE.project}] 이미지 검수\n` + lines.join("\n"));
}

/* ---------------- 이미지 수정·영상 제작 요청 ---------------- */
async function sendGithubRequest(kind, instruction, btn, resultBox, idleLabel, busyLabel) {
  if (!STATE.project) return toast("프로젝트를 먼저 열어 주세요");
  btn.disabled = true;
  btn.textContent = busyLabel;
  try {
    const r = await api(`/api/projects/${STATE.project}/github-issue`, { method: "POST", body: { kind, instruction } });
    if (r.url) window.open(r.url, "_blank", "noopener");
    const box = $(resultBox);
    box.style.display = "block";
    const head = r.mode === "api" ? "✅ 깃허브 이슈 생성됨" : (r.mode === "api_failed" ? "⚠️ 토큰 실패 → 작성 화면으로 대체" : "📝 깃허브 작성 화면 열렸음");
    box.innerHTML = `<div class="head"><b>${esc(head)}</b><span class="pill ${r.ok ? "ok" : "warn"}">${esc(r.target_inbox || "")}</span></div>
      <div class="body">${esc(r.message || "")}</div>
      ${instruction ? `<div class="body" style="margin-top:6px"><b>지시</b>\n${esc(instruction)}</div>` : ""}
      ${r.url ? `<div class="body"><a href="${esc(r.url)}" target="_blank">${esc(r.url)}</a></div>` : ""}
      <div class="row" style="margin-top:8px">
        <button class="btn small" onclick='copyText(${JSON.stringify(r.copy_text || r.body || "")})'>요청서 복사</button>
        ${r.url ? `<a class="btn small" href="${esc(r.url)}" target="_blank">깃하브에서 열기</a>` : ""}
      </div>`;
    toast(r.mode === "api" ? "요청을 보냈습니다 — 케빈이 확인합니다" : "작성 화면에서 [Submit new issue] 만 누르면 됩니다");
  } catch (e) { toast(e.message); }
  finally { btn.disabled = false; btn.textContent = idleLabel; }
}

$("#pdImgRevise").onclick = () => {
  const inst = $("#inpImgRevise").value.trim();
  if (!inst) return toast("수정 지시를 적어 주세요 (예: 3번 컷 감자 표정 더 놀라게)");
  sendGithubRequest("image_revision", inst, $("#pdImgRevise"), "#pdImgReviseResult",
    "✏️ 케빈에게 이미지 수정 요청", "수정 요청 보내는 중…");
};

$("#pdVideoRequest").onclick = () => {
  const note = $("#inpVideoNote") ? $("#inpVideoNote").value.trim() : "";  sendGithubRequest("video_request", note, $("#pdVideoRequest"), "#pdVideoResult",
    "🎬 케빈에게 영상(클립) 제작 요청", "영상 제작 요청 보내는 중…");
};

async function openFolderMedia() {
  if (STATE.project) await api(`/api/open/${STATE.project}`);
}

/* ---------------- GitHub 이미지함 ---------------- */
async function loadInbox() {
  const box = $("#inboxList");
  if (!box) return;
  try {
    const d = await api("/api/inbox");
    const fs = d.folders || [];
    if (!fs.length) {
      box.innerHTML = `<div class="hint">아직 받은 이미지가 없습니다. 케빈이 저장소 <span class="mono">assets_inbox/</span> 에 올리면 여기에 나타납니다.</div>`;
      return;
    }
    const opts = STATE.projects.map(p => `<option value="${p.id}">Ep.${p.ep} · ${esc(p.topic)}</option>`).join("");
    box.innerHTML = fs.map(f => {
      const thumb = f.files[0] ? `/api/inbox/image?folder=${encodeURIComponent(f.folder)}&name=${encodeURIComponent(f.files[0])}` : "";
      return `<div class="copyrow">
        <div class="row" style="justify-content:space-between">
          <b>📁 ${esc(f.folder)}</b><span class="pill">이미지 ${f.count}장${f.clips ? ` · 🎬 클립 ${f.clips}개` : ""} · ${esc(f.updated)}</span>
        </div>
        <div class="row" style="margin-top:8px;gap:10px">
          ${thumb ? `<img src="${thumb}" style="width:44px;height:78px;object-fit:cover;border-radius:6px;border:1px solid var(--line)">` : ""}
          <select id="inbox_${esc(f.folder)}" style="flex:1;max-width:360px">${opts}</select>
          <button class="btn primary small" onclick="importInbox('${esc(f.folder)}')">📥 프로젝트에 가져오기</button>
        </div>
      </div>`;
    }).join("");
  } catch (e) { box.innerHTML = `<div class="hint">${esc(e.message)}</div>`; }
}
async function importInbox(folder) {
  const sel = $("#inbox_" + folder);
  const pid = (sel && sel.value) || STATE.project;
  if (!pid) return toast("가져올 프로젝트를 선택하세요");
  try {
    const r = await api(`/api/projects/${pid}/import`, { method: "POST", body: { folder } });
    toast(`이미지 ${r.copied.length}장${(r.copied_clips || []).length ? ` · 클립 ${r.copied_clips.length}개` : ""}을 프로젝트에 넣었습니다`);
    STATE.project = pid;
    await loadProjects();
    showTab("projects");
    openProject(pid);
    if (r.queued) toast("모두 준비되어 영상 조립을 자동 시작합니다 🎬");
  } catch (e) { toast(e.message); }
}

/* ---------------- 업로드 패키지 ---------------- */
$("#btnPackage").onclick = async () => {
  if (!STATE.project) return toast("프로젝트를 먼저 열어 주세요");
  const kind = (document.querySelector('input[name=upmethod]:checked') || {}).value || "youtube";
  $("#packageResult").style.display = "block"; $("#packageResult").textContent = "처리 중…";
  try {
    const r = await api(`/api/projects/${STATE.project}/package`, { method: "POST", body: { kind } });
    $("#packageResult").textContent = JSON.stringify(r, null, 2);
    toast(kind === "instagram" ? "인스타 패키지 완성(ZIP)" : (kind === "local" ? "로컬 파일 준비 완료" : "예약 시간 제안"));
  } catch (e) { $("#packageResult").textContent = "실패: " + e.message; }
};
$("#pdImages").onclick = async () => {
  if (!STATE.project) return;
  try { await api(`/api/projects/${STATE.project}/images`, { method: "POST" }); toast("이미지 생성 시작"); startPolling(STATE.project); }
  catch (e) { toast(e.message); }
};
$("#pdScript").onclick = async () => {
  if (!STATE.project) return;
  const btn = $("#pdScript");
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "⏳ 대본 생성 중…";
  const bar = $("#pdBar"), pct = $("#pdPct"), step = $("#pdStep"), log = $("#pdLog");
  if (bar) bar.style.width = "25%";
  if (pct) pct.textContent = "25%";
  if (step) step.textContent = "AI 대본 요청 중…";
  if (log) { log.textContent += (log.textContent ? "\n" : "") + "[진행] 🧠 1/3 · AI 대본 요청 전송 중…"; log.scrollTop = log.scrollHeight; }
  toast("AI 대본 생성 중…");
  try {
    await api(`/api/projects/${STATE.project}/script`, { method: "POST", body: {} });
    toast("대본 반영 완료");
    openProject(STATE.project);
  } catch (e) {
    if (bar) bar.style.width = "0%";
    if (pct) pct.textContent = "0%";
    if (step) step.textContent = "대본 오류";
    if (log) { log.textContent += `\n[오류] ❌ ${e.message}`; log.scrollTop = log.scrollHeight; }
    toast(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
};
$("#pdFolder").onclick = async () => { if (STATE.project) await api(`/api/open/${STATE.project}`, { method: "GET" }); };

/* ---------------- 케빈에게 이미지 요청 (GitHub Issue) ---------------- */
async function requestFromKevin() {
  if (!STATE.project) return toast("프로젝트를 먼저 열어 주세요");
  const btn = $("#pdGithub");
  btn.disabled = true;
  btn.textContent = "요청 보내는 중…";
  try {
    const r = await api(`/api/projects/${STATE.project}/github-issue`, { method: "POST", body: {} });
    if (r.url) window.open(r.url, "_blank", "noopener");       // 토큰O: 만들어진 이슈 / 토큰X: 작성 화면
    if (r.copy_text) copy(r.copy_text);                          // 보조: 요청서 클립보드 복사
    const box = $("#pdIssueResult");
    box.style.display = "block";
    const head = r.mode === "api" ? "✅ 깃허브 이슈 생성됨" : (r.mode === "api_failed" ? "⚠️ 토큰 실패 → 작성 화면으로 대체" : "📝 깃허브 작성 화면 열림");
    box.innerHTML = `<div class="head"><b>${esc(head)}</b><span class="pill ${r.ok ? "ok" : "warn"}">${esc(r.target_inbox || "")}</span></div>
      <div class="body">${esc(r.message || "")}</div>
      ${r.url ? `<div class="body"><a href="${esc(r.url)}" target="_blank">${esc(r.url)}</a></div>` : ""}
      <div class="row" style="margin-top:8px">
        <button class="btn small" onclick='copyText(${JSON.stringify(r.copy_text || "")})'>요청서 복사</button>
        ${r.url ? `<a class="btn small" href="${esc(r.url)}" target="_blank">깃허브에서 열기</a>` : ""}
      </div>`;
    toast(r.mode === "api" ? "깃허브 이슈를 만들었습니다 — 케빈이 5분 이내에 이미지를 올립니다" : "작성 화면에서 [Submit new issue] 만 누르면 됩니다");
  } catch (e) { toast(e.message); }
  finally { btn.disabled = false; btn.textContent = "📤 케빈에게 이미지 요청하기"; }
}
$("#pdGithub").onclick = requestFromKevin;

/* ---------------- 자동 이미지 수신(20초) ---------------- */
async function loadWatch() {
  try {
    const w = await api("/api/watch");
    STATE.watch = w;
    const el = $("#watchState");
    if (el) { el.textContent = w.enabled ? "ON" : "OFF"; el.style.color = w.enabled ? "var(--ok)" : "var(--err)"; }
    const btn = $("#btnWatch");
    if (btn) btn.title = `20초마다 GitHub 확인 · ${w.has_git ? "연결됨" : ".git 없음"} · 최근: ${(w.state || {}).last_pull_message || "—"}`;
  } catch (e) { }
}
async function toggleWatch() {
  const on = !(STATE.watch && STATE.watch.enabled);
  try {
    STATE.watch = await api("/api/watch", { method: "POST", body: { on } });
    await loadWatch();
    toast(on ? "자동 이미지 수신 ON — 20초마다 깃허브를 확인합니다" : "자동 이미지 수신 OFF");
  } catch (e) { toast(e.message); }
}
$("#btnWatch").onclick = toggleWatch;
async function manualWatch() {
  toast("지금 확인 중…");
  try {
    const r = await api("/api/watch/now", { method: "POST", body: {} });
    const got = (r.received || []).reduce((n, x) => n + (x.added || []).length + (x.replaced || []).length
      + (x.added_clips || []).length + (x.replaced_clips || []).length, 0);
    const clips = (r.received || []).reduce((n, x) => n + (x.added_clips || []).length + (x.replaced_clips || []).length, 0);
    const rebuilt = (r.rendered || []).length;
    toast(got ? `새 파일 ${got}개를 받았습니다${clips ? ` (클립 ${clips}개)` : ""} 🎉${rebuilt ? " · 영상 재조립 중" : ""}`
              : ((r.pull && r.pull.message) || "새 파일이 없습니다"));
    await loadInbox(); loadWatch();
    if (STATE.project) openProject(STATE.project);
  } catch (e) { toast(e.message); }
}

async function copyRequest(pid) {
  const r = await fetch(`/api/projects/${pid}/image_request`); copy(await r.text());
}

/* ---------------- 진행률 ---------------- */
function startPolling(pid) {
  STATE.project = pid;
  if (STATE.poll) clearInterval(STATE.poll);
  $("#progressCard").style.display = "block";
  STATE.poll = setInterval(() => pollProject(pid), 1500);
}
async function pollProject(pid) {
  try {
    const p = await fetch(`/api/projects/${pid}/progress`).then(r => r.json());
    const pct = p.pct || 0;
    ["#progressBar", "#pdBar"].forEach(sel => { const el = $(sel); if (el) el.style.width = pct + "%"; });
    $("#progressPct").textContent = pct + "%";
    $("#progressStage").textContent = p.stage || "대기";
    if ($("#pdPct")) $("#pdPct").textContent = pct + "%";
    if ($("#pdStage")) $("#pdStage").textContent = p.stage || "대기";
    const logs = (p.log || []).join("\n");
    if ($("#progressLog")) { $("#progressLog").textContent = logs; $("#progressLog").scrollTop = 1e6; }
    if ($("#pdLog")) { $("#pdLog").textContent = logs; $("#pdLog").scrollTop = 1e6; }
    if (pct >= 100 && STATE.poll) { clearInterval(STATE.poll); STATE.poll = null; toast("완료! 프로젝트를 확인하세요"); }
  } catch (e) { }
}

/* ---------------- 업로드 카피 ---------------- */
async function loadCopyTab() {
  if (!STATE.projects.length) await loadProjects();
  const pid = $("#selCopyProject").value || STATE.project || (STATE.projects[0] && STATE.projects[0].id);
  if (!pid) return;
  $("#selCopyProject").value = pid;
  await renderCopy(pid);
}
async function renderCopy(pid) {
  try {
    const d = await api("/api/projects/" + pid + "/copy");
    const yt = d.youtube || {}, ig = d.instagram || {};
    const row = (label, text) => `<div class="copyrow"><div class="head"><b>${label}</b>
      <button class="btn small" onclick='copyText(${JSON.stringify(text || "")})'>복사</button></div>
      <div class="body">${esc(text || "")}</div></div>`;
    $("#ytCopy").innerHTML = row("제목 (70자 이내)", yt.title) + row("설명 + 해시태그", yt.description) +
      row("태그", (yt.tags || []).join(", ")) + row("고정댓글", yt.pinned);
    $("#igCopy").innerHTML = row("캡션", ig.caption) + row("3지선다 고정댓글", ig.pinned) +
      row("업로드 체크리스트", (ig.checklist || []).map(c => "☐ " + c).join("\n"));
  } catch (e) { $("#ytCopy").innerHTML = `<div class="hint">${esc(e.message)}</div>`; }
}
function copyText(t) { copy(t); }
$("#selCopyProject").onchange = () => renderCopy($("#selCopyProject").value);
$("#btnReloadCopy").onclick = async () => {
  const pid = $("#selCopyProject").value; if (!pid) return;
  await api(`/api/projects/${pid}/copy`, { method: "POST" }); renderCopy(pid); toast("카피를 새로 만들었습니다");
};

/* ---------------- 유튜브 ---------------- */
async function loadYt() {
  if (!STATE.projects.length) await loadProjects();
  try {
    const s = await api("/api/yt/status");
    const el = $("#ytStatus");
    if (s.ok && s.channel) {
      el.innerHTML = `✅ 연결됨 · <b>${esc(s.channel.title)}</b> · 구독자 ${s.channel.subs ?? "-"} · 영상 ${s.channel.videos ?? "-"}개`;
    } else if (s.token) {
      el.innerHTML = `⚠️ 로그인 파일은 있지만 확인 실패 ${esc(s.error || "")}`;
    } else {
      el.innerHTML = `⬜ 아직 로그인 전입니다. ${s.client_secret ? "client_secret.json 확인됨 → [유튜브 로그인]" : "config/client_secret.json 이 필요합니다."}`;
    }
    if (s.ok) loadYtVideos();
    loadPending();
  } catch (e) { $("#ytStatus").textContent = e.message; }
  const d = new Date(); $("#inpUpDate").value = d.toISOString().slice(0, 10);
}
async function loadPending() {
  const box = $("#pendingList");
  if (!box) return;
  try {
    const d = await api("/api/yt/pending-comments");
    const items = (d.items || []).slice(-10).reverse();
    if (!items.length) { box.innerHTML = `<div class="hint">예약 중인 고정댓글이 없습니다.</div>`; return; }
    box.innerHTML = items.map(i => `<div class="copyrow" style="margin-bottom:8px">
      <div class="row" style="justify-content:space-between">
        <b>${i.posted ? "✅ 게시 완료" : "⏳ 대기"}</b>
        <span class="pill ${i.posted ? "ok" : "warn"}">${i.posted ? (i.posted_at || "") : ("예정 " + (i.at || "").replace("T", " "))}</span>
      </div>
      <div class="hint" style="margin-top:4px">${esc((i.text || "").slice(0, 90))}…</div>
      ${i.last_error ? `<div class="hint">사유: ${esc(i.last_error)}</div>` : ""}
    </div>`).join("") +
      `<button class="btn small" onclick="runPending()">↻ 지금 확인·게시</button>`;
  } catch (e) { box.innerHTML = `<div class="hint">${esc(e.message)}</div>`; }
}
async function runPending() {
  const r = await api("/api/yt/pending-comments/run", { method: "POST" });
  toast(`게시 완료 ${(r.posted || []).length}건`); loadPending();
}
async function loadYtVideos() {
  const d = await api("/api/yt/videos");
  const rows = (d.videos || []).map(v => `<tr>
    <td>${esc(v.title).slice(0, 54)}</td>
    <td><span class="pill">${esc(v.privacy || "")}</span></td>
    <td>${v.views}</td><td>${v.likes}</td><td>${v.comments}</td>
    <td><a class="btn small" href="https://youtube.com/shorts/${v.id}" target="_blank">열기</a></td></tr>`).join("");
  $("#ytRows").innerHTML = rows || `<tr><td colspan="6" class="hint">${esc(d.error || "영상이 없습니다.")}</td></tr>`;
}
$("#btnYtLogin").onclick = async () => {
  try { const r = await api("/api/yt/login", { method: "POST" }); toast(r.message || "브라우저에서 로그인하세요"); }
  catch (e) { toast(e.message); }
};
$("#btnUpload").onclick = async () => {
  const pid = $("#selUpProject").value;
  if (!pid) return toast("프로젝트를 선택하세요");
  const mode = $("#selUpPrivacy").value;
  const body = { project_id: pid };
  if (mode === "scheduled") {
    const date = $("#inpUpDate").value, time = $("#inpUpTime").value || "19:00";
    if (!date) return toast("예약 날짜를 선택하세요");
    body.publish_at = `${date}T${time}`;
  } else {
    body.privacy = mode;
  }
  $("#upResult").style.display = "block"; $("#upResult").textContent = "업로드 중… (영상 크기에 따라 몇 분 걸립니다)";
  try {
    const r = await api("/api/yt/upload", { method: "POST", body });
    const cm = r.comment || {};
    $("#upResult").textContent = JSON.stringify(r, null, 2)
      + (cm.message ? `\n\n💬 ${cm.message}\n${cm.video_url || ""}` : "");
    toast(cm.message ? "💬 " + cm.message.slice(0, 40) : "업로드 완료");
    loadYt(); loadPending();
  } catch (e) { $("#upResult").textContent = "실패: " + e.message; toast(e.message); }
};

/* ---------------- 전략 ---------------- */
async function loadStrategy() {
  $("#strategyCards").innerHTML = `<div class="card hint">분석 중…</div>`;
  try {
    const d = await api("/api/strategy");
    $("#strategyMeta").textContent = `영상 ${d.videos}개 · 프로젝트 ${d.projects}개(완성 ${d.rendered}) · ${d.analyzed_at}`;
    const cards = (d.cards || []).map(c => `
      <div class="card">
        <div class="row" style="justify-content:space-between">
          <h3>${c.icon} ${esc(c.title)}</h3>
          <button class="btn primary small" onclick="approveCard('${c.id}')">${esc(c.button || "승인")}</button>
        </div>
        <div class="hint">${esc(c.reason)}</div>
      </div>`).join("");
    $("#strategyCards").innerHTML = cards + (d.warning ? `<div class="card hint">⚠️ ${esc(d.warning)}</div>` : "");
  } catch (e) { $("#strategyCards").innerHTML = `<div class="card hint">${esc(e.message)}</div>`; }
}
async function approveCard(id) {
  try {
    const r = await api(`/api/strategy/${id}/approve`, { method: "POST", body: {} });
    toast(r.message || "승인 완료");
    if (r.project) { STATE.project = r.project.id; showTab("projects"); openProject(r.project.id); }
    else loadStrategy();
  } catch (e) { toast(e.message); }
}
$("#btnAnalyze").onclick = loadStrategy;

/* ---------------- 댓글 ---------------- */
async function loadComments() {
  $("#commentList").innerHTML = `<div class="hint">불러오는 중…</div>`;
  try {
    const vid = $("#inpCommentVideo").value.trim();
    const d = await api("/api/comments" + (vid ? "?video_id=" + encodeURIComponent(vid) : ""));
    if (!d.ok) { $("#commentList").innerHTML = `<div class="card hint">${esc(d.error || "불러오기 실패")}</div>`; return; }
    const rows = (d.comments || []).map(c => `
      <div class="copyrow">
        <div class="head"><b>${esc(c.author)}</b><span class="hint">${c.replied ? "답글 있음" : "미답변"} · ♥ ${c.likes}</span></div>
        <div class="body" style="margin-bottom:8px">${esc(c.text)}</div>
        <div class="grid g2" style="gap:8px">
          <div><label style="margin:0 0 4px">🇰🇷 한국어 답글</label>
            <textarea id="dko_${c.comment_id}" style="min-height:64px;font-size:13px">${esc(c.draft_ko || c.draft || "")}</textarea>
            <button class="btn small" style="margin-top:6px" onclick="sendReply('${c.comment_id}','dko')">한국어로 등록</button></div>
          <div><label style="margin:0 0 4px">🇺🇸 English reply</label>
            <textarea id="den_${c.comment_id}" style="min-height:64px;font-size:13px">${esc(c.draft_en || "")}</textarea>
            <button class="btn small" style="margin-top:6px" onclick="sendReply('${c.comment_id}','den')">Post in English</button></div>
        </div>
      </div>`).join("");
    $("#commentList").innerHTML = rows || `<div class="card hint">댓글이 없습니다.</div>`;
  } catch (e) { $("#commentList").innerHTML = `<div class="card hint">${esc(e.message)}</div>`; }
}
async function sendReply(cid, which = "dko") {
  const text = $("#" + which + "_" + cid).value.trim();
  if (!text) return toast("답글 내용을 입력하세요");
  try { await api("/api/comments/reply", { method: "POST", body: { comment_id: cid, text } }); toast("답글을 등록했습니다"); loadComments(); }
  catch (e) { toast(e.message); }
}

/* ---------------- 설정 ---------------- */
const WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"];
function renderWeekdays(selected) {
  const sel = selected || [0, 1, 2, 3, 4, 5, 6];
  $("#weekdayBox").innerHTML = WEEKDAYS.map((n, i) =>
    `<label style="margin:0"><input type="checkbox" class="wd" value="${i}" ${sel.includes(i) ? "checked" : ""}> ${n}</label>`).join("");
}
function selectedWeekdays() {
  return $$(".wd").filter(x => x.checked).map(x => Number(x.value));
}

async function loadSettings() {
  try {
    const s = await api("/api/settings");
    for (const k of ["gemini", "openai", "anthropic", "kling_access_key", "kling_secret_key", "kling_api_key", "github_token"]) {
      const el = $("#k_" + k); if (el) el.value = s.keys[k] || "";
    }
    try {
      const g = await api("/api/github/status");
      if ($("#ghRepo")) $("#ghRepo").textContent = g.repo;
      if ($("#ghState")) $("#ghState").textContent = g.token ? `✓ 토큰 등록됨(끝 ${g.token_tail}) — 이슈 자동 생성` : "토큰 없음 — 1클릭 제출 화면으로 열립니다";
    } catch (e) { }
    $("#m_script_provider").value = s.models.script_provider;
    $("#m_kling_model").value = s.models.kling_model;
    $("#m_kling_mode").value = s.models.kling_mode;
    $("#m_encoder").value = s.render.encoder;
    $("#m_lufs").value = s.render.lufs;
    $("#m_hook_size").value = s.render.hook_size;
    $("#m_safe_area").value = s.render.safe_area;
    $("#m_flash").value = String(!!s.content.hook_flash);
    $("#s_enabled").value = String(!!s.scheduler.enabled);
    $("#s_time").value = s.scheduler.time || "07:00";
    $("#s_type").value = s.scheduler.content_type || "asmr3d";
    $("#s_upload").value = String(!!s.scheduler.auto_upload);
    $("#s_hour").value = s.scheduler.upload_hour || 19;
    $("#s_ep").value = s.scheduler.next_ep || "";
    renderWeekdays(s.scheduler.weekdays);
    const sc = await api("/api/scheduler");
    $("#schedState").textContent = sc.enabled
      ? `ON · 매일 ${sc.time} · 최근 실행 ${sc.last_run || "없음"}${sc.last_error ? " · 오류: " + sc.last_error : ""}`
      : "OFF";
    const ld = sc.launchd || {};
    $("#launchdState").innerHTML = ld.installed
      ? `⏰ launchd 등록됨${ld.loaded ? " (실행 대기)" : " (로드 확인 필요)"} · ${esc(ld.path || "")}`
      : "⏰ launchd 미등록 — 맥북이 꺼져 있을 때도 매일 돌리려면 [launchd 등록] 을 누르세요.";
    const sfx = await api("/api/sfx");
    $("#sfxList").innerHTML = sfx.library.map(x => `${x.exists ? "✅" : "⬜"} <b>${x.name}</b> — ${esc(x.desc)} (${x.seconds}s${x.loop ? ", 루프" : ""})`).join("<br>");
  } catch (e) { toast(e.message); }
}
$("#btnSaveKeys").onclick = async () => {
  const keys = {};
  for (const k of ["gemini", "openai", "anthropic", "kling_access_key", "kling_secret_key", "kling_api_key", "github_token"]) keys[k] = $("#k_" + k).value.trim();
  await api("/api/settings", { method: "POST", body: { keys } });
  toast("키를 저장했습니다"); loadHealth(); loadSettings();
};
$("#btnGhTest").onclick = async () => {
  try {
    const g = await api("/api/github/status");
    $("#ghState").textContent = g.token
      ? `✓ 토큰 등록됨(끝 ${g.token_tail}) · 저장소 ${g.repo} · 라벨 ${g.issue_label}`
      : `토큰 없음 · 저장소 ${g.repo} (1클릭 제출 화면으로 열립니다)`;
    toast(g.token ? "GitHub 토큰이 설정되어 있습니다" : "토큰은 없지만 요청 버튼은 동작합니다");
  } catch (e) { toast(e.message); }
};
$("#btnSaveRender").onclick = async () => {
  await api("/api/settings", { method: "POST", body: {
    models: { script_provider: $("#m_script_provider").value, kling_model: $("#m_kling_model").value.trim(), kling_mode: $("#m_kling_mode").value },
    render: { encoder: $("#m_encoder").value, lufs: Number($("#m_lufs").value), hook_size: Number($("#m_hook_size").value), safe_area: Number($("#m_safe_area").value) },
    content: { hook_flash: $("#m_flash").value === "true" },
  }});
  toast("제작 기본값을 저장했습니다"); loadHealth();
};
$("#btnSaveSched").onclick = async () => {
  const body = {
    enabled: $("#s_enabled").value === "true", time: $("#s_time").value,
    content_type: $("#s_type").value, auto_upload: $("#s_upload").value === "true",
    upload_hour: Number($("#s_hour").value), next_ep: $("#s_ep").value ? Number($("#s_ep").value) : null,
    weekdays: selectedWeekdays(),
  };
  const r = await api("/api/scheduler", { method: "POST", body });
  toast(r.enabled ? "자동 예약을 켰습니다" : "자동 예약을 껐습니다"); loadSettings();
};
$("#btnLaunchd").onclick = async () => {
  try {
    await api("/api/scheduler", { method: "POST", body: { weekdays: selectedWeekdays(), time: $("#s_time").value } });
    const r = await api("/api/scheduler/launchd", { method: "POST", body: { install: true } });
    toast(r.ok ? "launchd 등록 완료 — 지정한 요일·시간에 자동 실행됩니다" : ("등록 실패: " + (r.stderr || "")));
    loadSettings();
  } catch (e) { toast(e.message); }
};
$("#btnUnlaunchd").onclick = async () => {
  const r = await api("/api/scheduler/launchd", { method: "POST", body: { install: false } });
  toast("launchd 등록을 해제했습니다"); loadSettings();
};
$("#btnRunNow").onclick = async () => {
  toast("1편 자동 제작을 시작합니다…");
  try { const r = await api("/api/scheduler/run-now", { method: "POST" }); if (r.project) { STATE.project = r.project; showTab("projects"); openProject(r.project); } }
  catch (e) { toast(e.message); }
};
async function regenSfx() { toast("사운드 생성 중…"); const r = await api("/api/sfx/regenerate", { method: "POST" }); toast(r.count + "개 생성 완료"); loadSettings(); }

/* ---------------- 폰 접속 ---------------- */
async function openPhone() {
  $("#phoneModal").style.display = "flex";
  try {
    const d = await api("/api/remote");
    $("#phoneUrlList").innerHTML = (d.urls || []).map(u => `
      <div class="copyrow">
        <div class="head"><b>${u.kind === "wifi" ? "🏠" : (u.kind === "tailscale" ? "🌍" : "💻")} ${esc(u.label)}</b>
          <button class="btn small" onclick="copyText('${esc(u.url)}')">복사</button></div>
        <div class="body mono" style="word-break:break-all">${esc(u.url)}</div>
      </div>`).join("");
    const target = (d.urls || [])[0] || { url: "http://localhost:8766/" };
    const pinq = d.pin ? "" : "";
    $("#qrImg").src = `/api/qr.png?text=${encodeURIComponent(target.url + pinq)}&t=${Date.now()}`;
    $("#qrTarget").textContent = target.url;
    $("#inpPin").value = "";
    $("#pinState").textContent = d.pin ? `PIN 잠금 켜짐 (${d.pin_length}자리)` : "PIN 잠금 꺼짐 — 같은 Wi-Fi 의 누구나 접속할 수 있습니다";
    const c = d.caffeinate || {};
    $("#caffState").textContent = c.available ? (c.running ? "켜짐 — 맥북이 잠들지 않습니다" : "꺼짐") : "이 기기에서는 사용할 수 없습니다(macOS 전용)";
    $("#btnCaffeinate").textContent = c.running ? "☕ 잠자기 방지 끄기" : "☕ 맥북 잠자기 방지 켜기";
    $("#btnCaffeinate").dataset.on = c.running ? "1" : "0";
    if (d.tailscale) { /* Tailscale 감지됨 */ }
  } catch (e) { toast(e.message); }
}
function closePhone() { $("#phoneModal").style.display = "none"; }
$("#btnPhone").onclick = openPhone;
$("#phoneModal").addEventListener("click", (e) => { if (e.target.id === "phoneModal") closePhone(); });
async function savePin() {
  const v = $("#inpPin").value.replace(/[^0-9]/g, "").slice(0, 8);
  const r = await api("/api/remote/pin", { method: "POST", body: { pin: v } });
  toast(r.message);
  if (r.enabled) toast("PIN: " + r.pin + " — 폰에서 이 번호를 입력하세요");
  openPhone();
}
$("#btnCaffeinate").onclick = async () => {
  const on = $("#btnCaffeinate").dataset.on !== "1";
  const r = await api("/api/remote/caffeinate", { method: "POST", body: { on } });
  toast(r.message || (on ? "잠자기 방지 ON" : "잠자기 방지 OFF"));
  openPhone();
};

/* ---------------- 업데이트 / 도움말 ---------------- */
$("#btnUpdate").onclick = async () => {
  try {
    const st = await api("/api/update/status");
    if (!st.has_git) return toast("GitHub 연결 전입니다. README_맥북설치방법.txt 를 참고하세요.");
    toast("최신 버전·이미지 확인 중…");
    const r = await api("/api/update", { method: "POST" });
    toast(r.message || (r.ok ? "업데이트 완료" : "업데이트 실패"));
    if (r.ok) {
      if (r.restarting) {
        setTimeout(async () => {
          for (let i = 0; i < 12; i++) {
            try { await fetch("/api/health"); location.reload(); return; } catch (_) {}
            await new Promise(res => setTimeout(res, 500));
          }
          location.reload();
        }, 1200);
        return;
      }
      await loadInbox();
      if ((r.inbox || []).length) {
        showTab("projects");
        toast(`새 이미지 묶음 ${r.inbox.length}개를 받았습니다 📥`);
      }
      setTimeout(() => location.reload(), 1200);
    }
  } catch (e) { toast(e.message); }
};
$("#btnHelp").onclick = () => {
  alert(`【TastyPop Studio 사용법】

1) [설정]에서 API 키 입력 (없어도 0원 협업 모드로 제작 가능)
2) [제작] 탭 → 주제 입력 → 콘텐츠 타입 선택 → [프로젝트 만들기]
3) 이미지: 프로젝트 화면 [📤 케빈에게 이미지 요청하기] → 케빈이 올리면 20초마다 자동 수신
   (직접 넣을 때: 프로젝트 kf/ 에 c01.png … → 상단 [🔄 자동 이미지 수신] ON 이면 그대로 진행)
   완전자동: [이미지 자동 생성] → [영상 조립] (Kling 키가 있으면 영상화까지)
4) 완성되면 [업로드 카피] 탭에서 유튜브·인스타 문구를 복사
5) [유튜브 예약] 탭에서 예약 시간을 정해 업로드

진행률은 프로젝트 화면에서 실시간으로 보입니다.`);
};

/* ---------------- 시작 ---------------- */
(async function init() {
  await loadHealth();
  await loadProjects();
  await loadTopicCats();
  await loadWatch();
  const tab = localStorage.getItem("tp_tab") || "home";
  showTab(tab);
  setInterval(loadHealth, 60000);
  setInterval(loadWatch, 20000);
})();
