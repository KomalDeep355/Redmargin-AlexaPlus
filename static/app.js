const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let latestRedmarginReview = null;

async function loadRoles() {
  try {
    const response = await fetch("/api/roles");
    if (!response.ok) throw new Error("Role request failed");
    const roles = await response.json();
    $("role").innerHTML = roles.map((r) => `<option value="${esc(r.id)}">${esc(r.label)}</option>`).join("");
  } catch {
    $("role").innerHTML = '<option value="">Roles unavailable</option>';
    setStatus("Could not load roles. Check that the server is running, then refresh.", true);
  }
}

function setStatus(msg, isError = false) {
  $("status").textContent = msg;
  $("status").className = isError ? "status-line error" : "status-line";
}

function chips(list) {
  return `<div class="chips">${(list || []).map((e) => `<span class="chip">${esc(e)}</span>`).join("")}</div>`;
}

function renderMatrix(rows) {
  const head = `<div class="g-row g-head" role="row"><span>SKILL / REQUIREMENT</span><span>SHARE OF POSTINGS</span><span>IN REPO?</span><span>RECEIPTS</span></div>`;
  const body = (rows || []).map((r) => {
    const minimumPostings = Number(r.minimum_postings_for_market_assessment || 10);
    const marketAvailable = r.market_status !== "insufficient_market_data"
      && Number(r.total_postings) >= minimumPostings;
    const pct = marketAvailable ? Math.max(0, Math.min(100, Math.round(Number(r.pct || 0) * 100))) : 0;
    let mark = '<span class="mark no" title="No evidence found in the inspected repository" aria-label="No evidence found">✕</span>';
    if (!r.detectable) mark = '<span class="mark na" title="Cannot be judged from code alone" aria-label="Needs human review">CHECK</span>';
    else if (r.in_repo) mark = '<span class="mark yes" title="Evidence found" aria-label="Evidence found">✓</span>';
    const evidence = r.evidence_status === "not_assessed"
      ? "Not assessed from repository contents; review the write-up or demo manually"
      : r.evidence_status === "no_evidence_found"
        ? "No matching evidence found in inspected files"
        : (r.evidence || []).join(", ") || "Evidence found";
    const priority = Math.round(Number(r.role_weight || 0) * 100);
    const market = marketAvailable
      ? `<div class="bar" aria-label="${pct}% of postings mention this skill"><span style="width:${pct}%"></span></div><span class="bar-label">${esc(r.postings_mentioning)} of ${esc(r.total_postings)} postings · ${pct}%</span>`
      : `<span class="bar-label">${esc(r.total_postings)} real posting(s); insufficient market data (at least ${minimumPostings} needed)</span>`;
    return `<div class="g-row" role="row">
      <span>${esc(r.label)}<span class="bar-label">Role priority: ${priority}%</span></span>
      <span>${market}</span>
      <span>${mark}</span>
      <span class="ev">${esc(evidence)}</span>
    </div>`;
  }).join("");
  $("matrix").innerHTML = head + (body || '<div class="g-row"><span>No skill comparisons were returned for this review.</span></div>');
}

function renderNotes(items, cls) {
  return (items || []).map((f) => `<article class="note ${cls}">
      <h3>${esc(f.title || "Review note")}</h3>
      ${f.detail ? `<p>${esc(f.detail)}</p>` : ""}
      ${chips(f.evidence)}
    </article>`).join("");
}

