(function () {
  const ROLE_TITLES = {
    super_admin: "Organization overview",
    admin: "Operations overview",
    operator: "My workday",
  };
  const STATE_LABEL = {
    checked_in: ["Checked in", "badge-success"],
    paused: ["Paused", ""],
    checked_out: ["Checked out", "badge-neutral"],
    not_checked_in: ["Not in", "badge-neutral"],
  };
  const STATUS_LABEL = {
    open: "Open",
    in_progress: "In progress",
    waiting_on: "Waiting",
    delegated: "Delegated",
    blocked: "Blocked",
    done: "Done",
    cancelled: "Cancelled",
  };

  const root = () => document.getElementById("dashboard-root");
  const text = (value) => esc(String(value ?? ""));
  const num = (value) => Number(value || 0);
  let generation = 0;

  const ICONS = {
    users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    grid: '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
    building: '<rect width="16" height="20" x="4" y="2" rx="2"/><path d="M9 22v-4h6v4"/><path d="M8 6h.01M16 6h.01M12 6h.01M12 10h.01M12 14h.01M16 10h.01M16 14h.01M8 10h.01M8 14h.01"/>',
    list: '<path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/>',
    check: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    alert: '<circle cx="12" cy="12" r="10"/><line x1="12" x2="12" y1="8" y2="12"/><line x1="12" x2="12.01" y1="16" y2="16"/>',
    shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
    clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    cal: '<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/>',
    pause: '<circle cx="12" cy="12" r="10"/><line x1="10" x2="10" y1="15" y2="9"/><line x1="14" x2="14" y1="15" y2="9"/>',
  };
  const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;

  function kpi({ label, value, sub = "", tone = "", icon: iconName = "", href = "" }) {
    const body = `<span class="kpi-head"><span class="kpi-label">${text(label)}</span>${iconName ? `<span class="kpi-icon">${icon(iconName)}</span>` : ""}</span>
      <strong class="kpi-value">${text(value)}</strong>${sub ? `<span class="kpi-sub">${text(sub)}</span>` : ""}`;
    return href
      ? `<a class="kpi ${tone}" href="${text(href)}">${body}</a>`
      : `<div class="kpi ${tone}">${body}</div>`;
  }

  function panel(id, title, { subtitle = "", action = "", wide = false } = {}) {
    return `<section class="dash-panel${wide ? " dash-wide" : ""}" id="dash-${id}">
      <header class="dash-panel-head"><div><h3>${text(title)}</h3>${subtitle ? `<p>${text(subtitle)}</p>` : ""}</div>${action}</header>
      <div class="dash-panel-body"><div class="dash-skeleton"><span></span><span></span><span></span></div></div>
    </section>`;
  }

  const empty = (message) => `<p class="dash-empty">${text(message)}</p>`;

  async function fill(id, loader) {
    const mine = generation;
    const target = document.querySelector(`#dash-${id} .dash-panel-body`);
    if (!target) return;
    try {
      const html = await loader();
      if (mine === generation) target.innerHTML = html;
    } catch (error) {
      if (mine !== generation) return;
      target.innerHTML = `<div class="dash-error"><span>${text(error.message || "Could not load this section.")}</span><button type="button" class="ghost" data-retry="${id}">Retry</button></div>`;
      target.querySelector("[data-retry]")?.addEventListener("click", () => {
        target.innerHTML = '<div class="dash-skeleton"><span></span><span></span><span></span></div>';
        fill(id, loader);
      });
    }
  }

  const dueLabel = (deadline) => {
    if (!deadline) return "No deadline";
    const day = String(deadline).slice(0, 10);
    const today = new Date();
    const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    if (day === iso) return "Today";
    return new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };
  const isOverdue = (task) => {
    if (!task.deadline || ["done", "cancelled"].includes(task.status)) return false;
    const today = new Date();
    const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    return String(task.deadline).slice(0, 10) < iso;
  };

  function taskTable(tasks, emptyMessage) {
    if (!tasks.length) return empty(emptyMessage);
    return `<div class="table-wrap"><table class="ws-table dash-table">
      <thead><tr><th>Task</th><th>Brand</th><th>Status</th><th>Due</th></tr></thead>
      <tbody>${tasks.map((task) => `<tr>
        <td><a class="dash-link" href="#tasks/all" data-task="${text(task.id)}">${text(task.title)}</a>${task.priority === "P0" ? '<span class="badge badge-danger dash-chip">P0</span>' : ""}</td>
        <td>${text(task.business_name || "—")}</td>
        <td><span class="badge badge-neutral">${text(STATUS_LABEL[task.status] || task.status)}</span></td>
        <td class="${isOverdue(task) ? "dash-due-over" : ""}">${text(dueLabel(task.deadline))}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  function stackedBar(segments) {
    const total = segments.reduce((sum, item) => sum + item.value, 0);
    if (!total) return empty("No tasks yet.");
    return `<div class="dash-bar" role="img" aria-label="${segments.map((s) => `${s.label} ${s.value}`).join(", ")}">${segments
      .filter((item) => item.value)
      .map((item) => `<span class="${item.tone}" style="flex:${item.value}"></span>`)
      .join("")}</div>
      <ul class="dash-legend">${segments.map((item) => `<li><i class="${item.tone}"></i>${text(item.label)}<b>${item.value}</b></li>`).join("")}</ul>`;
  }

  function workloadTable(rows) {
    if (!rows.length) return empty("No brand work to show yet.");
    return `<div class="table-wrap"><table class="ws-table dash-table">
      <thead><tr><th>Brand</th><th>Pending</th><th>In progress</th><th>Done</th><th>Overdue</th></tr></thead>
      <tbody>${rows.slice(0, 6).map((row) => `<tr>
        <td><a class="dash-link" href="#brand/${text(row.id)}">${text(row.name)}</a></td>
        <td>${num(row.pending_tasks)}</td><td>${num(row.in_progress_tasks)}</td><td>${num(row.completed_tasks)}</td>
        <td class="${num(row.overdue_tasks) ? "dash-due-over" : ""}">${num(row.overdue_tasks)}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  function departmentProgress(departments) {
    const rows = departments.filter((d) => d.status !== "inactive");
    if (!rows.length) return empty("No departments available.");
    const maxOpen = Math.max(1, ...rows.map((d) => num(d.open_count)));
    return `<ul class="dash-rows">${rows.map((d) => `<li>
      <a class="dash-link" href="#dept/${text(d.id)}">${text(d.name)}</a>
      <span class="dash-meter" aria-hidden="true"><i style="width:${Math.round((num(d.open_count) / maxOpen) * 100)}%"></i></span>
      <span class="dash-meta">${num(d.open_count)} open${num(d.overdue_count) ? ` · <b class="dash-due-over">${num(d.overdue_count)} overdue</b>` : ""}</span>
    </li>`).join("")}</ul>`;
  }

  function approvalsList(items) {
    if (!items.length) return empty("Nothing is waiting for approval.");
    return `<ul class="dash-rows">${items.slice(0, 6).map((item) => `<li>
      <span class="dash-row-title">${text(item.summary)}</span>
      <span class="dash-meta">${text(String(item.action_type || "").replace(/_/g, " "))}</span>
    </li>`).join("")}</ul><a class="text-link dash-more" href="#approvals">Review all approvals</a>`;
  }

  function activityList(items) {
    if (!items.length) return empty("No recent activity yet.");
    return `<ul class="dash-rows dash-activity">${items.slice(0, 7).map((item) => `<li>
      <span class="dash-row-title">${text(item.title)}</span>
      <span class="dash-meta">${text(item.business_name)} · ${text(STATUS_LABEL[item.status] || item.status)}</span>
    </li>`).join("")}</ul>`;
  }

  function notificationList(payload) {
    const items = payload.items || [];
    if (!items.length) return empty("You're all caught up.");
    return `<ul class="dash-rows">${items.slice(0, 5).map((item) => `<li>
      <span class="dash-row-title">${item.read_at ? "" : '<i class="dash-dot" aria-label="Unread"></i>'}${text(item.title)}</span>
      <span class="dash-meta">${text(item.body)}</span>
    </li>`).join("")}</ul><a class="text-link dash-more" href="#notifications">Open notification center</a>`;
  }

  function attendanceToday(data) {
    const rows = data.employees || [];
    const summary = `<div class="dash-summary">
      <div><b>${data.present}</b><span>Present</span></div>
      <div><b>${data.counts.checked_in}</b><span>Active now</span></div>
      <div><b>${data.late}</b><span>Late arrivals</span></div>
      <div><b>${Math.max(0, data.total - data.present)}</b><span>Not in</span></div>
    </div>`;
    if (!data.scheduled_today) {
      return `${summary}${empty("Today is not a scheduled working day.")}`;
    }
    const list = rows.slice(0, 8).map((row) => {
      const [label, tone] = STATE_LABEL[row.state] || STATE_LABEL.not_checked_in;
      return `<li><span class="dash-row-title">${text(row.name)}</span><span class="dash-meta">${row.late ? '<span class="badge dash-chip">Late</span>' : ""}<span class="badge ${tone}">${label}</span></span></li>`;
    }).join("");
    return `${summary}<ul class="dash-rows">${list}</ul><a class="text-link dash-more" href="#attendance">Open attendance reports</a>`;
  }

  async function integrationHealth(isSuper) {
    const checks = [
      api("/api/health").then((h) => ({ name: "AI assistant", ok: Boolean(h.ai_configured), detail: h.ai_configured ? h.model : "Not configured" })),
      api("/api/slack/status").then((s) => ({ name: "Slack", ok: Boolean(s.connected), detail: s.connected ? s.team_name || "Connected" : s.configured ? "Not connected" : "Not configured" })),
    ];
    if (isSuper) {
      checks.push(api("/api/google/status").then((g) => ({ name: "Google Workspace", ok: Boolean(g.connected), detail: g.connected ? "Connected" : "Not connected" })));
    }
    const results = await Promise.allSettled(checks);
    const names = ["AI assistant", "Slack", "Google Workspace"];
    return `<ul class="dash-rows">${results.map((result, index) => {
      const row = result.status === "fulfilled" ? result.value : { name: names[index], ok: false, detail: "Status unavailable" };
      return `<li><span class="dash-row-title">${text(row.name)}</span><span class="dash-meta">${text(row.detail)} <span class="badge ${row.ok ? "badge-success" : "badge-neutral"}">${row.ok ? "Healthy" : "Attention"}</span></span></li>`;
    }).join("")}</ul><a class="text-link dash-more" href="#integrations">Manage integrations</a>`;
  }

  function myAttendanceCard() {
    return panel("my-attendance", "Office attendance", { subtitle: "Separate from task time logs" });
  }

  async function myAttendanceHtml() {
    const status = await api("/api/attendance/status");
    const session = status.session;
    const active = session && session.status !== "checked_out";
    const label = !session ? "Not checked in" : session.status === "checked_in" ? "Checked in" : session.status === "paused" ? "Paused" : "Checked out";
    const tone = !session ? "badge-neutral" : session.status === "checked_in" ? "badge-success" : session.status === "paused" ? "" : "badge-neutral";
    return `<div class="dash-attendance">
      <div><span class="badge ${tone}">${label}</span>
        <p class="dash-attendance-time">${text(formatDuration(status.today_confirmed_seconds))}<small>confirmed today</small></p></div>
      <button type="button" class="${active ? "ghost" : "primary-action"}" id="dash-attendance-action">${active ? "Check out" : "Check in"}</button>
    </div>
    <p class="dash-error-inline" id="dash-attendance-error" role="alert"></p>`;
  }

  function wireAttendanceAction() {
    const button = document.getElementById("dash-attendance-action");
    if (!button || button.dataset.bound) return;
    button.dataset.bound = "1";
    button.addEventListener("click", async () => {
      const checkingOut = button.textContent.trim() === "Check out";
      button.disabled = true;
      try {
        await api(checkingOut ? "/api/attendance/check-out" : "/api/attendance/check-in", { method: "POST" });
        await window.TSLAttendance?.sync?.();
        await fill("my-attendance", async () => {
          const html = await myAttendanceHtml();
          queueMicrotask(wireAttendanceAction);
          return html;
        });
      } catch (error) {
        button.disabled = false;
        const message = document.getElementById("dash-attendance-error");
        if (message) message.textContent = error.message;
      }
    });
  }

  function quickActions(role) {
    const actions = [];
    if (role !== "operator") actions.push(["Create task", "#quick-add-task", ""]);
    if (role === "super_admin") {
      actions.push(["Add brand", "#quick-add-brand", ""]);
      actions.push(["Manage departments", "", "#deptmgmt"]);
      actions.push(["Review approvals", "", "#approvals"]);
      actions.push(["Administration", "", "/admin"]);
    } else if (role === "admin") {
      actions.push(["Review approvals", "", "#approvals"]);
      actions.push(["Schedule event", "#quick-schedule-event", ""]);
    } else {
      actions.push(["My tasks", "", "#tasks/mine"]);
      actions.push(["Time logs", "", "#timelog"]);
    }
    return `<div class="dash-actions">${actions.map(([label, trigger, href]) => href
      ? `<a class="ghost dash-action" href="${href}">${text(label)}</a>`
      : `<button type="button" class="ghost dash-action" data-trigger="${trigger}">${text(label)}</button>`).join("")}</div>`;
  }

  function greeting(name) {
    const hour = new Date().getHours();
    const part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    return `${part}${name ? `, ${name.split(" ")[0]}` : ""}`;
  }

  function shell(role, me) {
    const name = me?.display_name || me?.username || "";
    const date = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
    return `<div class="dash-head"><div><span class="eyebrow">${text(ROLE_TITLES[role] || "Overview")}</span><h2>${text(greeting(name))}</h2><p>${text(date)}</p></div>${quickActions(role)}</div>
      <div class="kpi-grid" id="dash-kpis">${Array.from({ length: role === "operator" ? 5 : 8 }, () => '<div class="kpi kpi-loading"><span></span><span></span></div>').join("")}</div>`;
  }

  async function renderSuper() {
    const body = root();
    body.insertAdjacentHTML(
      "beforeend",
      `<div class="dash-grid">
        ${panel("tasks", "Task status", { subtitle: "Organization-wide" })}
        ${panel("attendance", "Today's attendance", { subtitle: "Server-confirmed" })}
        ${panel("workload", "Brand workload", { wide: true, action: '<a class="text-link" href="#brands">All brands</a>' })}
        ${panel("approvals", "Pending approvals")}
        ${panel("integrations", "Integration health")}
        ${panel("activity", "Recent activity")}
      </div>`,
    );
    const [brief, overview, departments, today] = await Promise.allSettled([
      api("/api/brief"),
      api("/api/admin/overview"),
      api("/api/departments"),
      api("/api/attendance/today"),
    ]);
    const s = brief.status === "fulfilled" ? brief.value.summary : {};
    const stats = overview.status === "fulfilled" ? overview.value.stats : {};
    const att = today.status === "fulfilled" ? today.value : null;
    document.getElementById("dash-kpis").innerHTML = [
      kpi({ label: "Employees", value: stats.users_active ?? "—", sub: `${num(stats.users_total)} total`, icon: "users", href: "/admin" }),
      kpi({ label: "Brand boards", value: stats.businesses ?? "—", icon: "grid", href: "#brands" }),
      kpi({ label: "Departments", value: departments.status === "fulfilled" ? departments.value.length : "—", icon: "building", href: "#deptmgmt" }),
      kpi({ label: "Open tasks", value: num(s.open_tasks), sub: `${num(s.due_today)} due today`, icon: "list", href: "#tasks/all" }),
      kpi({ label: "Completed", value: num(s.completed_tasks), icon: "check" }),
      kpi({ label: "Overdue", value: num(s.overdue), tone: num(s.overdue) ? "kpi-danger" : "", icon: "alert", href: "#tasks/overdue" }),
      kpi({ label: "Pending approvals", value: num(s.pending_approvals), tone: num(s.pending_approvals) ? "kpi-warn" : "", icon: "shield", href: "#approvals" }),
      kpi({ label: "In office today", value: att ? `${att.present}/${att.total}` : "—", sub: att ? `${att.late} late` : "", icon: "clock", href: "#attendance" }),
    ].join("");
    fill("tasks", async () => {
      if (brief.status !== "fulfilled") throw brief.reason;
      return stackedBar([
        { label: "Pending", value: num(s.pending_tasks), tone: "seg-neutral" },
        { label: "In progress", value: num(s.in_progress_tasks), tone: "seg-accent" },
        { label: "Completed", value: num(s.completed_tasks), tone: "seg-success" },
        { label: "Overdue", value: num(s.overdue), tone: "seg-danger" },
      ]);
    });
    fill("attendance", async () => {
      if (!att) throw today.reason;
      return attendanceToday(att);
    });
    fill("workload", async () => {
      if (brief.status !== "fulfilled") throw brief.reason;
      return workloadTable(brief.value.brand_workload || []);
    });
    fill("approvals", async () => {
      if (brief.status !== "fulfilled") throw brief.reason;
      return approvalsList(brief.value.pending_approvals || []);
    });
    fill("integrations", () => integrationHealth(true));
    fill("activity", async () => activityList(await api("/api/activity")));
  }

  async function renderAdmin() {
    const body = root();
    body.insertAdjacentHTML(
      "beforeend",
      `<div class="dash-grid">
        ${panel("attention", "Tasks requiring attention", { wide: true, subtitle: "Overdue and due today" })}
        ${myAttendanceCard()}
        ${panel("departments", "Department progress")}
        ${panel("workload", "Brand workload")}
        ${panel("approvals", "Pending approvals")}
        ${panel("integrations", "Connections")}
        ${panel("activity", "Recent task activity")}
      </div>`,
    );
    const [brief, departments] = await Promise.allSettled([api("/api/brief"), api("/api/departments")]);
    const b = brief.status === "fulfilled" ? brief.value : { summary: {} };
    const s = b.summary;
    document.getElementById("dash-kpis").innerHTML = [
      kpi({ label: "Needs attention", value: num(s.overdue) + num(s.due_today), sub: "overdue + due today", tone: num(s.overdue) ? "kpi-danger" : "", icon: "alert", href: "#tasks/all" }),
      kpi({ label: "Open tasks", value: num(s.open_tasks), icon: "list", href: "#tasks/all" }),
      kpi({ label: "Overdue", value: num(s.overdue), tone: num(s.overdue) ? "kpi-danger" : "", icon: "alert", href: "#tasks/overdue" }),
      kpi({ label: "Due in 3 days", value: num(s.due_soon), icon: "cal" }),
      kpi({ label: "Pending approvals", value: num(s.pending_approvals), tone: num(s.pending_approvals) ? "kpi-warn" : "", icon: "shield", href: "#approvals" }),
      kpi({ label: "Waiting on others", value: num(s.waiting_on), icon: "pause" }),
    ].join("");
    fill("attention", async () => {
      if (brief.status !== "fulfilled") throw brief.reason;
      const seen = new Set();
      const tasks = [...(b.overdue || []), ...(b.due_today || [])].filter((t) => !seen.has(t.id) && seen.add(t.id));
      return taskTable(tasks.slice(0, 8), "Nothing is overdue or due today.");
    });
    fill("my-attendance", async () => {
      const html = await myAttendanceHtml();
      queueMicrotask(wireAttendanceAction);
      return html;
    });
    fill("departments", async () => {
      if (departments.status !== "fulfilled") throw departments.reason;
      return departmentProgress(departments.value);
    });
    fill("workload", async () => {
      if (brief.status !== "fulfilled") throw brief.reason;
      return workloadTable(b.brand_workload || []);
    });
    fill("approvals", async () => approvalsList(b.pending_approvals || []));
    fill("integrations", () => integrationHealth(false));
    fill("activity", async () => activityList(await api("/api/activity")));
  }

  async function renderOperator() {
    const body = root();
    body.insertAdjacentHTML(
      "beforeend",
      `<div class="dash-grid">
        ${myAttendanceCard()}
        ${panel("mine", "My tasks", { wide: true, subtitle: "Soonest deadline first", action: '<a class="text-link" href="#tasks/mine">View all</a>' })}
        ${panel("notifications", "Recent notifications")}
        ${panel("activity", "Recent activity")}
      </div>`,
    );
    const [tasksResult, statusResult] = await Promise.allSettled([
      api("/api/tasks?view=mine&limit=200"),
      api("/api/attendance/status"),
    ]);
    const tasks = tasksResult.status === "fulfilled" ? tasksResult.value : [];
    const open = tasks.filter((t) => !["done", "cancelled"].includes(t.status));
    const today = new Date();
    const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    const dueToday = open.filter((t) => String(t.deadline || "").slice(0, 10) === iso);
    const overdue = open.filter(isOverdue);
    const blocked = open.filter((t) => ["waiting_on", "blocked"].includes(t.status));
    document.getElementById("dash-kpis").innerHTML = [
      kpi({ label: "My open tasks", value: open.length, icon: "list", href: "#tasks/mine" }),
      kpi({ label: "Due today", value: dueToday.length, icon: "cal" }),
      kpi({ label: "Overdue", value: overdue.length, tone: overdue.length ? "kpi-danger" : "", icon: "alert" }),
      kpi({ label: "Waiting or blocked", value: blocked.length, icon: "pause" }),
      kpi({ label: "Office time today", value: statusResult.status === "fulfilled" ? formatDuration(statusResult.value.today_confirmed_seconds) : "—", icon: "clock", href: "#attendance" }),
    ].join("");
    fill("my-attendance", async () => {
      const html = await myAttendanceHtml();
      queueMicrotask(wireAttendanceAction);
      return html;
    });
    fill("mine", async () => {
      if (tasksResult.status !== "fulfilled") throw tasksResult.reason;
      const sorted = [...open].sort((a, b) => String(a.deadline || "9999").localeCompare(String(b.deadline || "9999")));
      return taskTable(sorted.slice(0, 8), "You have no open tasks assigned.");
    });
    fill("notifications", async () => notificationList(await api("/api/notifications?limit=5")));
    fill("activity", async () => activityList(await api("/api/activity")));
  }

  async function render() {
    const target = root();
    if (!target || !currentUserRole) return;
    generation += 1;
    const role = currentUserRole;
    let me = null;
    try {
      me = (await api("/api/auth/me")).user;
    } catch {
      return;
    }
    target.innerHTML = shell(role, me);
    target.querySelectorAll("[data-trigger]").forEach((button) => {
      button.addEventListener("click", () => document.querySelector(button.dataset.trigger)?.click());
    });
    try {
      if (role === "super_admin") await renderSuper();
      else if (role === "admin") await renderAdmin();
      else await renderOperator();
    } catch (error) {
      target.insertAdjacentHTML("beforeend", `<div class="dash-error">${text(error.message)}</div>`);
    }
  }

  window.TSLDashboard = {
    onRoute() {
      render();
    },
  };
})();
