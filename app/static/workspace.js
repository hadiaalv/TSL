/* Department workspace: sidebar departments, Kanban boards, task drawer,
   notifications page, reports and department management.
   Relies on globals from app.js ($, $$, api, esc, formatTimestamp, formatDuration, setStatus). */
(function () {
  const STATUS_LABEL = {
    open: "To Do", in_progress: "In Progress", delegated: "Delegated", waiting_on: "Review",
    blocked: "Blocked", done: "Completed", cancelled: "Cancelled",
  };
  const COLUMNS = [
    { key: "todo", label: "To Do", drop: "open", statuses: ["open"] },
    { key: "progress", label: "In Progress", drop: "in_progress", statuses: ["in_progress", "delegated"] },
    { key: "review", label: "Review", drop: "waiting_on", statuses: ["waiting_on", "blocked"] },
    { key: "done", label: "Completed", drop: "done", statuses: ["done", "cancelled"] },
  ];
  const SCOPES = {
    mine: "My Tasks", all: "All Tasks", unassigned: "Unassigned Tasks",
    completed: "Completed Tasks", overdue: "Overdue Tasks",
  };
  const EVENT_LABEL = {
    assignment: "Assigned", review: "Review requested", unassignment: "Removed", reassignment: "Reassigned",
    mention: "Mentioned", comment: "New comment",
  };

  const state = {
    me: null,
    isSA: false,
    canManageTasks: false,
    departmentIds: [],
    departments: [],
    members: [],
    businesses: [],
    reminderShown: false,
    route: null,
    filters: { department: "", assignee: "", business: "", text: "" },
    tasks: [],
    notifFilter: "all",
    drawerTaskId: null,
  };

  const deptById = (id) => state.departments.find((d) => d.id === id);
  const canManageDepartment = (id) => state.isSA || Boolean(id && state.departmentIds.includes(id));
  const canManageTask = (task) => state.isSA || (state.canManageTasks && canManageDepartment(task.department_id));
  const initials = (name) => String(name || "?").trim().split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  const today = () => new Date().toISOString().slice(0, 10);
  const fmtDate = (value) => (value ? String(value).slice(0, 10) : "");
  const json = (method, body) => ({ method, body: JSON.stringify(body || {}) });

  function deptChip(id, name, color) {
    const d = deptById(id);
    const label = name || d?.name || "No department";
    const c = color || d?.color || "#94a3b8";
    return `<span class="dept-chip" style="--dept:${esc(c)}">${esc(label)}</span>`;
  }

  function toast(message) {
    if (window.setStatus) window.setStatus(message);
  }

  // ── Sidebar ───────────────────────────────────────────────
  async function loadDepartments() {
    try {
      state.departments = await api("/api/departments");
    } catch (error) {
      state.departments = [];
      toast(error.message);
    }
    renderSidebar();
  }

  function renderSidebar() {
    const box = $("#sidebar-departments");
    if (!box) return;
    box.innerHTML = state.departments.length
      ? state.departments.map((d) => `
        <a href="#dept/${esc(d.id)}" data-route="dept/${esc(d.id)}" class="sidebar-nav-link dept-link">
          <span class="dept-dot" style="background:${esc(d.color)}" aria-hidden="true">${esc(d.icon || initials(d.name)[0])}</span>
          <span>${esc(d.name)}</span>
          ${d.open_count ? `<em class="count-badge">${d.open_count}</em>` : ""}
        </a>`).join("")
      : `<p class="muted nav-empty">No departments yet.</p>`;
    const route = currentRoute();
    $$("[data-route]").forEach((l) => l.classList.toggle("active", l.dataset.route === (route.nav || route.view)));
  }

  async function refreshCounts() {
    try {
      const mine = await api("/api/tasks?view=mine&limit=500");
      const badge = $("#count-mine");
      if (badge) {
        badge.textContent = String(mine.length);
        badge.classList.toggle("hidden", !mine.length);
      }
    } catch { /* non-critical */ }
  }

  async function onSession(user) {
    state.me = user;
    state.isSA = Boolean(user?.is_super_admin);
    state.canManageTasks = Boolean(user?.can_manage_tasks);
    state.departmentIds = user?.department_ids || [];
    $$(".sa-only").forEach((el) => el.classList.toggle("hidden", !state.isSA));
    $$(".admin-only").forEach((el) => el.classList.toggle("hidden", !state.canManageTasks));
    document.body.classList.toggle("not-sa", !state.isSA);
    await loadDepartments();
    refreshCounts();
    try {
      state.members = await api("/api/team/members");
    } catch { state.members = []; }
    try {
      state.businesses = await api("/api/businesses");
    } catch (error) {
      state.businesses = [];
      toast(`Could not load brands: ${error.message}`);
    }
    if (state.route) onRoute(state.route);
    showPendingTaskReminder().catch((error) => toast(`Could not load pending-task reminders: ${error.message}`));
  }

  async function showPendingTaskReminder() {
    if (state.reminderShown) return;
    state.reminderShown = true;
    const tasks = await api("/api/tasks?view=mine&limit=500");
    if (!tasks.length) return;
    const modal = $("#pending-task-reminder");
    const list = $("#pending-task-reminder-list");
    if (!modal || !list) return;
    list.innerHTML = tasks.map((task) => `
      <li><button type="button" data-reminder-task="${esc(task.id)}">
        <strong>${esc(task.title)}</strong>
        <span>${esc(task.business_name || "General")} · ${esc(task.department_name || "No department")} · ${esc(STATUS_LABEL[task.status] || task.status)}</span>
      </button></li>`).join("");
    modal.classList.remove("hidden");
    $("#pending-task-reminder-close").addEventListener("click", () => modal.classList.add("hidden"), { once: true });
    list.querySelectorAll("[data-reminder-task]").forEach((button) => button.addEventListener("click", () => {
      modal.classList.add("hidden");
      openTask(button.dataset.reminderTask);
    }));
  }

  $("#departments-toggle")?.addEventListener("click", (event) => {
    const open = event.currentTarget.getAttribute("aria-expanded") !== "true";
    event.currentTarget.setAttribute("aria-expanded", String(open));
    $("#sidebar-departments")?.classList.toggle("hidden", !open);
  });

  // ── Routing ───────────────────────────────────────────────
  function onRoute(route) {
    state.route = route;
    if (!state.me) return;
    if (route.view === "workspace") renderWorkspace().catch((e) => toast(e.message));
    else if (route.view === "notifications") renderNotifications().catch((e) => toast(e.message));
    else if (route.view === "reports") renderReports();
    else if (route.view === "deptmgmt") renderDeptMgmt().catch((e) => toast(e.message));
    if (route.view === "workspace") {
      const t = $("#page-title");
      if (t) t.textContent = route.scope === "dept" ? (deptById(route.departmentId)?.name || "Department") : (SCOPES[route.scope] || "Tasks");
    }
  }

  // ── Workspace / Kanban ────────────────────────────────────
  function taskQuery(route) {
    const p = new URLSearchParams({ limit: "500" });
    const f = state.filters;
    if (route.scope === "dept") {
      p.set("department_id", route.departmentId);
      p.set("open_only", "false");
    } else if (route.scope === "all") {
      p.set("open_only", "false");
      if (f.department) p.set("department_id", f.department);
    } else if (SCOPES[route.scope]) {
      p.set("view", route.scope);
      if (route.scope === "completed") p.set("open_only", "false");
      if (f.department) p.set("department_id", f.department);
    }
    if (f.business) p.set("business_id", f.business);
    if (f.assignee && state.canManageTasks) p.set("assignee_id", f.assignee);
    return p.toString();
  }

  async function renderWorkspace() {
    const root = $("#workspace-root");
    const route = state.route;
    if (!root) return;
    const isDept = route.scope === "dept";
    const dept = isDept ? deptById(route.departmentId) : null;
    const f = state.filters;
    const chips = Object.entries(SCOPES).map(([k, label]) =>
      `<a href="#tasks/${k}" class="ws-chip${route.scope === k ? " active" : ""}">${esc(label)}</a>`).join("");
    root.innerHTML = `
      <div class="ws-head">
        <div>
          ${dept ? `<span class="eyebrow">${deptChip(dept.id)}</span>` : `<span class="eyebrow">Workspace</span>`}
          <h2>${esc(isDept ? (dept?.name || "Department") : SCOPES[route.scope] || "Tasks")}</h2>
          ${dept?.description ? `<p class="muted">${esc(dept.description)}</p>` : ""}
        </div>
        ${state.canManageTasks && (!isDept || canManageDepartment(route.departmentId)) ? `<button type="button" class="primary-action" id="ws-new-task">+ New task</button>` : ""}
      </div>
      <div class="ws-chips">${chips}</div>
      <div class="ws-filters">
        <input type="search" id="ws-text" placeholder="Filter by title…" value="${esc(f.text)}" aria-label="Filter by title" />
        <select id="ws-business" aria-label="Brand"><option value="">All brands</option>${state.businesses.map((b) => `<option value="${esc(b.id)}"${f.business === b.id ? " selected" : ""}>${esc(b.name)}</option>`).join("")}</select>
        ${isDept ? "" : `<select id="ws-dept" aria-label="Department"><option value="">All departments</option>${state.departments.map((d) => `<option value="${esc(d.id)}"${f.department === d.id ? " selected" : ""}>${esc(d.name)}</option>`).join("")}</select>`}
        ${state.canManageTasks ? `<select id="ws-assignee" aria-label="Assignee"><option value="">Anyone</option>${state.members.map((m) => `<option value="${esc(m.id)}"${f.assignee === m.id ? " selected" : ""}>${esc(m.display_name)}</option>`).join("")}</select>` : ""}
      </div>
      <div id="ws-board" class="ws-board" aria-live="polite"><p class="muted">Loading tasks…</p></div>`;
    $("#ws-new-task")?.addEventListener("click", () => openTask(null, { department_id: isDept ? route.departmentId : "" }));
    $("#ws-text").addEventListener("input", (e) => { state.filters.text = e.target.value; paintBoard(); });
    ["ws-business:business", "ws-dept:department", "ws-assignee:assignee"].forEach((pair) => {
      const [id, key] = pair.split(":");
      $(`#${id}`)?.addEventListener("change", (e) => { state.filters[key] = e.target.value; renderWorkspace(); });
    });
    try {
      state.tasks = await api(`/api/tasks?${taskQuery(route)}`);
      paintBoard();
    } catch (error) {
      $("#ws-board").innerHTML = `<p class="form-error">${esc(error.message)}</p>`;
    }
  }

  function cardMarkup(t) {
    const names = t.assignees || [];
    const businessName = t.business_name || state.businesses.find((business) => business.id === t.business_id)?.name || "General";
    const avatars = names.length
      ? `<span class="avatars">${names.slice(0, 3).map((a) => `<span class="avatar" title="${esc(a.display_name)}">${esc(initials(a.display_name))}</span>`).join("")}${names.length > 3 ? `<span class="avatar more">+${names.length - 3}</span>` : ""}</span>`
      : `<span class="muted unassigned-tag">Unassigned</span>`;
    const overdue = t.deadline && fmtDate(t.deadline) < today() && !["done", "cancelled"].includes(t.status);
    const canMove = state.canManageTasks
      ? canManageTask(t)
      : (t.assignee_ids || []).includes(state.me?.id);
    return `<article class="ws-card" draggable="${canMove}" tabindex="0" data-id="${esc(t.id)}" data-status="${esc(t.status)}">
      <div class="ws-card-top">${deptChip(t.department_id, t.department_name, t.department_color)}
        <span class="brand-chip"><span class="brand-chip-label">Brand:</span> ${esc(businessName)}</span>
        ${["P0", "P1"].includes(t.priority) ? `<span class="priority-pill priority-${esc(t.priority.toLowerCase())}">${esc(t.priority)}</span>` : ""}
      </div>
      <h4>${esc(t.title)}</h4>
      <div class="ws-card-meta">
        ${avatars}
        <span class="ws-status">${esc(STATUS_LABEL[t.status] || t.status)}</span>
        ${t.deadline ? `<span class="due${overdue ? " overdue" : ""}">📅 ${esc(fmtDate(t.deadline))}</span>` : ""}
        <span class="comments-count" title="Comments">💬 ${t.comment_count || 0}${t.unread_comments ? `<i class="unread-dot" title="Unread conversation"></i>` : ""}</span>
      </div>
    </article>`;
  }

  function paintBoard() {
    const board = $("#ws-board");
    if (!board) return;
    const text = state.filters.text.trim().toLowerCase();
    const list = state.tasks.filter((t) => !text || t.title.toLowerCase().includes(text));
    const canQuickAdd = state.canManageTasks
      && state.route?.scope === "dept"
      && canManageDepartment(state.route.departmentId);
    board.innerHTML = COLUMNS.map((col) => {
      const items = list.filter((t) => col.statuses.includes(t.status));
      return `<section class="ws-col" data-drop="${col.drop}" aria-label="${col.label}">
        <header><h3>${col.label}</h3><span class="count-badge">${items.length}</span></header>
        <div class="ws-col-body">${items.map(cardMarkup).join("") || `<p class="muted ws-empty">No tasks</p>`}</div>
        ${canQuickAdd ? `<form class="ws-quick-add" data-status="${col.drop}">
          <input name="title" maxlength="300" placeholder="＋ Add a task" aria-label="Add a task to ${col.label}" autocomplete="off" />
          <select name="business_id" aria-label="Brand" required>${state.businesses.map((business) => `<option value="${esc(business.id)}"${business.id === (state.filters.business || state.businesses.find((item) => item.slug === "general")?.id || state.businesses[0]?.id) ? " selected" : ""}>${esc(business.name)}</option>`).join("")}</select>
          <input name="approver_name" maxlength="160" placeholder="Approver (optional)" aria-label="External approver" />
        </form>` : ""}
      </section>`;
    }).join("");
    board.querySelectorAll(".ws-quick-add").forEach((form) => form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const title = form.title.value.trim();
      if (!title) return;
      form.title.disabled = true;
      try {
        await api("/api/tasks", json("POST", {
          title,
          status: form.dataset.status,
          department_id: state.route.departmentId,
          business_id: form.elements.business_id.value,
          approver_name: form.elements.approver_name.value.trim(),
        }));
        await renderWorkspace();
        loadDepartments();
        toast("Task added — open it to assign people");
      } catch (error) {
        form.title.disabled = false;
        toast(`Could not add task: ${error.message}`);
      }
    }));
    board.querySelectorAll(".ws-card").forEach((card) => {
      card.addEventListener("click", () => openTask(card.dataset.id));
      card.addEventListener("keydown", (e) => { if (e.key === "Enter") openTask(card.dataset.id); });
      card.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", card.dataset.id));
    });
    board.querySelectorAll(".ws-col").forEach((col) => {
      col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("drag-over"); });
      col.addEventListener("dragleave", () => col.classList.remove("drag-over"));
      col.addEventListener("drop", async (e) => {
        e.preventDefault();
        col.classList.remove("drag-over");
        const id = e.dataTransfer.getData("text/plain");
        const task = state.tasks.find((t) => t.id === id);
        const column = COLUMNS.find((c) => c.drop === col.dataset.drop);
        const canUpdateStatus = task && (state.canManageTasks
          ? canManageTask(task)
          : (task.assignee_ids || []).includes(state.me?.id));
        if (!task || !column || !canUpdateStatus || column.statuses.includes(task.status)) return;
        try {
          await api(`/api/tasks/${encodeURIComponent(id)}`, json("PATCH", { status: col.dataset.drop }));
          await renderWorkspace();
          loadDepartments();
        } catch (error) {
          toast(`Could not move task: ${error.message}`);
        }
      });
    });
  }

  // ── Task drawer ───────────────────────────────────────────
  function closeDrawer() {
    $("#task-drawer")?.classList.add("hidden");
    document.body.classList.remove("drawer-open");
    state.drawerTaskId = null;
  }

  $("#task-drawer")?.addEventListener("click", (e) => { if (e.target.closest("[data-drawer-close]")) closeDrawer(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.drawerTaskId !== null) closeDrawer(); });

  async function openTask(taskId, defaults = {}) {
    if (!state.me) return;
    if (!state.businesses.length && state.canManageTasks) {
      try { state.businesses = await api("/api/businesses"); } catch { /* optional */ }
    }
    let task = null;
    let comments = [];
    let deptMembers = [];
    if (taskId) {
      try {
        [task, comments] = await Promise.all([
          api(`/api/tasks/${encodeURIComponent(taskId)}`),
          api(`/api/tasks/${encodeURIComponent(taskId)}/comments`),
        ]);
      } catch (error) {
        toast(`Could not open task: ${error.message}`);
        return;
      }
    }
    const defaultBusinessId = defaults.business_id || state.filters.business
      || state.businesses.find((business) => business.slug === "general")?.id || "";
    const view = task || {
      title: "",
      description: "",
      status: "open",
      deadline: "",
      assignees: [],
      assignee_ids: [],
      checklist: [],
      links: [],
      business_id: defaultBusinessId,
      department_id: defaults.department_id || "",
      comment_count: 0,
    };
    state.drawerTaskId = taskId || "";
    const draft = { assignee_ids: [...(view.assignee_ids || [])], checklist: (view.checklist || []).map((c) => ({ ...c })), links: (view.links || []).map((l) => ({ ...l })) };
    const isAssignee = task && (task.assignee_ids || []).includes(state.me.id);
    const canEdit = task ? canManageTask(task) : state.canManageTasks;
    const canStatus = task
      ? (state.canManageTasks ? canManageTask(task) : isAssignee)
      : state.canManageTasks;
    const loadDeptMembers = async (deptId) => {
      if (!deptId || !state.canManageTasks) { deptMembers = []; return; }
      try { deptMembers = await api(`/api/departments/${encodeURIComponent(deptId)}/members`); } catch { deptMembers = []; }
    };
    await loadDeptMembers(view.department_id);

    const panel = $("#task-drawer-panel");
    const nameOf = (id) => (view.assignees || []).find((a) => a.id === id)?.display_name || state.members.find((m) => m.id === id)?.display_name || deptMembers.find((m) => m.id === id)?.display_name || id;

    let assignOpen = false;
    const paint = () => {
      const activeMembers = deptMembers.filter((m) => m.status === "active");
      const assignBlock = canEdit
        ? (view.department_id
          ? `<details class="assign-pop"${assignOpen ? " open" : ""}><summary>＋ Assign people</summary>
              <p class="assign-brand">For brand: <strong>${esc(view.business_name || (state.businesses.find((b) => b.id === view.business_id)?.name) || "General")}</strong></p>
              <div class="assign-list">${activeMembers.map((m) => `<label><input type="checkbox" data-assign="${esc(m.id)}" ${draft.assignee_ids.includes(m.id) ? "checked" : ""} /> <span class="avatar">${esc(initials(m.display_name))}</span> ${esc(m.display_name)}</label>`).join("") || `<p class="muted">No members in this department yet. Add people in Department Management.</p>`}</div>
            </details>`
          : `<p class="muted">Choose a department first.</p>`)
        : "";
      const side = `<aside class="td-side">
          <div class="td-field"><span>Status</span><select name="status" form="drawer-form" ${canStatus ? "" : "disabled"}>
            ${Object.entries(STATUS_LABEL).map(([k, v]) => `<option value="${k}"${view.status === k ? " selected" : ""}>${v}</option>`).join("")}
          </select></div>
          <div class="td-field"><span>Assignees</span>
            <div class="assignee-chips">${draft.assignee_ids.map((id) => `<span class="assignee-chip"><span class="avatar">${esc(initials(nameOf(id)))}</span>${esc(nameOf(id))}${canEdit ? `<button type="button" data-remove-assignee="${esc(id)}" aria-label="Remove">×</button>` : ""}</span>`).join("") || `<span class="muted">Unassigned</span>`}</div>
            ${assignBlock}
          </div>
          <div class="td-field"><span>Department</span><select name="department_id" form="drawer-form" ${canEdit ? "" : "disabled"} ${!task && state.me?.role === "admin" ? "required" : ""}>
            ${!task && state.me?.role === "admin"
              ? `<option value="" disabled${view.department_id ? "" : " selected"}>Choose a department</option>`
              : `<option value="">— None —</option>`}
            ${state.departments.filter((d) => !canEdit || canManageDepartment(d.id)).map((d) => `<option value="${esc(d.id)}"${view.department_id === d.id ? " selected" : ""}>${esc(d.name)}</option>`).join("")}
          </select></div>
          ${task ? `<div class="td-field"><span>Brand</span><strong class="td-brand-name">${esc(view.business_name || "General")}</strong></div>` : ""}
          <div class="td-field"><span>Due date</span><input type="date" name="deadline" form="drawer-form" value="${esc(fmtDate(view.deadline))}" ${canEdit ? "" : "disabled"} /></div>
          ${canEdit && !task ? `<div class="td-field"><span>Brand</span><select name="business_id" form="drawer-form" required>${state.businesses.map((b) => `<option value="${esc(b.id)}"${view.business_id === b.id ? " selected" : ""}>${esc(b.name)}</option>`).join("")}</select></div>` : ""}
          ${canEdit && !task ? `<label class="td-field"><span>Approver</span><input name="approver_name" form="drawer-form" maxlength="160" placeholder="CEO, client, etc. (optional)" /></label>` : ""}
          ${task ? `<p class="muted drawer-time">⏱ Tracked: ${esc(formatDuration(task.tracked_seconds || 0))}${(task.running_timers || []).length ? ` · running: ${(task.running_timers || []).map((r) => esc(nameOf(r.user_id))).join(", ")}` : ""}<br />Your time is logged while you are an assignee and the task is In Progress.</p>` : ""}
        </aside>`;
      panel.innerHTML = `
        <header class="drawer-head">
          <div>${deptChip(view.department_id, view.department_name, view.department_color)}
          ${task ? `<span class="muted"> Created by ${esc(task.created_by_name || "—")} · ${esc(formatTimestamp(task.created_at))}</span>` : `<span class="muted"> New task</span>`}</div>
          <button type="button" class="ghost" data-drawer-close aria-label="Close">✕</button>
        </header>
        <div class="td-layout">
        <div class="td-main">
        <form id="drawer-form" class="drawer-body">
          <input class="td-title" name="title" maxlength="300" required placeholder="Task title" value="${esc(view.title)}" ${canEdit ? "" : "disabled"} />
          <label>Description <textarea name="description" rows="4" placeholder="Add details…" ${canEdit ? "" : "disabled"}>${esc(view.description || "")}</textarea></label>

          <div class="drawer-section">
            <h4>Checklist</h4>
            <ul class="checklist">${draft.checklist.map((c, i) => `<li><label><input type="checkbox" data-check="${i}" ${c.done ? "checked" : ""} ${canStatus ? "" : "disabled"} /> <span>${esc(c.text)}</span></label>${canEdit ? `<button type="button" class="ghost mini" data-del-check="${i}" aria-label="Remove item">×</button>` : ""}</li>`).join("") || `<li class="muted">No items</li>`}</ul>
            ${canEdit ? `<div class="inline-add"><input id="check-new" placeholder="Add checklist item" maxlength="300" /><button type="button" class="ghost" id="check-add">Add</button></div>` : ""}
          </div>

          <div class="drawer-section">
            <h4>Links &amp; attachments</h4>
            <ul class="link-list">${draft.links.map((l, i) => `<li><a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label || l.url)}</a> <span class="muted">${esc(l.kind || "link")}</span>${canEdit ? `<button type="button" class="ghost mini" data-del-link="${i}" aria-label="Remove link">×</button>` : ""}</li>`).join("") || `<li class="muted">None</li>`}</ul>
            ${canEdit ? `<div class="inline-add"><input id="link-url" placeholder="https://…" /><input id="link-label" placeholder="Label" maxlength="200" /><select id="link-kind"><option value="link">Link</option><option value="attachment">Attachment URL</option></select><button type="button" class="ghost" id="link-add">Add</button></div>` : ""}
          </div>

          <p id="drawer-error" class="form-error" role="alert"></p>
          ${!task && canEdit ? `<div class="drawer-actions"><button type="submit" class="primary-action">Create task</button></div>` : ""}
        </form>
        ${task ? `<section class="drawer-comments">
          <h4>Conversation <span class="muted">(${comments.length})</span></h4>
          <div class="comment-list">${comments.map((c) => `<article class="comment"><strong>${esc(c.author_display_name || c.author_username)}</strong> <time>${esc(formatTimestamp(c.created_at))}</time><p>${esc(c.content).replace(/(^|\s)@([\w.-]+)/g, '$1<span class="mention">@$2</span>')}</p></article>`).join("") || `<p class="muted">No comments yet.</p>`}</div>
          <form id="comment-form" class="comment-form">
            <div class="mention-wrap"><textarea name="content" rows="2" maxlength="2000" placeholder="Write a comment… use @ to mention" required></textarea><div id="mention-menu" class="mention-menu hidden"></div></div>
            <button type="submit" class="primary-action">Comment</button>
          </form>
        </section>` : ""}
        </div>
        ${side}
        </div>`;
      bind();
    };

    const bind = () => {
      const form = $("#drawer-form");
      const collect = () => {
        const fd = new FormData(form);
        const body = {};
        if (canEdit) {
          body.title = String(fd.get("title") || "").trim();
          body.description = String(fd.get("description") || "");
          body.deadline = fd.get("deadline") || null;
          body.department_id = fd.get("department_id") || null;
          body.assignee_ids = draft.assignee_ids;
          body.checklist = draft.checklist;
          body.links = draft.links;
        } else {
          body.checklist = draft.checklist;
        }
        body.status = fd.get("status");
        return body;
      };
      const syncView = () => {
        const fd = new FormData(form);
        Object.assign(view, { title: fd.get("title"), description: fd.get("description"), status: fd.get("status"), deadline: fd.get("deadline") });
      };
      const persist = async (patch, reload = false) => {
        const errorEl = $("#drawer-error");
        if (errorEl) errorEl.textContent = "";
        try {
          await api(`/api/tasks/${encodeURIComponent(task.id)}`, json("PATCH", patch));
          toast("Saved");
          afterChange();
          if (reload) await openTask(task.id);
        } catch (error) {
          if (errorEl) errorEl.textContent = error.message;
          toast(error.message);
          if (reload) await openTask(task.id);
        }
      };
      const autosave = (name, build) => {
        if (!task) return;
        document.querySelectorAll(`#task-drawer-panel [name=${name}]`).forEach((el) => {
          el.addEventListener("change", () => { syncView(); persist(build(el.value)); });
        });
      };
      document.querySelector("#task-drawer-panel [name=department_id]")?.addEventListener("change", async (e) => {
        syncView();
        view.department_id = e.target.value;
        view.department_name = deptById(view.department_id)?.name || "";
        view.department_color = deptById(view.department_id)?.color || "";
        draft.assignee_ids = [];
        if (task) {
          await persist({ department_id: view.department_id || null, assignee_ids: [] }, true);
          return;
        }
        await loadDeptMembers(view.department_id);
        paint();
      });
      document.querySelector("#task-drawer-panel [name=business_id]")?.addEventListener("change", (e) => {
        view.business_id = e.target.value;
        view.business_name = state.businesses.find((business) => business.id === view.business_id)?.name || "General";
        paint();
      });
      document.querySelector(".assign-pop")?.addEventListener("toggle", (e) => { assignOpen = e.target.open; });
      const changeAssignees = (ids) => {
        syncView();
        draft.assignee_ids = ids;
        if (task) persist({ assignee_ids: ids }, true); else paint();
      };
      document.querySelectorAll("#task-drawer-panel [data-assign]").forEach((box) => box.addEventListener("change", () => {
        const id = box.dataset.assign;
        changeAssignees(box.checked ? [...new Set([...draft.assignee_ids, id])] : draft.assignee_ids.filter((x) => x !== id));
      }));
      document.querySelectorAll("#task-drawer-panel [data-remove-assignee]").forEach((b) => b.addEventListener("click", () => {
        changeAssignees(draft.assignee_ids.filter((id) => id !== b.dataset.removeAssignee));
      }));
      autosave("title", (v) => ({ title: v.trim() }));
      autosave("description", (v) => ({ description: v }));
      autosave("deadline", (v) => ({ deadline: v || null }));
      autosave("status", (v) => ({ status: v }));
      form.querySelectorAll("[data-check]").forEach((box) => box.addEventListener("change", () => {
        draft.checklist[Number(box.dataset.check)].done = box.checked;
        if (task) persist({ checklist: draft.checklist });
      }));
      form.querySelectorAll("[data-del-check]").forEach((b) => b.addEventListener("click", () => {
        syncView(); draft.checklist.splice(Number(b.dataset.delCheck), 1);
        if (task) persist({ checklist: draft.checklist }, true); else paint();
      }));
      form.querySelectorAll("[data-del-link]").forEach((b) => b.addEventListener("click", () => {
        syncView(); draft.links.splice(Number(b.dataset.delLink), 1);
        if (task) persist({ links: draft.links }, true); else paint();
      }));
      $("#check-add")?.addEventListener("click", () => {
        const text = $("#check-new").value.trim();
        if (!text) return;
        syncView(); draft.checklist.push({ text, done: false });
        if (task) persist({ checklist: draft.checklist }, true); else paint();
      });
      $("#link-add")?.addEventListener("click", () => {
        const url = $("#link-url").value.trim();
        if (!url) return;
        syncView();
        draft.links.push({ url, label: $("#link-label").value.trim() || url, kind: $("#link-kind").value });
        if (task) persist({ links: draft.links }, true); else paint();
      });
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (task) return;
        const errorEl = $("#drawer-error");
        errorEl.textContent = "";
        const body = collect();
        try {
          const fd = new FormData(form);
          await api("/api/tasks", json("POST", {
            ...body,
            business_id: fd.get("business_id") || null,
            approver_name: fd.get("approver_name") || "",
          }));
          closeDrawer();
          afterChange();
        } catch (error) {
          errorEl.textContent = error.message;
        }
      });
      bindComments();
    };

    const bindComments = () => {
      const form = $("#comment-form");
      if (!form) return;
      const area = form.content;
      const menu = $("#mention-menu");
      const updateMenu = () => {
        const upto = area.value.slice(0, area.selectionStart);
        const m = upto.match(/(?:^|\s)@([\w.-]*)$/);
        if (!m) { menu.classList.add("hidden"); return; }
        const q = m[1].toLowerCase();
        const hits = state.members.filter((u) => u.username.toLowerCase().includes(q) || u.display_name.toLowerCase().includes(q)).slice(0, 6);
        menu.innerHTML = hits.map((u) => `<button type="button" data-user="${esc(u.username)}">${esc(u.display_name)} <span class="muted">@${esc(u.username)}</span></button>`).join("");
        menu.classList.toggle("hidden", !hits.length);
      };
      area.addEventListener("input", updateMenu);
      menu.addEventListener("click", (e) => {
        const b = e.target.closest("[data-user]");
        if (!b) return;
        const pos = area.selectionStart;
        const before = area.value.slice(0, pos).replace(/@[\w.-]*$/, `@${b.dataset.user} `);
        area.value = before + area.value.slice(pos);
        area.focus();
        menu.classList.add("hidden");
      });
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const content = area.value.trim();
        if (!content) return;
        try {
          await api(`/api/tasks/${encodeURIComponent(task.id)}/comments`, json("POST", { content }));
          await openTask(task.id);
          afterChange();
        } catch (error) {
          toast(`Could not post comment: ${error.message}`);
        }
      });
    };

    paint();
    $("#task-drawer").classList.remove("hidden");
    document.body.classList.add("drawer-open");
    state.drawerTaskId = taskId || "";
  }

  function afterChange() {
    loadDepartments();
    refreshCounts();
    if (typeof refreshTasks === "function") refreshTasks().catch(() => {});
    if (typeof refreshBrief === "function") refreshBrief().catch(() => {});
    if (state.route?.view === "workspace") renderWorkspace().catch(() => {});
    if (typeof refreshNotifications === "function") refreshNotifications().catch(() => {});
  }

  // ── Notifications page ────────────────────────────────────
  async function renderNotifications() {
    const root = $("#notifications-root");
    if (!root) return;
    const data = await api(`/api/notifications?filter=${state.notifFilter}&limit=100`);
    const tabs = ["all", "unread", "read"].map((k) => `<button type="button" class="ws-chip${state.notifFilter === k ? " active" : ""}" data-nf="${k}">${k[0].toUpperCase() + k.slice(1)}</button>`).join("");
    root.innerHTML = `
      <div class="ws-head"><div><span class="eyebrow">Inbox</span><h2>Notifications <span class="count-badge">${data.unread_count} unread</span></h2></div>
        <button type="button" class="ghost" id="notif-read-all"${data.unread_count ? "" : " disabled"}>Mark all as read</button></div>
      <div class="ws-chips">${tabs}</div>
      <div class="notif-list">${data.items.map((n) => `
        <article class="notif-row${n.read_at ? "" : " unread"}" data-id="${esc(n.id)}">
          <button type="button" class="notif-main" data-open="${esc(n.task_id || "")}">
            <span class="notif-type">${esc(EVENT_LABEL[n.notification_type] || n.notification_type)}</span>
            <strong>${esc(n.title)}</strong>
            <span class="notif-meta">${n.department_name ? deptChip(n.department_id, n.department_name) : ""} <span class="muted">by ${esc(n.actor_name || "System")} · ${esc(formatTimestamp(n.created_at))}</span></span>
          </button>
          <button type="button" class="ghost mini" data-toggle="${n.read_at ? "unread" : "read"}">${n.read_at ? "Mark unread" : "Mark read"}</button>
        </article>`).join("") || `<p class="muted">Nothing here.</p>`}</div>`;
    root.querySelectorAll("[data-nf]").forEach((b) => b.addEventListener("click", () => { state.notifFilter = b.dataset.nf; renderNotifications().catch((e) => toast(e.message)); }));
    $("#notif-read-all")?.addEventListener("click", async () => {
      await api("/api/notifications/read-all", json("POST"));
      await renderNotifications();
      refreshNotifications();
    });
    root.querySelectorAll(".notif-row").forEach((row) => {
      row.querySelector("[data-toggle]").addEventListener("click", async (e) => {
        await api(`/api/notifications/${encodeURIComponent(row.dataset.id)}/${e.currentTarget.dataset.toggle}`, json("POST"));
        await renderNotifications();
        refreshNotifications();
      });
      row.querySelector("[data-open]").addEventListener("click", async (e) => {
        await api(`/api/notifications/${encodeURIComponent(row.dataset.id)}/read`, json("POST"));
        refreshNotifications();
        const id = e.currentTarget.dataset.open;
        if (id) await openTask(id);
        renderNotifications().catch(() => {});
      });
    });
  }

  // ── Reports ───────────────────────────────────────────────
  function renderReports() {
    const root = $("#reports-root");
    if (!root) return;
    if (!state.isSA) {
      root.innerHTML = `<p class="form-error">Super admin access required.</p>`;
      return;
    }
    const end = new Date();
    const start = new Date(Date.now() - 6 * 86400000);
    const iso = (d) => d.toISOString().slice(0, 10);
    root.innerHTML = `
      <div class="ws-head"><div><span class="eyebrow">${state.isSA ? "All departments" : "Your work"}</span><h2>Reports</h2></div></div>
      <form id="report-form" class="ws-filters">
        <label>From <input type="date" name="start" value="${iso(start)}" required /></label>
        <label>To <input type="date" name="end" value="${iso(end)}" required /></label>
        <select name="department_id" aria-label="Department"><option value="">All departments</option>${state.departments.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("")}</select>
        ${state.isSA ? `<select name="user_id" aria-label="Employee"><option value="">All employees</option>${state.members.map((m) => `<option value="${esc(m.id)}">${esc(m.display_name)}</option>`).join("")}</select>` : ""}
        <select name="status" aria-label="Status"><option value="">Any status</option>${Object.entries(STATUS_LABEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
        <select name="completion" aria-label="Completed vs pending"><option value="">Completed + pending</option><option value="completed">Completed</option><option value="pending">Pending</option></select>
        <button type="submit" class="primary-action">Run report</button>
      </form>
      <div id="report-out" class="report-out"><p class="muted">Loading…</p></div>`;
    const run = async () => {
      const fd = new FormData($("#report-form"));
      const p = new URLSearchParams();
      for (const [k, v] of fd.entries()) if (v && k !== "start" && k !== "end") p.set(k, v);
      p.set("start", new Date(`${fd.get("start")}T00:00:00`).toISOString());
      p.set("end", new Date(new Date(`${fd.get("end")}T00:00:00`).getTime() + 86400000).toISOString());
      const out = $("#report-out");
      try {
        const r = await api(`/api/reports/overview?${p}`);
        const t = r.totals;
        const cards = [["Tasks", t.tasks], ["Completed", t.completed], ["Pending", t.pending], ["Overdue", t.overdue], ["Unassigned", t.unassigned], ["Completion", `${t.completion_rate}%`], ["Time logged", formatDuration(t.seconds)]]
          .map(([l, v]) => `<div class="summary-item"><strong>${esc(v)}</strong><span>${l}</span></div>`).join("");
        const taskRows = (rows) => rows.map((x) => `<li><a href="#" data-open-task="${esc(x.id)}">${esc(x.title)}</a> ${deptChip(x.department_id, x.department_name)} <span class="muted">${esc(fmtDate(x.deadline))}</span></li>`).join("") || `<li class="muted">None</li>`;
        out.innerHTML = `
          <div class="work-summary">${cards}</div>
          <h3>By department</h3>
          <div class="table-wrap"><table class="ws-table"><thead><tr><th>Department</th><th>Tasks</th><th>Done</th><th>Pending</th><th>Overdue</th><th>Unassigned</th><th>Rate</th><th>Time</th></tr></thead><tbody>
            ${r.by_department.map((d) => `<tr><td>${deptChip(d.department_id, d.department_name)}</td><td>${d.total}</td><td>${d.completed}</td><td>${d.pending}</td><td>${d.overdue}</td><td>${d.unassigned}</td><td>${d.completion_rate}%</td><td>${esc(formatDuration(d.seconds))}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">No data</td></tr>`}
          </tbody></table></div>
          <h3>By employee</h3>
          <div class="table-wrap"><table class="ws-table"><thead><tr><th>Employee</th><th>Time</th><th>Open</th><th>Completed</th><th>Workload by department</th></tr></thead><tbody>
            ${r.by_employee.map((e) => `<tr><td>${esc(e.user_name)}</td><td>${esc(formatDuration(e.seconds))}</td><td>${e.open_tasks}</td><td>${e.completed_tasks}</td><td>${Object.entries(e.workload || {}).map(([k, v]) => `${esc(k)}: ${v}`).join(", ") || "—"}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">No data</td></tr>`}
          </tbody></table></div>
          <div class="report-lists"><div><h3>Overdue</h3><ul>${taskRows(r.overdue)}</ul></div><div><h3>Unassigned</h3><ul>${taskRows(r.unassigned)}</ul></div></div>`;
        out.querySelectorAll("[data-open-task]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); openTask(a.dataset.openTask); }));
      } catch (error) {
        out.innerHTML = `<p class="form-error">${esc(error.message)}</p>`;
      }
    };
    $("#report-form").addEventListener("submit", (e) => { e.preventDefault(); run(); });
    run();
  }

  // ── Department management (super admin) ───────────────────
  async function renderDeptMgmt() {
    const root = $("#deptmgmt-root");
    if (!root) return;
    if (!state.isSA) { root.innerHTML = `<p class="form-error">Super admin access required.</p>`; return; }
    const [depts, users] = await Promise.all([api("/api/departments?include_inactive=true"), api("/api/admin/users")]);
    const people = users.users.filter((u) => u.status === "active");
    root.innerHTML = `
      <div class="ws-head"><div><span class="eyebrow">Super admin</span><h2>Department Management</h2><p class="muted">Add, rename, recolor, deactivate departments and manage who belongs to them.</p></div></div>
      <form id="dept-create" class="dept-card dept-new">
        <input name="name" placeholder="New department name" maxlength="80" required />
        <input name="icon" placeholder="Icon" maxlength="8" class="icon-input" />
        <input type="color" name="color" value="#6366f1" aria-label="Color" />
        <input name="description" placeholder="Description" />
        <button type="submit" class="primary-action">Add department</button>
      </form>
      <p id="dept-error" class="form-error" role="alert"></p>
      <div class="dept-list">${depts.map((d) => `
        <section class="dept-card${d.status === "inactive" ? " inactive" : ""}" data-id="${esc(d.id)}">
          <div class="dept-row">
            <input type="color" data-f="color" value="${esc(d.color)}" aria-label="Color" />
            <input data-f="icon" value="${esc(d.icon || "")}" maxlength="8" class="icon-input" aria-label="Icon" />
            <input data-f="name" value="${esc(d.name)}" maxlength="80" aria-label="Name" />
            <input data-f="description" value="${esc(d.description || "")}" aria-label="Description" />
            <button type="button" class="ghost" data-save>Save</button>
            <button type="button" class="ghost" data-toggle-status="${d.status === "active" ? "inactive" : "active"}">${d.status === "active" ? "Deactivate" : "Activate"}</button>
          </div>
          <details><summary>Members (${d.member_count || 0})</summary>
            <div class="member-grid" data-members="${esc(d.id)}">Loading…</div>
          </details>
        </section>`).join("")}</div>`;
    const err = $("#dept-error");
    const guard = (fn) => async (...a) => { err.textContent = ""; try { await fn(...a); } catch (e) { err.textContent = e.message; } };
    $("#dept-create").addEventListener("submit", guard(async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      await api("/api/departments", json("POST", Object.fromEntries(fd.entries())));
      await loadDepartments();
      await renderDeptMgmt();
    }));
    root.querySelectorAll(".dept-card[data-id]").forEach((card) => {
      const id = card.dataset.id;
      card.querySelector("[data-save]").addEventListener("click", guard(async () => {
        const body = {};
        card.querySelectorAll("[data-f]").forEach((i) => { body[i.dataset.f] = i.value; });
        await api(`/api/departments/${encodeURIComponent(id)}`, json("PATCH", body));
        await loadDepartments();
        toast("Department saved.");
      }));
      card.querySelector("[data-toggle-status]").addEventListener("click", guard(async (e) => {
        await api(`/api/departments/${encodeURIComponent(id)}`, json("PATCH", { status: e.currentTarget.dataset.toggleStatus }));
        await loadDepartments();
        await renderDeptMgmt();
      }));
      const details = card.querySelector("details");
      const grid = card.querySelector("[data-members]");
      const paintMembers = async () => {
        const members = await api(`/api/departments/${encodeURIComponent(id)}/members`);
        const ids = new Set(members.map((m) => m.id));
        grid.innerHTML = people.map((u) => `<label><input type="checkbox" data-user="${esc(u.id)}" ${ids.has(u.id) ? "checked" : ""} /> ${esc(u.display_name || u.username)}</label>`).join("");
        grid.querySelectorAll("input").forEach((box) => box.addEventListener("change", guard(async () => {
          if (box.checked) await api(`/api/departments/${encodeURIComponent(id)}/members`, json("POST", { user_id: box.dataset.user }));
          else await api(`/api/departments/${encodeURIComponent(id)}/members/${encodeURIComponent(box.dataset.user)}`, { method: "DELETE" });
          await loadDepartments();
        })));
      };
      details.addEventListener("toggle", () => { if (details.open) guard(paintMembers)(); }, { once: true });
    });
  }

  window.TSLWorkspace = { onRoute, onSession, openTask };
})();