function renderPlan(plan) {
  const safePlan = plan || {};
  const min = safePlan.total_hours_min ?? 0;
  const max = safePlan.total_hours_max ?? min;
  const perDay = safePlan.hours_per_day ?? "your selected";
  $("planNote").textContent = `${safePlan.note || "A suggested sequence based on the findings above."} Estimated effort: about ${min}–${max} hours at ${perDay} hours/day. Treat this as a planning estimate, not a promise.`;
  const fixes = (safePlan.fixes || []).map((f) => `<article class="fix">
      <h3>${esc(f.title)}</h3>
      <div class="hrs">${esc(f.hours_min ?? "?")}–${esc(f.hours_max ?? "?")} HRS</div>
      <p>${esc(f.why || "")}</p>
      ${(f.reasons || []).length ? `<ul>${f.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
    </article>`).join("");
  const days = (safePlan.timetable || []).map((d) => `<div class="day"><b>DAY ${esc(d.day)} · ${esc(d.hours)} H</b>${(d.tasks || []).map((t) => `${esc(t.title)} (${esc(t.hours)} h)`).join(" · ")}</div>`).join("");
  const unaddressed = (safePlan.unaddressed_gaps || []).length
    ? `<article class="note"><h3>Not a quick fix — this may need a new feature or project</h3>${safePlan.unaddressed_gaps.map((g) => `<p>${esc(g.reason)}</p>`).join("")}</article>`
    : "";
  $("plan").innerHTML = (fixes || '<p class="note">No fixes were suggested from this posting sample. That is not a guarantee that the project has no gaps.</p>') + days + unaddressed;
}

function renderSignals(signals) {
  $("signals").innerHTML = (signals || []).map((s) => {
    const ok = s.value === s.good_when_true;
    return `<li><span class="mark ${ok ? "yes" : "no"}" aria-hidden="true">${ok ? "✓" : "✕"}</span><span>${esc(s.label)}${s.note ? ` <span class="small">${esc(s.note)}</span>` : ""}</span></li>`;
  }).join("") || '<li>No repository checks were returned.</li>';
}

function scoreComment(score) {
  if (score == null) return { sticker: "NOT ENOUGH EVIDENCE", explanation: "No detectable role skills were assessed in the inspected files." };
  if (score >= 9) return { sticker: "A+ ENERGY ✳", explanation: "Strong evidence coverage in this review. Keep checking the depth and quality of the implementation." };
  if (score >= 7.5) return { sticker: "SOLID DRAFT", explanation: "Good evidence in several areas, with a few marks still in the margin." };
  if (score >= 5.5) return { sticker: "PROMISING, REVISE", explanation: "Some useful evidence is here. Focus on the highest-priority gaps first." };
  if (score >= 3) return { sticker: "RED PEN TIME", explanation: "The current repository provides limited evidence against this rubric. That gives you a roadmap, not a verdict on your ability." };
  return { sticker: "BACK TO THE LAB", explanation: "Little checkable evidence was found by this rubric. Start with one small, demonstrable improvement at a time." };
}

function render(data) {
  const warning = data.postings?.warning || "";
  $("warning").hidden = !warning;
  $("warning").textContent = warning;
  const repo = data.repo || {};
  const role = data.role || {};
  const filesRead = (repo.files_read || []).length;
  const realPostingCount = Number(data.postings?.evidence_count ?? 0);
  const samplePostingCount = Number(data.postings?.sample_count ?? 0);
  const postingSummary = `${realPostingCount} real posting(s)${samplePostingCount ? `; ${samplePostingCount} synthetic sample(s) excluded` : ""}`;
  $("repoMeta").innerHTML = `<a href="${esc(repo.url || "#")}" target="_blank" rel="noopener noreferrer">${esc(repo.full_name || repo.url || "Repository")}</a> reviewed for <b>${esc(role.label || "selected role")}</b> against ${postingSummary}. Files read: ${filesRead}.${repo.tree_truncated ? " The repository file list was truncated by GitHub." : ""}`;
  $("summary").textContent = data.critique?.summary || "The review is complete. Check the evidence and notes below before deciding what to change.";
  const evidenceCheck = data.critique?.evidence_check || {};
  $("source").textContent = `Explanation source: ${data.critique?.source || "not specified"}. Checkable findings retained: ${evidenceCheck.findings_kept ?? "—"} of ${evidenceCheck.findings_total ?? "—"}.${data.critique?.llm_error ? " Model note: " + data.critique.llm_error : ""}`;
  renderMatrix(data.skill_matrix || []);
  $("findings").innerHTML = renderNotes(data.critique?.findings, "") || '<p class="note">No findings returned. This does not necessarily mean no gaps exist.</p>';
  $("strengths").innerHTML = renderNotes(data.critique?.strengths, "good") || '<p class="note good">No strengths were returned for this run.</p>';
  renderPlan(data.plan || {});
  renderSignals(data.signals || []);

  const score = data.repository_score;
  const comment = scoreComment(score);
  $("scoreNumber").innerHTML = score == null ? `—<span>/10</span>` : `${score.toFixed(1)}<span>/10</span>`;
  $("scoreSticker").textContent = comment.sticker;
  if (score == null) {
    $("scoreExplanation").textContent = comment.explanation;
  } else {
    $("scoreExplanation").textContent =
      `${comment.explanation} This score is based on detectable role skills found in inspected repository files. It does not depend on job postings, ` +
      "and missing repository evidence does not establish that you lack a skill.";
  }
  $("marketStatus").textContent = data.market_comparison_available
    ? `Market comparison available · ${data.real_posting_count} real job postings`
    : `Market comparison unavailable — ${data.real_posting_count} real job posting(s); at least 10 and matched role skills are required.`;
  $("results").hidden = false;
}

$("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const pasted = $("postings").value.split(/^\s*---\s*$/m).map((s) => s.trim()).filter(Boolean);
  $("go").disabled = true;
  $("go").querySelector("span:first-child").textContent = "Marking in progress…";
  setStatus("Reading repository files and comparing them with the selected role. This can take up to a minute.");
  try {
    const response = await fetch("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repo_url: $("repo").value.trim(),
        role: $("role").value,
        pasted_postings: pasted,
        hours_per_day: parseFloat($("hours").value) || 2
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The review could not be completed. Please try again.");
    latestRedmarginReview = data;
    setStatus("Review complete. Read the evidence before acting on any recommendation.");
    render(data);
    $("results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setStatus(error.message || "Something went wrong. Please try again.", true);
  } finally {
    $("go").disabled = false;
    $("go").querySelector("span:first-child").textContent = "Mark my repo";
  }
});

loadRoles();

function openHelp() {
  $("helpPanel").hidden = false;
  $("helpLauncher").setAttribute("aria-expanded", "true");
  $("agent-goal").focus();
}

function closeHelp() {
  $("helpPanel").hidden = true;
  $("helpLauncher").setAttribute("aria-expanded", "false");
  $("helpLauncher").focus();
}

$("helpLauncher").addEventListener("click", () => {
  if ($("helpPanel").hidden) {
    openHelp();
  } else {
    closeHelp();
  }
});

$("helpClose").addEventListener("click", closeHelp);

const agentButton = $("agent-send");

if (agentButton) {
  agentButton.addEventListener("click", async () => {
    const goalInput = $("agent-goal");
    const hoursInput = $("agent-hours");
    const status = $("agent-status");
    const results = $("agent-results");
    const roleSelect = $("role");
    const review = latestRedmarginReview || {};
    const message = goalInput.value.trim();

    if (!message) {
      status.textContent = "Tell me what you want to learn or build.";
      goalInput.focus();
      return;
    }

    agentButton.disabled = true;
    status.textContent = "Building your learning and project roadmap...";
    results.replaceChildren();

    try {
      const response = await fetch("/api/help", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          role_id: review.role?.id || roleSelect?.value || "nlp",
          available_hours_per_day: Number(hoursInput.value) || 2,
          missing_skills: (review.skill_matrix || [])
            .filter((skill) => skill.detectable && skill.status === "gap")
            .map((skill) => skill.id),
          evidence_summary: [
            review.critique?.summary,
            ...(review.skill_matrix || [])
              .filter((skill) => skill.detectable
                && (skill.status === "gap" || skill.evidence_status === "evidence_found"))
              .slice(0, 20)
              .map((skill) => {
                if (skill.status === "gap") {
                  return `${skill.label}: no matching evidence found in inspected files.`;
                }
                const files = (skill.evidence || []).join(", ");
                return `${skill.label}: evidence found${files ? ` in ${files}` : ""}.`;
              })
          ].filter(Boolean).join("\n").slice(0, 5000)
        })
      });

      const data = await response.json();
      if (!response.ok) {
        const detail = typeof data.detail === "string"
          ? data.detail
          : "The agent request failed. Check the inputs and try again.";
        throw new Error(detail);
      }

      renderAgentRoadmap(data, results);
      status.textContent = "Roadmap ready.";
    } catch (error) {
      status.textContent = error.message || "Unable to contact the agent.";
    } finally {
      agentButton.disabled = false;
    }
  });
}

