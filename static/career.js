
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[char]));

const REVIEW_STORAGE_KEY = "redmargin.latestReview";

let latestReview = null;
let latestAnswer = "";
let busy = false;
let listening = false;
let recognition = null;
let speechUtterance = null;
let autoSpeakEnabled = true;

function sayStatus(message, error = false) {
  const element = $("status");
  if (!element) return;
  element.textContent = message;
  element.className = error ? "status error" : "status";
}

function setTrace(message) {
  if ($("trace")) $("trace").textContent = message;
}

function setActivity(message) {
  if ($("activity")) $("activity").textContent = `Activity trace: ${message}`;
}

function persistLatestReview(data) {
  if (!data) return;
  try {
    localStorage.setItem(REVIEW_STORAGE_KEY, JSON.stringify(data));
  } catch (error) {
    console.warn("Could not save repository review.", error);
  }
}

function restoreLatestReview() {
  try {
    const raw = localStorage.getItem(REVIEW_STORAGE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (!data || typeof data !== "object") return null;
    latestReview = data;
    return data;
  } catch (error) {
    console.warn("Could not restore repository review.", error);
    return null;
  }
}

function addMessage(message, type = "bot") {
  const element = document.createElement("div");
  element.className = `message ${type}`;
  element.innerHTML =
    `<small>${type === "bot" ? "REDMARGIN CAREER AGENT" : "YOU"}</small>` +
    `<p>${esc(message).replace(/\n/g, "<br>")}</p>`;

  $("chat").appendChild(element);
  element.scrollIntoView({ behavior: "smooth", block: "nearest" });

  if (type === "bot") latestAnswer = message;
}

function addAssistantAnswer(message) {
  addMessage(message, "bot");
  speakAnswer(message);
}

function stopSpeaking() {
  if ("speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
  speechUtterance = null;
}

function speakAnswer(text = latestAnswer) {
  if (
    !autoSpeakEnabled ||
    !text ||
    !("speechSynthesis" in window) ||
    typeof SpeechSynthesisUtterance === "undefined"
  ) return;

  stopSpeaking();

  const cleanText = String(text)
    .replace(/```[\s\S]*?```/g, "Code block omitted.")
    .replace(/https?:\/\/\S+/g, "link")
    .replace(/[#*_`]/g, "")
    .replace(/\n+/g, ". ")
    .trim();

  if (!cleanText) return;

  const utterance = new SpeechSynthesisUtterance(cleanText.slice(0, 8000));
  utterance.lang = "en-IN";
  utterance.rate = 0.96;
  utterance.pitch = 1;
  utterance.volume = 1;

  speechUtterance = utterance;

  utterance.onstart = () => setVoiceState("Speaking your answer…", "speaking");

  utterance.onend = () => {
    if (speechUtterance === utterance) {
      speechUtterance = null;
      setVoiceState("Ready when you are");
    }
  };

  utterance.onerror = event => {
    if (speechUtterance === utterance) {
      speechUtterance = null;
      if (event.error !== "canceled" && event.error !== "interrupted") {
        setVoiceState("Speech unavailable. Your answer is shown on screen.");
      } else {
        setVoiceState("Ready when you are");
      }
    }
  };

  try {
    window.speechSynthesis.speak(utterance);
  } catch (error) {
    speechUtterance = null;
    console.warn("Speech playback failed.", error);
  }
}

function renderEvidencePath(path) {
  if (String(path).startsWith("skill:")) {
    return `<span class="tag">${esc(path.replace("skill:", "Skill: "))}</span>`;
  }

  if (String(path).startsWith("signal:")) {
    return `<span class="tag">${esc(path.replace("signal:", "Check: "))}</span>`;
  }

  const repoUrl = $("repo").value.trim().replace(/\/+$/, "");
  const filePath = String(path).split("/").map(encodeURIComponent).join("/");

  return `<a href="${esc(repoUrl)}/blob/HEAD/${filePath}" target="_blank" rel="noopener noreferrer">${esc(path)}</a>`;
}

function renderCards(title, items, className = "") {
  if (!items?.length) {
    return `<h3>${esc(title)}</h3><p class="muted">No items returned for this result.</p>`;
  }

  return `<h3>${esc(title)}</h3>` + items.map(item => {
    const evidence = Array.isArray(item.evidence) ? item.evidence : [];

    return `<article class="evidence-card ${className}">
      <strong>${esc(item.title || item.question || item.text || "Finding")}</strong>
      ${item.detail ? `<p>${esc(item.detail)}</p>` : ""}
      ${item.why_they_ask ? `<p>${esc(item.why_they_ask)}</p>` : ""}
      ${item.follow_up ? `<p><b>Follow-up:</b> ${esc(item.follow_up)}</p>` : ""}
      ${item.why ? `<p>${esc(item.why)}</p>` : ""}
      ${item.claim_type ? `<p class="muted">Claim type: ${esc(item.claim_type)}</p>` : ""}
      ${item.verification ? `<p class="muted">${esc(item.verification)}</p>` : ""}
      ${item.evidence_confidence ? `<p class="muted">Evidence confidence: ${esc(item.evidence_confidence)}</p>` : ""}
      ${typeof item.metrics_verified === "boolean" ? `<p class="muted">Metrics independently verified: ${item.metrics_verified ? "yes" : "no"}</p>` : ""}
      ${item.reason ? `<p><b>Reason:</b> ${esc(item.reason)}</p>` : ""}
      ${evidence.map(renderEvidencePath).join(" ")}
    </article>`;
  }).join("");
}

function renderReview(data) {
  latestReview = data;
  persistLatestReview(data);

  const score = data.repository_score;
  const scoreText = score == null ? "N/A" : `${score}/10`;
  const findings = data.critique?.findings || [];
  const strengths = data.critique?.strengths || [];
  const fixes = data.plan?.fixes || [];
  const matrix = data.skill_matrix || [];
  const signals = data.signals || [];
  const summary = data.critique?.summary || "Review completed. Inspect the evidence and limitations below.";
  const repo = data.repo || {};

  $("results").classList.remove("hidden");
  $("results").innerHTML = `
    <div class="result-grid">
      <div class="metric"><strong>${esc(scoreText)}</strong><span>Repository evidence score · not a hiring prediction</span></div>
      <div class="metric"><strong>${esc(repo.files_read?.length || 0)}</strong><span>Files inspected</span></div>
      <div class="metric"><strong>${esc(data.real_posting_count ?? 0)}</strong><span>Real job postings</span></div>
      <div class="metric"><strong>${esc(fixes.length)}</strong><span>Suggested fixes</span></div>
    </div>
    <article class="evidence-card">
      <h3>${esc(repo.full_name || "Repository review")}</h3>
      <p>${esc(summary)}</p>
      <a href="${esc(repo.url || $("repo").value)}" target="_blank" rel="noopener noreferrer">Open repository ↗</a>
    </article>
    ${renderCards("What needs work", findings, "warn")}
    ${renderCards("Evidence-backed strengths", strengths, "good")}
    <h3>Skill evidence</h3>
    ${matrix.map(row => `<article class="evidence-card">
      <strong>${esc(row.label)}</strong>
      <p>${esc(row.evidence_status || row.status || "Status not provided")}</p>
      <p class="muted">${esc(row.detectable ? (row.in_repo ? "Matching evidence found in inspected files." : "No matching evidence found in inspected files.") : "This skill cannot be assessed reliably from repository signals.")}</p>
      ${(row.evidence || []).map(path => `<span class="tag">${esc(path)}</span>`).join(" ")}
    </article>`).join("")}
    <h3>Prioritized improvement plan</h3>
    ${fixes.map((fix, index) => `<article class="evidence-card">
      <strong>${index + 1}. ${esc(fix.title)}</strong>
      <p>${esc(fix.why || "")}</p>
      <p class="muted">Estimated effort: ${esc(fix.hours_min ?? "?")}–${esc(fix.hours_max ?? "?")} hours. Estimate, not a guarantee.</p>
      ${(fix.reasons || []).map(reason => `<span class="tag">${esc(reason)}</span>`).join(" ")}
    </article>`).join("") || "<p class='muted'>No fixes were returned. This does not mean the repository has no gaps.</p>"}
    <h3>Repository checks</h3>
    ${signals.map(signal => `<article class="evidence-card">
      <strong>${esc(signal.label)}</strong>
      <p>${esc(signal.note || `Observed value: ${signal.value}`)}</p>
    </article>`).join("")}
    <p class="muted">Market comparison available: ${esc(data.market_comparison_available ? "yes" : "no")}. Missing repository evidence is not proof of a missing personal skill.</p>
  `;
}

function renderInterview(data) {
  const questions = data.questions || [];

  $("results").classList.remove("hidden");
  $("results").innerHTML = `<h3>Repository-grounded interview preparation</h3>
    <p class="muted">Source: ${esc(data.source || "Redmargin interview generator")}</p>
    ${questions.map((question, index) => `<article class="evidence-card">
      <strong>${index + 1}. ${esc(question.question)}</strong>
      <p><b>Why it is relevant:</b> ${esc(question.why_they_ask)}</p>
      <p><b>Inspected evidence:</b> ${(question.evidence || []).map(renderEvidencePath).join(", ") || "No evidence path was returned."}</p>
      <p><b>Follow-up:</b> ${esc(question.follow_up)}</p>
    </article>`).join("")}`;
}

function renderResume(data) {
  $("results").classList.remove("hidden");
  $("results").innerHTML = `<article class="evidence-card">
    <h3>Resume evidence review</h3>
    <strong>${esc(data.verdict || "Review complete")}</strong>
    <p>${esc(data.explanation || "")}</p>
    <p class="muted">Source: ${esc(data.source || "Redmargin")} · Rejected unsupported bullets: ${esc(data.rejected_bullets ?? "Not reported")}</p>
    ${data.bullet_generation_note ? `<p class="muted">${esc(data.bullet_generation_note)}</p>` : ""}
  </article>
  ${renderCards("Evidence-backed resume bullets", data.bullets || [], "good")}
  ${renderCards("Rejected bullets and reasons", (data.rejected_bullet_details || []).map(item => ({
    ...item,
    title: item.text || "Rejected bullet"
  })), "warn")}`;
}

async function apiPost(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    const detail = typeof data.detail === "string"
      ? data.detail
      : JSON.stringify(data.detail || data);

    throw new Error(detail || `Request failed (${response.status})`);
  }

  return data;
}

function setupPayload() {
  const repo_url = $("repo").value.trim();
  const role = $("role").value;
  const hours_per_day = Number($("hours").value);
  const pasted_postings = $("postings").value
    .split(/^\s*---\s*$/m)
    .map(text => text.trim())
    .filter(Boolean);

  if (!repo_url || !role) {
    throw new Error("Enter a repository URL and choose a role.");
  }

  if (!Number.isFinite(hours_per_day) || hours_per_day < 0.5 || hours_per_day > 12) {
    throw new Error("Hours per day must be between 0.5 and 12.");
  }

  return { repo_url, role, hours_per_day, pasted_postings };
}

async function ensureRepositoryReview() {
  if (latestReview) return latestReview;

  const payload = setupPayload();

  setActivity("POST /api/review · collecting repository evidence");
  setTrace("Reviewing your repository before preparing personalized career advice…");

  const data = await apiPost("/api/review", payload);
  renderReview(data);

  return data;
}

function answerFromLatestReview(text) {
  if (!latestReview) {
    addAssistantAnswer("I need to inspect your repository first. Ask me for career advice again after the review completes.");
    return;
  }

  const lower = text.toLowerCase();

  if (/evidence|why|prove|receipt|skill|show me/.test(lower)) {
    const rows = latestReview.skill_matrix || [];
    const terms = lower.split(/[^a-z0-9_+#.-]+/).filter(word => word.length > 3);
    const matches = rows.filter(row =>
      terms.some(term => String(row.label || "").toLowerCase().includes(term))
    );
    const selected = matches.length
      ? matches
      : rows.filter(row => row.in_repo || !row.detectable).slice(0, 4);

    const details = selected.map(row =>
      `${row.label}: ${row.in_repo ? "evidence found in inspected files" : row.detectable ? "no matching evidence found in inspected files" : "not reliably assessable from repository signals"}. ${Array.isArray(row.evidence) ? row.evidence.join(", ") : ""}`
    ).join("\n");

    addAssistantAnswer(
      `Here is what the current review can support:\n${details || "No matching evidence detail was returned."}\n\nMissing repository evidence is not proof that you lack the skill.`
    );

    setActivity("Using the saved repository review.");
    return;
  }

  if (/prioriti|fix|next|plan|improve|first/.test(lower)) {
    const fixes = latestReview.plan?.fixes || [];
    const answer = fixes.slice(0, 3).map((fix, index) =>
      `${index + 1}. ${fix.title} — ${fix.why || "Address this finding"}. Estimated effort: ${fix.hours_min ?? "?"}–${fix.hours_max ?? "?"} hours.`
    ).join("\n");

    addAssistantAnswer(
      `Based on the current review, start here:\n${answer || "The review did not return a prioritized fix list."}\n\nEffort values are estimates, not guarantees.`
    );

    setActivity("Using the saved improvement plan.");
    return;
  }

  addAssistantAnswer("I can explain repository evidence, recommend career next steps, prioritize improvements, or prepare you for interviews.");
}

function buildEvidenceSummary(review, payload) {
  if (!review) {
    return "No repository review has been completed. Do not claim repository-specific findings.";
  }

  const matrix = review.skill_matrix || [];
  const strengths = review.critique?.strengths || [];
  const findings = review.critique?.findings || [];
  const fixes = review.plan?.fixes || [];
  const signals = review.signals || [];

  return [
    `Repository: ${review.repo?.full_name || payload.repo_url}`,
    `Target role: ${payload.role}`,
    `Repository evidence score: ${review.repository_score ?? "not available"}/10`,
    `Files inspected: ${(review.repo?.files_read || []).join(", ") || "not reported"}`,
    `Review summary: ${review.critique?.summary || "No summary returned."}`,
    `Strengths: ${strengths.map(item => `${item.title || item.text || item.detail || "Strength"}${item.evidence?.length ? ` [evidence: ${item.evidence.join(", ")}]` : ""}`).join("; ") || "None reported"}`,
    `Findings: ${findings.map(item => `${item.title || item.text || item.detail || "Finding"}${item.evidence?.length ? ` [evidence: ${item.evidence.join(", ")}]` : ""}`).join("; ") || "None reported"}`,
    `Skill matrix: ${matrix.map(row => `${row.label}: ${row.detectable ? (row.in_repo ? "evidence found" : "no matching evidence found") : "not reliably assessable"}${row.evidence?.length ? ` [${row.evidence.join(", ")}]` : ""}`).join("; ") || "No skill matrix returned"}`,
    `Prioritized fixes: ${fixes.map((fix, index) => `${index + 1}. ${fix.title}: ${fix.why || "No rationale returned"}; effort ${fix.hours_min ?? "?"}-${fix.hours_max ?? "?"} hours`).join("; ") || "No fixes returned"}`,
    `Repository checks: ${signals.map(signal => `${signal.label}: ${signal.note || signal.value || "not reported"}`).join("; ") || "No signals returned"}`,
    "Important: repository evidence is not proof of a person's full ability. Clearly separate observed facts, missing signals, and recommendations."
  ].join("\n");
}

async function getCareerAdvice(message) {
  const payload = setupPayload();
  const lower = message.toLowerCase();

  const wantsRepositoryAdvice =
    /\b(career|roadmap|what should i learn|what should i build|what should i focus|next step|next steps|job ready|job-ready|career path|based on my repo|based on my repository|considering my repository|after thinking about my repository)\b/.test(lower);

  if (wantsRepositoryAdvice && !latestReview) {
    await ensureRepositoryReview();
  }

  const review = latestReview;
  const matrix = review?.skill_matrix || [];

  const missingSkills = matrix
    .filter(row => row.detectable && !row.in_repo)
    .map(row => row.label);

  const evidenceSummary = buildEvidenceSummary(review, payload);

  const coachingMessage = wantsRepositoryAdvice
    ? `${message}\n\nProvide personalized, practical career advice for the selected role using the repository evidence supplied below. Connect verified strengths to role requirements, explain important evidence gaps without assuming the user lacks those skills, recommend the highest-impact next steps, suggest what to learn or build, and explain why each recommendation matters. Give a realistic prioritized plan based on the user's available hours per day. Distinguish observed repository facts from recommendations. Do not just repeat the repository score or return generic advice. If the evidence is insufficient, say what remains unknown.\n\nRepository evidence:\n${evidenceSummary}`
    : message;

  setActivity("POST /api/help · evidence-aware career coaching");
  setTrace("Preparing career advice using your question, target role, and repository evidence…");

  const data = await apiPost("/api/help", {
    message: coachingMessage,
    role_id: payload.role,
    available_hours_per_day: payload.hours_per_day,
    missing_skills: missingSkills,
    evidence_summary: evidenceSummary
  });

  const answer = buildCareerAnswer(data, review, payload);

  $("results").classList.remove("hidden");
  $("results").innerHTML = answer.html;

  addAssistantAnswer(answer.text);
  sayStatus("Career advice ready.");
}

function buildCareerAnswer(data, review, payload) {
  const roleLabel = data.role?.label || $("role").selectedOptions[0]?.textContent || payload.role;
  const project = data.project || {};
  const fixes = review?.plan?.fixes || [];
  const strengths = review?.critique?.strengths || [];
  const findings = review?.critique?.findings || [];
  const matrix = review?.skill_matrix || [];

  const missingEvidence = matrix.filter(row => row.detectable && !row.in_repo);
  const notAssessable = matrix.filter(row => !row.detectable);

  const firstFix = fixes[0];
  const firstTask = project.tasks?.[0];

  const evidencePaths = [...new Set([
    ...strengths.flatMap(item => item.evidence || []),
    ...findings.flatMap(item => item.evidence || []),
    ...matrix.flatMap(row => row.evidence || [])
  ])];

  const observation = review
    ? review.critique?.summary || "The repository review returned evidence but no narrative summary."
    : "No repository review was available, so repository-specific claims have not been verified.";

  const recommendations = [
    firstFix
      ? `1. ${firstFix.title}. ${firstFix.why || "This is the first prioritized improvement from the review."} Estimated effort: ${firstFix.hours_min ?? "?"}–${firstFix.hours_max ?? "?"} hours.`
      : null,
    firstTask
      ? `${firstFix ? "2" : "1"}. Suggested project task: ${firstTask.name}. ${firstTask.deliverable || project.why || "Use this task to build relevant evidence."}`
      : null
  ].filter(Boolean);

  const text = [
    `Career guidance for ${roleLabel}`,
    "",
    "What your repository shows:",
    observation,
    strengths.length
      ? `Strengths: ${strengths.map(item => item.title || item.text || item.detail || "Strength").join("; ")}`
      : "The review did not return explicit strengths.",
    findings.length
      ? `Improvement areas: ${findings.map(item => item.title || item.text || item.detail || "Finding").join("; ")}`
      : "The review did not return explicit findings.",
    "",
    "What is not yet verified:",
    ...missingEvidence.map(row => `- No matching repository evidence for ${row.label}. This does not prove you lack the skill.`),
    ...notAssessable.map(row => `- ${row.label} could not be reliably assessed from the inspected files.`),
    "",
    "Recommended next steps:",
    recommendations.join("\n") || "The review did not return a prioritized task. Ask for a specific skill or project recommendation.",
    "",
    "Personalized guidance:",
    data.answer || data.advice || data.message || project.why || "The help service did not return additional narrative advice.",
    "",
    `Planning constraint: ${payload.hours_per_day} hours per day.`,
    "Treat repository findings as evidence about inspected files, not a complete measure of your abilities.",
    "",
    `Evidence paths: ${evidencePaths.join(", ") || "No supporting paths returned."}`
  ].join("\n");

  const html = `
    <h3>Career guidance for ${esc(roleLabel)}</h3>
    <article class="evidence-card">
      <h3>What your repository shows</h3>
      <p>${esc(observation)}</p>
    </article>
    ${renderCards("Evidence-backed strengths", strengths, "good")}
    ${renderCards("Areas to improve", findings, "warn")}
    <h3>What is not yet verified</h3>
    ${renderCards("Evidence limitations", [
      ...missingEvidence.map(row => ({
        title: row.label,
        detail: "No matching evidence was found in the inspected files. This does not prove you lack this skill.",
        evidence: row.evidence || []
      })),
      ...notAssessable.map(row => ({
        title: row.label,
        detail: "This skill could not be reliably assessed from the available repository signals.",
        evidence: row.evidence || []
      }))
    ], "warn")}
    <h3>Your next steps</h3>
    ${recommendations.length
      ? recommendations.map(item => `<article class="evidence-card"><p>${esc(item)}</p></article>`).join("")
      : `<p class="muted">No prioritized task was returned by the review.</p>`}
    <article class="evidence-card">
      <h3>Career coach's guidance</h3>
      <p>${esc(data.answer || data.advice || data.message || project.why || "The help service returned no additional narrative advice.")}</p>
    </article>
    <p class="muted">Available time: ${esc(payload.hours_per_day)} hours per day. Evidence paths: ${esc(evidencePaths.join(", ") || "Not returned")}.</p>
  `;

  return { text, html };
}

function classifyPrompt(text) {
  const lower = text.toLowerCase();

  if (/\b(mock interview|interview questions|interview prep|prepare me for an interview|practice interview)\b/.test(lower)) {
    return "interview";
  }

  if (/\b(resume|résumé|cv|curriculum vitae|resume bullet|cv bullet)\b/.test(lower)) {
    return "resume";
  }

  if (/\b(review my repo|review my repository|analyse my repo|analyze my repo|analyse my repository|analyze my repository|run a repository review|scan my repository|scan my repo)\b/.test(lower)) {
    return "review";
  }

  if (/\b(show evidence|show the evidence|show receipts|prove this|why did you say|where is the evidence|what files support|evidence for this|explain this finding)\b/.test(lower)) {
    return "evidence";
  }

  if (/\b(priorities|prioritise|prioritize|what should i fix|what should i do next|next steps|improvement plan|what do i fix first)\b/.test(lower)) {
    return "priorities";
  }

  return "advice";
}

async function runReview() {
  const payload = setupPayload();

  setTrace("Reviewing repository evidence…");
  setActivity("POST /api/review");

  const data = await apiPost("/api/review", payload);
  renderReview(data);

  addAssistantAnswer(
    `Review complete for ${data.repo?.full_name || payload.repo_url}. ` +
    `Repository evidence score: ${data.repository_score == null ? "not available" : `${data.repository_score}/10`}. ` +
    `Inspected files: ${data.repo?.files_read?.length || 0}. ` +
    `Suggested fixes: ${data.plan?.fixes?.length || 0}.`
  );

  sayStatus("Repository review complete.");
}

async function handlePrompt(rawText) {
  const text = String(rawText || "").trim();
  if (!text || busy) return;

  stopSpeaking();
  addMessage(text, "user");
  $("message").value = "";

  busy = true;
  $("sendBtn").disabled = true;
  $("reviewBtn").disabled = true;

  try {
    const intent = classifyPrompt(text);

    if (intent === "interview") {
      const payload = setupPayload();

      setActivity("POST /api/interview");
      setTrace("Generating interview questions from inspected repository files…");

      const data = await apiPost("/api/interview", {
        repo_url: payload.repo_url,
        role: payload.role
      });

      renderInterview(data);

      addAssistantAnswer(
        `Generated ${data.questions?.length || 0} repository-grounded interview questions. ` +
        "Review each question's evidence and follow-up."
      );

      sayStatus("Interview preparation complete.");
    } else if (intent === "resume") {
      const payload = setupPayload();

      setActivity("POST /api/resume-verdict");
      setTrace("Checking resume claims against inspected repository evidence…");

      const data = await apiPost("/api/resume-verdict", {
        repo_url: payload.repo_url,
        role: payload.role
      });

      renderResume(data);

      addAssistantAnswer(
        `Resume evidence review complete. Verdict: ${data.verdict || "not provided"}. ` +
        `Rejected unsupported bullets: ${data.rejected_bullets ?? "not reported"}.`
      );

      sayStatus("Resume evidence review complete.");
    } else if (intent === "review") {
      await runReview();
    } else if ((intent === "evidence" || intent === "priorities") && latestReview) {
      answerFromLatestReview(text);
    } else {
      await getCareerAdvice(text);
    }

    $("speakBtn").disabled = !("speechSynthesis" in window);
  } catch (error) {
    const message = error?.message || "An unexpected error occurred.";

    addMessage(`I couldn't complete that action: ${message}`);
    sayStatus(message, true);
    setTrace("Request failed. Check the API response and server logs.");
    setActivity("Request failed.");
  } finally {
    busy = false;
    $("sendBtn").disabled = false;
    $("reviewBtn").disabled = false;
  }
}

function setVoiceState(message, mode = "ready") {
  const state = $("voiceState");
  const button = $("voiceBtn");

  if (state) state.textContent = message;

  if (button) {
    button.classList.toggle("is-listening", mode === "listening");
    button.classList.toggle("is-thinking", mode === "thinking");
    button.classList.toggle("is-speaking", mode === "speaking");
    button.setAttribute("aria-pressed", String(mode === "listening"));
    button.setAttribute(
      "aria-label",
      mode === "listening" ? "Stop voice input" : "Start voice input"
    );
  }
}

function stopVoiceInput() {
  if (recognition && listening) {
    try {
      recognition.stop();
    } catch (error) {
      console.warn("Could not stop voice input.", error);
    }
  }

  listening = false;
  setVoiceState("Ready when you are");
}

function startVoiceInput() {
  if (listening) {
    stopVoiceInput();
    return;
  }

  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!Recognition) {
    sayStatus("Voice input isn't supported in this browser. Type your question instead.", true);
    setVoiceState("Voice input isn't supported in this browser.");
    return;
  }

  if (busy) {
    sayStatus("Please wait for the current request to finish.");
    return;
  }

  stopSpeaking();

  recognition = new Recognition();
  recognition.lang = "en-IN";
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  recognition.continuous = false;
  listening = true;

  let finalTranscript = "";

  recognition.onstart = () => {
    setVoiceState("Listening… ask your question", "listening");
    sayStatus("Listening for your question…");
  };

  recognition.onresult = event => {
    let interimTranscript = "";

    for (let i = event.resultIndex; i < event.results.length; i++) {
      const transcript = event.results[i][0].transcript;

      if (event.results[i].isFinal) {
        finalTranscript += `${transcript} `;
      } else {
        interimTranscript += transcript;
      }
    }

    $("message").value = `${finalTranscript} ${interimTranscript}`.trim();
  };

  recognition.onerror = event => {
    listening = false;
    setVoiceState("Voice input stopped");

    const messages = {
      "not-allowed": "Allow microphone access in your browser settings.",
      "no-speech": "I didn't hear anything. Try again closer to your microphone.",
      "network": "Browser speech recognition needs a working network connection."
    };

    sayStatus(
      messages[event.error] || `Voice input failed (${event.error}). You can type instead.`,
      true
    );
  };

  recognition.onend = async () => {
    listening = false;
    const transcript = finalTranscript.trim();

    if (!transcript) {
      setVoiceState("No speech detected. Try again.");
      return;
    }

    $("message").value = transcript;
    setVoiceState("Got it. Preparing your answer…", "thinking");
    await handlePrompt(transcript);
    setVoiceState("Ready when you are");
  };

  try {
    recognition.start();
  } catch (error) {
    listening = false;
    setVoiceState("Couldn't start voice input. Try again.");
    sayStatus("Could not start the microphone. Check browser permissions or type your question.", true);
  }
}

function addHelpButton() {
  if ($("helpVoiceBtn")) return;

  const composer = $("composer");
  if (!composer) return;

  const button = document.createElement("button");
  button.id = "helpVoiceBtn";
  button.type = "button";
  button.className = "secondary";
  button.textContent = "🎙 How may I help you?";
  button.setAttribute("aria-label", "Ask Redmargin a question using your voice");
  button.addEventListener("click", startVoiceInput);

  composer.insertAdjacentElement("afterend", button);

  button.style.marginTop = "10px";
  button.style.padding = "10px 16px";
  button.style.borderRadius = "12px";
  button.style.cursor = "pointer";
}

function wireInterface() {
  $("setup").addEventListener("submit", async event => {
    event.preventDefault();
    await handlePrompt("Review my repository and show me the evidence.");
  });

  $("composer").addEventListener("submit", async event => {
    event.preventDefault();

    const message = $("message").value.trim();

    if (message) {
      await handlePrompt(message);
    } else {
      startVoiceInput();
    }
  });

  $("voiceBtn").addEventListener("click", startVoiceInput);

  $("speakBtn").addEventListener("click", () => {
    if (!latestAnswer) {
      sayStatus("No answer is available to read aloud yet.", true);
      return;
    }

    autoSpeakEnabled = true;
    speakAnswer(latestAnswer);
  });

  document.querySelectorAll("[data-prompt]").forEach(button => {
    button.addEventListener("click", () => handlePrompt(button.dataset.prompt));
  });

  addHelpButton();
}

async function loadRoles() {
  try {
    const response = await fetch("/api/roles");
    if (!response.ok) throw new Error("Could not load roles.");

    const roles = await response.json();

    $("role").innerHTML = roles.map(role =>
      `<option value="${esc(role.id)}">${esc(role.label)}</option>`
    ).join("");

    if (roles.some(role => role.id === "fde")) {
      $("role").value = "fde";
    }

    restoreLatestReview();

    if (latestReview) {
      renderReview(latestReview);
      addMessage("Restored your latest repository review. Ask for career advice to connect this evidence to your next steps.");
    }

    if ($("speakBtn")) {
      $("speakBtn").disabled = !("speechSynthesis" in window);
    }

    sayStatus("Ready. Ask about your career or repository.");
  } catch (error) {
    sayStatus(error.message || "Could not load roles.", true);
    setTrace("Could not load role options. Check that FastAPI is running.");
  }
}

wireInterface();
loadRoles();
