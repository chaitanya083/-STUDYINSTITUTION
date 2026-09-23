const state = { token: localStorage.getItem("study_token"), user: null, role: null, data: {} };
const $ = (selector) => document.querySelector(selector);
const roleNames = { super_admin: "Super Admin", institution_admin: "Institution Admin", professor: "Professor", student: "Student" };

function setVisible(selector, visible) { $(selector).classList.toggle("hidden", !visible); }
function role() { return state.user?.roles?.find((item) => roleNames[item]) || "student"; }
function authHeaders() { return { Authorization: `Bearer ${state.token}` }; }
async function api(path) {
  const response = await fetch(path, { headers: authHeaders() });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed (${response.status})`);
  }
  return response.json();
}
function showNotice(message) { $("#notice").textContent = message; setVisible("#notice", Boolean(message)); }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char])); }
function formatStatus(value) { return String(value || "").replaceAll("_", " "); }

function renderNavigation() {
  const entries = { super_admin: ["Overview", "Institutions", "Plans", "Users"], institution_admin: ["Overview", "People", "Courses", "Structure"], professor: ["Overview", "Courses", "Students"], student: ["Overview", "My courses", "History", "Sessions"] };
  $("#nav").innerHTML = (entries[state.role] || entries.student).map((name, index) => `<button class="nav-item ${index === 0 ? "active" : ""}" type="button">${name}</button>`).join("");
}
function renderScope() {
  const scopes = { super_admin: [["Platform control", "Manage institutions, plans, payments, and accounts."], ["Monitoring", "Inspect users and active sessions across the platform."]], institution_admin: [["Institution control", "Manage people, courses, subscriptions, and organization structure."], ["Tenant boundary", "All records shown belong to your institution."]], professor: [["Teaching scope", "View institution courses and the students you teach."], ["Course work", "Assign available courses within your permitted scope."]], student: [["Learning space", "Open assigned courses and track your learning activity."], ["Sessions", "Review active sign-ins and terminate sessions when needed."]] };
  $("#scope-list").innerHTML = scopes[state.role].map(([title, text]) => `<div class="scope-item"><span class="scope-dot"></span><div><strong>${title}</strong><span>${text}</span></div></div>`).join("");
}
function renderStats(stats) {
  $("#stat-grid").innerHTML = stats.map(([label, value]) => `<article class="stat-card"><span class="stat-label">${label}</span><strong class="stat-value">${escapeHtml(value)}</strong></article>`).join("");
}
function renderSummary(summary) {
  renderStats([["Institutions", summary.institutions.toLocaleString()], ["Active students", summary.active_students.toLocaleString()], ["Professors", summary.professors.toLocaleString()], ["Active courses", summary.active_courses.toLocaleString()], ["Active sessions", summary.active_sessions.toLocaleString()]]);
  $("#subscription-status").textContent = summary.subscription_status;
  $("#subscription-progress").style.width = `${summary.subscription_progress}%`;
}
function renderTable(title, columns, rows) {
  $("#table-title").textContent = title;
  if (!rows.length) { $("#table-wrap").innerHTML = '<div class="empty-state">No records to display yet.</div>'; return; }
  $("#table-wrap").innerHTML = `<table><thead><tr>${columns.map((column) => `<th>${column.label}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(column.value(row))}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
async function loadSuperAdmin() {
  const [summary, institutions, plans, users] = await Promise.all([api("/dashboard/summary"), api("/admin/institutions"), api("/plans"), api("/admin/users")]);
  renderSummary(summary);
  state.data = { institutions, plans, users };
  renderStats([["Institutions", institutions.length], ["Active plans", plans.filter((item) => item.is_active).length], ["Platform users", users.length], ["Pending work", "-"]]);
  renderTable("Institutions", [{ label: "Institution", value: (item) => item.name }, { label: "Code", value: (item) => item.code }, { label: "Status", value: (item) => formatStatus(item.status) }], institutions);
}
async function loadInstitutionAdmin() {
  const [summary, institution, subscription, members, courses, branches] = await Promise.all([api("/dashboard/summary"), api("/institutions/me"), api("/institutions/me/subscription").catch(() => null), api("/institutions/me/members"), api("/courses"), api("/institutions/me/branches")]);
  renderSummary(summary);
  state.data = { institution, subscription, members, courses, branches };
  renderStats([["Members", members.length], ["Courses", courses.length], ["Branches", branches.length], ["Plan", subscription ? formatStatus(subscription.status) : "None"]]);
  renderTable(institution.name, [{ label: "Name", value: (item) => item.display_name || item.user_id }, { label: "Role", value: (item) => formatStatus(item.role) }, { label: "Active", value: (item) => item.is_active ? "Yes" : "No" }], members);
}
async function loadProfessor() {
  const [summary, courses, students] = await Promise.all([api("/dashboard/summary"), api("/professor/courses"), api("/professor/students")]);
  renderSummary(summary);
  state.data = { courses, students };
  renderStats([["Courses", courses.length], ["Students", students.length], ["Assignments", "-"], ["Status", "Active"]]);
  renderTable("Teaching workspace", [{ label: "Course", value: (item) => item.title }, { label: "Description", value: (item) => item.description || "No description" }], courses);
}
async function loadStudent() {
  const [summary, courses, history, sessions] = await Promise.all([api("/dashboard/summary"), api("/student/courses"), api("/history"), api("/sessions")]);
  renderSummary(summary);
  state.data = { courses, history, sessions };
  renderStats([["Assigned courses", courses.length], ["Completed sessions", history.length], ["Active sessions", sessions.length], ["Learning status", courses.length ? "Ready" : "Waiting"]]);
  renderTable("My courses", [{ label: "Course", value: (item) => item.title }, { label: "Description", value: (item) => item.description || "No description" }], courses);
}
async function loadDashboard() {
  showNotice("");
  $("#refresh-button").disabled = true;
  try {
    if (state.role === "super_admin") await loadSuperAdmin();
    else if (state.role === "institution_admin") await loadInstitutionAdmin();
    else if (state.role === "professor") await loadProfessor();
    else await loadStudent();
  } catch (error) {
    showNotice(error.message);
  } finally { $("#refresh-button").disabled = false; }
}
async function authenticate(token) {
  state.token = token;
  const user = await api("/me");
  state.user = user;
  state.role = role();
  localStorage.setItem("study_token", token);
  $("#role-pill").textContent = roleNames[state.role] || "User";
  $("#page-title").textContent = `${roleNames[state.role] || "User"} dashboard`;
  renderNavigation(); renderScope(); setVisible("#login-view", false); setVisible("#dashboard-view", true); setVisible("#logout-button", true);
  await loadDashboard();
}
function logout() { localStorage.removeItem("study_token"); state.token = null; state.user = null; setVisible("#dashboard-view", false); setVisible("#login-view", true); setVisible("#logout-button", false); $("#role-pill").textContent = "Signed out"; $("#login-form").reset(); }

$("#login-form").addEventListener("submit", async (event) => { event.preventDefault(); $("#login-error").textContent = ""; const button = event.target.querySelector("button"); button.disabled = true; try { const form = new FormData(event.target); const response = await fetch("/auth/token", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) }); const result = await response.json(); if (!response.ok) throw new Error(result.detail || "Unable to sign in"); await authenticate(result.access_token); } catch (error) { $("#login-error").textContent = error.message; } finally { button.disabled = false; } });
$("#logout-button").addEventListener("click", logout);
$("#refresh-button").addEventListener("click", loadDashboard);
if (state.token) authenticate(state.token).catch(logout);