function renderAgentRoadmap(data, container) {
  const appendText = (tag, text, parent = container) => {
    const element = document.createElement(tag);
    element.textContent = text;
    parent.appendChild(element);
    return element;
  };

  appendText("h3", data.project.title);
  appendText("p", `Target role: ${data.role.label} · Skill: ${data.recommended_skill.label}`);
  appendText("p", data.project.why);
  appendText(
    "p",
    `Estimated effort: ${data.project.estimated_hours} hours. ` +
    `At your pace: about ${data.project.estimated_days_at_available_pace} days.`
  );
  const reviewContext = data.repository_context || {};
  if (reviewContext.evidence_summary || (reviewContext.missing_skills || []).length) {
    appendText("h4", "From your repository review");
    if (reviewContext.evidence_summary) {
      appendText("p", reviewContext.evidence_summary);
    }
    if ((reviewContext.missing_skills || []).length) {
      appendText(
        "p",
        `Detected review gaps: ${reviewContext.missing_skills
          .map((skill) => skill.replace(/_/g, " "))
          .join(", ")}. These are missing repository evidence, not proof you lack the skills.`
      );
    }
    if (reviewContext.note) appendText("p", reviewContext.note);
  }
  appendText("h4", "Implementation roadmap");

  const taskList = document.createElement("ol");
  for (const task of data.project.tasks) {
    const item = document.createElement("li");
    appendText("strong", `${task.name} (${task.hours} hours)`, item);
    appendText("p", `${task.required ? "Essential" : "Optional"} — ${task.deliverable}`, item);
    taskList.appendChild(item);
  }
  container.appendChild(taskList);

  appendText("h4", "Learning resources");
  const resourceList = document.createElement("ul");
  for (const resource of data.resources) {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.textContent = resource.title;
    try {
      const url = new URL(resource.url);
      if (url.protocol !== "https:" && url.protocol !== "http:") continue;
      link.href = url.href;
    } catch {
      continue;
    }
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    item.appendChild(link);
    appendText("p", resource.description, item);
    resourceList.appendChild(item);
  }
  container.appendChild(resourceList);

  appendText("h4", "What makes this resume-worthy?");
  const resumeList = document.createElement("ul");
  for (const evidence of data.resume_evidence) {
    appendText("li", evidence, resumeList);
  }
  container.appendChild(resumeList);
  appendText("p", data.limitations.join(" "));
}
