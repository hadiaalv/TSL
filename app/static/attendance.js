(function () {
  const root = () => document.querySelector("#attendance-root");
  const state = {
    started: false,
    busy: false,
    session: null,
    policy: null,
    report: null,
    members: [],
    departments: [],
    todaySeconds: 0,
    month: "",
    userId: "",
    departmentId: "",
    error: "",
    heartbeatTimer: null,
  };

  const isSuperAdmin = () => currentUserRole === "super_admin";
  const canViewTeam = () => isSuperAdmin();
  const monthNow = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  };
  const statusLabel = (session) => {
    if (!session) return "Not Checked In";
    if (session.status === "checked_in") return "Checked In";
    if (session.status === "paused") return "Paused";
    return "Checked Out";
  };
  const dateTime = (value) => {
    if (!value) return "—";
    const zone = state.policy?.timezone || "Asia/Karachi";
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: zone,
    }).format(new Date(value));
  };
  const dateLabel = (value) => {
    if (!value) return "—";
    const zone = state.policy?.timezone || "Asia/Karachi";
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeZone: zone,
    }).format(new Date(`${value}T12:00:00Z`));
  };
  const hoursText = (seconds) => formatDuration(Number(seconds || 0));
  const emptyRow = (message) => `<p class="muted attendance-empty">${esc(message)}</p>`;

  async function getStatus() {
    const response = await api("/api/attendance/status");
    state.session = response.session;
    state.policy = response.policy;
    state.todaySeconds = response.today_confirmed_seconds || 0;
    return response;
  }

  async function sendHeartbeat() {
    if (!state.session || state.busy || document.visibilityState === "hidden") return;
    try {
      state.session = await api("/api/attendance/heartbeat", {
        method: "POST",
        body: JSON.stringify({ request_id: crypto.randomUUID() }),
      });
      state.error = "";
      if (location.hash === "#attendance") render();
    } catch (error) {
      state.error = error.message;
      if (location.hash === "#attendance") render();
    }
  }

  function startHeartbeatLoop() {
    if (state.heartbeatTimer) window.clearInterval(state.heartbeatTimer);
    if (!state.session || state.session.status === "checked_out") {
      state.heartbeatTimer = null;
      return;
    }
    state.heartbeatTimer = window.setInterval(
      sendHeartbeat,
      (state.policy?.heartbeat_interval_seconds || 45) * 1000,
    );
  }

  async function loadReport() {
    const query = new URLSearchParams({ month: state.month || monthNow() });
    if (canViewTeam()) {
      query.set("user_id", state.userId || "all");
      if (state.departmentId) query.set("department_id", state.departmentId);
    }
    state.report = await api(`/api/attendance/report?${query}`);
  }

  async function loadOptions() {
    if (!canViewTeam()) return;
    const [members, departments] = await Promise.all([
      api("/api/team/members"),
      api("/api/departments"),
    ]);
    state.members = members;
    state.departments = departments;
  }

  function policyMarkup() {
    if (!isSuperAdmin() || !state.policy) return "";
    const days = [
      ["0", "Mon"],
      ["1", "Tue"],
      ["2", "Wed"],
      ["3", "Thu"],
      ["4", "Fri"],
      ["5", "Sat"],
      ["6", "Sun"],
    ];
    return `<details class="attendance-policy">
      <summary>Attendance policy</summary>
      <form id="attendance-policy-form" class="attendance-policy-grid">
        <label>Organization timezone<input name="timezone" value="${esc(state.policy.timezone)}" required /></label>
        <label>Heartbeat interval (seconds)<input name="heartbeat_interval_seconds" type="number" min="15" max="300" value="${state.policy.heartbeat_interval_seconds}" required /></label>
        <label>Inactivity timeout (seconds)<input name="inactivity_timeout_seconds" type="number" min="16" max="900" value="${state.policy.inactivity_timeout_seconds}" required /></label>
        <label>Scheduled start<input name="work_start" type="time" value="${esc(state.policy.work_start)}" required /></label>
        <label>Scheduled end<input name="work_end" type="time" value="${esc(state.policy.work_end)}" required /></label>
        <label>Late grace (minutes)<input name="late_grace_minutes" type="number" min="0" max="240" value="${state.policy.late_grace_minutes}" required /></label>
        <label>Overtime grace (minutes)<input name="overtime_grace_minutes" type="number" min="0" max="480" value="${state.policy.overtime_grace_minutes}" required /></label>
        <label>Allowed half leaves / month<input name="allowed_half_leaves" type="number" min="0" max="31" value="${state.policy.allowed_half_leaves}" required /></label>
        <label>Allowed days off / month<input name="allowed_days_off" type="number" min="0" max="31" value="${state.policy.allowed_days_off}" required /></label>
        <label>Allowed late check-ins / month<input name="allowed_late_checkins" type="number" min="0" max="31" value="${state.policy.allowed_late_checkins}" required /></label>
        <label>Allowed early offs / month<input name="allowed_early_offs" type="number" min="0" max="31" value="${state.policy.allowed_early_offs}" required /></label>
        <label class="attendance-day"><input name="overtime_enabled" type="checkbox"${state.policy.overtime_enabled ? " checked" : ""} />Enable overtime calculation</label>
        <fieldset><legend>Working days</legend>${days.map(([value, name]) => `
          <label class="attendance-day"><input type="checkbox" name="working_days" value="${value}"${state.policy.working_days.includes(Number(value)) ? " checked" : ""} />${name}</label>
        `).join("")}</fieldset>
        <button class="primary-action" type="submit">Save policy</button>
      </form>
    </details>`;
  }

  function currentCard() {
    const session = state.session;
    const status = statusLabel(session);
    const isActive = session && session.status !== "checked_out";
    const events = (session?.events || []).filter((event) => (
      ["interruption", "resumed", "missed_check_out"].includes(event.event_type)
    ));
    return `<section class="attendance-card">
      <div class="attendance-card-heading">
        <div><span class="eyebrow">Office Attendance</span><h2>Today’s attendance</h2></div>
        <span class="attendance-state state-${esc(session?.status || "not-checked-in")}">${esc(status)}</span>
      </div>
      <div class="attendance-metrics">
        <div><span>Check-in</span><strong>${esc(dateTime(session?.checked_in_at))}</strong></div>
        <div><span>Session confirmed</span><strong>${esc(hoursText(session?.confirmed_seconds))}</strong></div>
        <div><span>Confirmed today</span><strong>${esc(hoursText(state.todaySeconds))}</strong></div>
        <div><span>Check-out</span><strong>${esc(dateTime(session?.checked_out_at))}</strong></div>
      </div>
      <div class="attendance-actions">
        ${isActive
          ? `<button id="attendance-check-out" class="primary-action" type="button"${state.busy ? " disabled" : ""}>Check Out</button>`
          : `<button id="attendance-check-in" class="primary-action" type="button"${state.busy ? " disabled" : ""}>Check In</button>`}
        <span class="muted">Server time · ${esc(state.policy?.timezone || "Asia/Karachi")}</span>
      </div>
      ${state.error ? `<p class="form-error" role="alert">${esc(state.error)}</p>` : ""}
      <p class="attendance-limit">${esc(state.trackingLimit || "Browser heartbeat tracking can detect gaps, but cannot prove computer sleep or physical office presence.")}</p>
      <section class="attendance-events"><h3>Pause and resume history</h3>
        ${events.length ? `<ol>${events.map((event) => `<li>
          <strong>${esc(event.event_type === "interruption" ? "Tracking interrupted" : event.event_type === "resumed" ? "Tracking resumed" : "Missed check-out")}</strong>
          <time>${esc(dateTime(event.occurred_at))}</time>
          <span>${esc(event.reason || "Unknown tracking gap")}${event.review_status !== "informational" ? ` · ${esc(event.review_status === "resolved" ? "Reviewed" : "Needs review")}` : ""}</span>
        </li>`).join("")}</ol>` : emptyRow("No interruptions recorded for this active session.")}</section>
    </section>`;
  }

  function reportControls() {
    if (!canViewTeam()) {
      return `<label>Reporting month<input id="attendance-month" type="month" value="${esc(state.month)}" /></label>`;
    }
    return `<label>Reporting month<input id="attendance-month" type="month" value="${esc(state.month)}" /></label>
      <label>Employee<select id="attendance-user">
        <option value="all"${state.userId === "all" || !state.userId ? " selected" : ""}>All authorized employees</option>
        ${state.members.map((member) => `<option value="${esc(member.id)}"${state.userId === member.id ? " selected" : ""}>${esc(member.display_name)}</option>`).join("")}
      </select></label>
      <label>Department<select id="attendance-department">
        <option value="">All authorized departments</option>
        ${state.departments.map((dept) => `<option value="${esc(dept.id)}"${state.departmentId === dept.id ? " selected" : ""}>${esc(dept.name)}</option>`).join("")}
      </select></label>`;
  }

  function correctionControl(employee, day) {
    if (!isSuperAdmin() || !day.session_ids?.length) return "";
    return `<button type="button" class="attendance-correct" data-session="${esc(day.session_ids[0])}">Correct</button>`;
  }

  function exceptionControls(day) {
    if (!isSuperAdmin()) return "";
    return day.exceptions.map((event) => event.review_status === "needs_review"
      ? `<button type="button" class="attendance-resolve" data-event="${esc(event.id)}">Resolve</button>`
      : "").join(" ");
  }

  const ALLOWANCE_LABELS = [
    ["half_leaves", "Half leaves"],
    ["days_off", "Days off"],
    ["late_checkins", "Late check-ins"],
    ["early_offs", "Early offs"],
  ];

  function allowanceMarkup(employee) {
    const rows = ALLOWANCE_LABELS.map(([key, label]) => {
      const item = employee.allowances?.[key] || { allowed: 0, taken: 0, remaining: 0, exceeded: false };
      return `<tr${item.exceeded ? ` class="attendance-over"` : ""}><td>${label}</td><td>${item.allowed}</td><td>${item.taken}</td><td>${item.exceeded ? "Exceeded" : item.remaining}</td></tr>`;
    }).join("");
    const leaves = (employee.leaves || []).map((leave) => `<li>${esc(dateLabel(leave.leave_date))} · ${leave.leave_type === "half" ? "Half leave" : "Day off"}${leave.reason ? ` · ${esc(leave.reason)}` : ""}${isSuperAdmin() ? ` <button type="button" class="ghost attendance-leave-delete" data-leave="${esc(leave.id)}">Remove</button>` : ""}</li>`).join("");
    return `<div class="table-wrap"><table class="ws-table attendance-allowances">
      <thead><tr><th>Monthly allowance</th><th>Allowed</th><th>Taken</th><th>Remaining</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      ${leaves ? `<ul class="attendance-leave-list">${leaves}</ul>` : ""}
      ${isSuperAdmin() ? `<button type="button" class="ghost attendance-leave-add" data-user="${esc(employee.user_id)}">Record leave</button>` : ""}`;
  }

  function reportMarkup() {
    const report = state.report;
    const employees = report?.employees || [];
    if (!employees.length) return emptyRow("No attendance records are available for this month and scope.");
    return employees.map((employee) => `<section class="attendance-employee">
      <div class="attendance-employee-heading">
        <div><h3>${esc(employee.employee_name)}</h3><span class="muted">${esc(employee.username)} · ${esc(employee.month)}</span></div>
        <span><button type="button" class="ghost attendance-export">Export CSV</button>${isSuperAdmin() ? ` <button type="button" class="ghost attendance-print">Print</button>` : ""}</span>
      </div>
      <div class="attendance-summary-grid">
        <div><strong>${employee.days_present}</strong><span>Present</span></div>
        <div><strong>${employee.days_absent}</strong><span>Absent days</span></div>
        <div><strong>${employee.scheduled_working_days}</strong><span>Scheduled days</span></div>
        <div><strong>${employee.late_arrivals}</strong><span>Late arrivals</span></div>
        <div><strong>${employee.early_departures}</strong><span>Early departures</span></div>
        <div><strong>${esc(hoursText(employee.confirmed_seconds))}</strong><span>Confirmed office time</span></div>
        <div><strong>${esc(hoursText(employee.average_seconds_per_present_day))}</strong><span>Average / present day</span></div>
        <div><strong>${employee.missed_check_out_days}</strong><span>Missed check-outs</span></div>
        <div><strong>${esc(hoursText(employee.overtime_seconds))}</strong><span>Overtime</span></div>
      </div>
      ${allowanceMarkup(employee)}
      <p class="muted attendance-data-note">Holiday and approved-break records are unavailable; overtime is calculated only when enabled in policy.</p>
      <div class="table-wrap"><table class="ws-table attendance-table">
        <thead><tr><th>Date</th><th>Check-in</th><th>Check-out</th><th>Confirmed</th><th>Exceptions</th><th>Review</th></tr></thead>
        <tbody>${employee.daily.length ? employee.daily.map((day) => `<tr>
          <td>${esc(dateLabel(day.date))}</td>
          <td>${esc((day.check_ins || []).map(dateTime).join(", ") || "—")}${day.late_arrival ? `<span class="attendance-flag">Late</span>` : ""}</td>
          <td>${esc((day.check_outs || []).map(dateTime).join(", ") || "—")}${day.early_departure ? `<span class="attendance-flag">Early</span>` : ""}</td>
          <td>${esc(hoursText(day.confirmed_seconds))}</td>
          <td>${day.exceptions.length ? day.exceptions.map((item) => `<span class="attendance-flag">${esc(item.event_type === "interruption" ? "Tracking gap" : "Missed check-out")}</span>`).join(" ") : "—"}</td>
          <td>${correctionControl(employee, day)} ${exceptionControls(day)}</td>
        </tr>`).join("") : `<tr><td colspan="6">No daily attendance recorded.</td></tr>`}</tbody>
      </table></div>
    </section>`).join("");
  }

  function render() {
    const target = root();
    if (!target) return;
    target.innerHTML = `<div class="ws-head attendance-head">
      <div><span class="eyebrow">Separate from task time logs</span><h2>Office Attendance</h2>
        <p class="muted">Check in when you arrive. Only server-confirmed active intervals are included.</p></div>
      ${policyMarkup()}
    </div>
    ${currentCard()}
    <section class="attendance-report">
      <div class="attendance-report-head"><div><h2>Monthly attendance</h2><p class="muted">Calculated from persisted sessions, active intervals, and authorized corrections.</p></div>
        <div class="attendance-filters">${reportControls()}${isSuperAdmin() ? `<button type="button" class="ghost" id="attendance-print-all">Print report</button>` : ""}</div>
      </div>
      <div id="attendance-report-body">${reportMarkup()}</div>
    </section>
    ${isSuperAdmin() ? `<details class="attendance-policy attendance-audit"><summary>Recent attendance audit history</summary><div id="attendance-audit-list">${emptyRow("Loading audit history…")}</div></details>` : ""}`;
    target.querySelector("#attendance-check-in")?.addEventListener("click", checkIn);
    target.querySelector("#attendance-check-out")?.addEventListener("click", checkOut);
    target.querySelector("#attendance-month")?.addEventListener("change", async (event) => {
      state.month = event.target.value || monthNow();
      await reloadReport();
    });
    target.querySelector("#attendance-user")?.addEventListener("change", async (event) => {
      state.userId = event.target.value;
      await reloadReport();
    });
    target.querySelector("#attendance-department")?.addEventListener("change", async (event) => {
      state.departmentId = event.target.value;
      if (currentUserRole === "admin") state.userId = "all";
      await reloadReport();
    });
    target.querySelector("#attendance-policy-form")?.addEventListener("submit", savePolicy);
    target.querySelectorAll(".attendance-export").forEach((button, index) => {
      button.addEventListener("click", () => exportCsv(state.report.employees[index]));
    });
    target.querySelectorAll(".attendance-print").forEach((button, index) => {
      button.addEventListener("click", () => printReport([state.report.employees[index]]));
    });
    target.querySelector("#attendance-print-all")?.addEventListener("click", () => printReport(state.report?.employees || []));
    target.querySelectorAll(".attendance-leave-add").forEach((button) => {
      button.addEventListener("click", () => addLeave(button.dataset.user));
    });
    target.querySelectorAll(".attendance-leave-delete").forEach((button) => {
      button.addEventListener("click", () => deleteLeave(button.dataset.leave));
    });
    target.querySelectorAll(".attendance-correct").forEach((button) => {
      button.addEventListener("click", () => correctSession(button.dataset.session));
    });
    target.querySelectorAll(".attendance-resolve").forEach((button) => {
      button.addEventListener("click", () => resolveException(button.dataset.event));
    });
  }

  async function reloadReport() {
    try {
      await loadReport();
      render();
    } catch (error) {
      state.error = error.message;
      render();
    }
  }

  async function refresh() {
    const [statusResponse] = await Promise.all([getStatus()]);
    state.trackingLimit = statusResponse.tracking_limit;
    startHeartbeatLoop();
    if (!state.month) state.month = monthNow();
    if (canViewTeam() && !state.userId) state.userId = "all";
    await loadOptions();
    await loadReport();
    render();
    if (isSuperAdmin()) loadAudit().catch((error) => { state.error = error.message; });
  }

  async function checkIn() {
    state.busy = true;
    state.error = "";
    render();
    try {
      state.session = await api("/api/attendance/check-in", { method: "POST" });
      startHeartbeatLoop();
      await refresh();
    } catch (error) {
      state.error = error.message;
      render();
    } finally {
      state.busy = false;
      render();
    }
  }

  async function checkOut() {
    state.busy = true;
    state.error = "";
    render();
    try {
      state.session = await api("/api/attendance/check-out", { method: "POST" });
      await refresh();
    } catch (error) {
      state.error = error.message;
    } finally {
      state.busy = false;
      render();
    }
  }

  async function savePolicy(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const payload = {
      timezone: String(values.get("timezone")).trim(),
      heartbeat_interval_seconds: Number(values.get("heartbeat_interval_seconds")),
      inactivity_timeout_seconds: Number(values.get("inactivity_timeout_seconds")),
      work_start: String(values.get("work_start")),
      work_end: String(values.get("work_end")),
      late_grace_minutes: Number(values.get("late_grace_minutes")),
      overtime_enabled: values.has("overtime_enabled"),
      overtime_grace_minutes: Number(values.get("overtime_grace_minutes")),
      working_days: values.getAll("working_days").map(Number),
      allowed_half_leaves: Number(values.get("allowed_half_leaves")),
      allowed_days_off: Number(values.get("allowed_days_off")),
      allowed_late_checkins: Number(values.get("allowed_late_checkins")),
      allowed_early_offs: Number(values.get("allowed_early_offs")),
    };
    try {
      state.policy = await api("/api/attendance/policy", { method: "PUT", body: JSON.stringify(payload) });
      startHeartbeatLoop();
      setStatus("Attendance policy saved.");
      render();
    } catch (error) {
      state.error = error.message;
      render();
    }
  }

  async function correctSession(sessionId) {
    const hoursText = window.prompt("Correction in seconds (positive or negative; maximum 24 hours):");
    if (hoursText === null) return;
    const delta = Number(hoursText);
    const reason = window.prompt("Required correction reason:");
    if (reason === null) return;
    try {
      await api(`/api/attendance/sessions/${encodeURIComponent(sessionId)}/corrections`, {
        method: "POST",
        body: JSON.stringify({ delta_seconds: delta, reason }),
      });
      await refresh();
      setStatus("Attendance correction recorded and audited.");
    } catch (error) {
      state.error = error.message;
      render();
    }
  }

  async function resolveException(eventId) {
    const resolutionNote = window.prompt("How was this attendance exception reviewed?");
    if (resolutionNote === null) return;
    try {
      await api(`/api/attendance/events/${encodeURIComponent(eventId)}/resolve`, {
        method: "POST",
        body: JSON.stringify({ resolution_note: resolutionNote }),
      });
      await refresh();
      setStatus("Attendance exception resolved.");
    } catch (error) {
      state.error = error.message;
      render();
    }
  }

  async function loadAudit() {
    const items = await api("/api/attendance/audit?limit=100");
    const target = document.querySelector("#attendance-audit-list");
    if (!target) return;
    target.innerHTML = items.length ? `<div class="table-wrap"><table class="ws-table">
      <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Details</th></tr></thead>
      <tbody>${items.map((item) => `<tr><td>${esc(dateTime(item.created_at))}</td><td>${esc(item.display_name || item.username)}</td><td>${esc(item.action)}</td><td><code>${esc(JSON.stringify(item.details))}</code></td></tr>`).join("")}</tbody>
    </table></div>` : emptyRow("No attendance audit events yet.");
  }

  async function addLeave(userId) {
    const date = window.prompt("Leave date (YYYY-MM-DD):", `${state.month || monthNow()}-01`);
    if (!date) return;
    const type = (window.prompt("Type: half or full (day off)", "half") || "").trim().toLowerCase();
    if (!type) return;
    const reason = window.prompt("Reason (optional):") || "";
    try {
      await api("/api/attendance/leaves", {
        method: "POST",
        body: JSON.stringify({ user_id: userId, leave_date: date.trim(), leave_type: type, reason }),
      });
      await reloadReport();
    } catch (error) {
      state.error = error.message;
      render();
    }
  }

  async function deleteLeave(leaveId) {
    if (!window.confirm("Remove this leave record?")) return;
    try {
      await api(`/api/attendance/leaves/${encodeURIComponent(leaveId)}`, { method: "DELETE" });
      await reloadReport();
    } catch (error) {
      state.error = error.message;
      render();
    }
  }

  function printReport(employees) {
    if (!isSuperAdmin() || !employees.length) return;
    const win = window.open("", "_blank");
    if (!win) {
      state.error = "Allow pop-ups to print the report.";
      render();
      return;
    }
    const sections = employees.map((employee) => `<section>
      <h2>${esc(employee.employee_name)} <small>(${esc(employee.username)}) · ${esc(employee.month)}</small></h2>
      <p>Present: ${employee.days_present} · Absent: ${employee.days_absent} · Scheduled: ${employee.scheduled_working_days} · Confirmed office time: ${esc(hoursText(employee.confirmed_seconds))} · Overtime: ${esc(hoursText(employee.overtime_seconds))}</p>
      <table><thead><tr><th>Monthly allowance</th><th>Allowed</th><th>Taken</th><th>Remaining</th></tr></thead><tbody>
        ${ALLOWANCE_LABELS.map(([key, label]) => {
          const item = employee.allowances[key];
          return `<tr><td>${label}</td><td>${item.allowed}</td><td>${item.taken}</td><td>${item.exceeded ? "Exceeded" : item.remaining}</td></tr>`;
        }).join("")}
      </tbody></table>
      <table><thead><tr><th>Date</th><th>Check-in</th><th>Check-out</th><th>Confirmed</th><th>Flags</th></tr></thead><tbody>
        ${employee.daily.map((day) => `<tr><td>${esc(dateLabel(day.date))}</td><td>${esc((day.check_ins || []).map(dateTime).join(", ") || "—")}</td><td>${esc((day.check_outs || []).map(dateTime).join(", ") || "—")}</td><td>${esc(hoursText(day.confirmed_seconds))}</td><td>${[day.late_arrival ? "Late" : "", day.early_departure ? "Early off" : "", day.missed_check_out ? "Missed check-out" : ""].filter(Boolean).join(", ") || "—"}</td></tr>`).join("")}
      </tbody></table>
    </section>`).join("");
    win.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>Attendance report ${esc(state.report.month)}</title>
      <style>body{font:13px system-ui,sans-serif;margin:24px;color:#111}h1{font-size:20px}h2{font-size:16px;margin:20px 0 6px}small{font-weight:400;color:#555}
      table{border-collapse:collapse;width:100%;margin:8px 0}th,td{border:1px solid #bbb;padding:4px 8px;text-align:left}th{background:#eee}section{page-break-after:always}section:last-child{page-break-after:auto}</style>
      </head><body><h1>Attendance report · ${esc(state.report.month)}</h1><p>Timezone: ${esc(state.report.timezone)}</p>${sections}</body></html>`);
    win.document.close();
    win.focus();
    win.print();
  }

  function csvCell(value) {
    let text = String(value ?? "");
    if (/^[=+\-@]/.test(text)) text = `'${text}`;
    return `"${text.replace(/"/g, '""')}"`;
  }

  function exportCsv(employee) {
    const rows = [
      ["Employee", "Username", "Month", "Date", "Check-in", "Check-out", "Confirmed seconds", "Late", "Early", "Missed check-out", "Exceptions"],
      ...employee.daily.map((day) => [
        employee.employee_name,
        employee.username,
        employee.month,
        day.date,
        (day.check_ins || []).join(" | "),
        (day.check_outs || []).join(" | "),
        day.confirmed_seconds,
        day.late_arrival,
        day.early_departure,
        day.missed_check_out,
        day.exceptions.map((item) => item.event_type).join(" | "),
      ]),
    ];
    const blob = new Blob([rows.map((row) => row.map(csvCell).join(",")).join("\r\n")], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `attendance-${employee.username}-${employee.month}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function onRoute() {
    state.error = "";
    try {
      await refresh();
    } catch (error) {
      state.error = error.message;
      render();
    }
  }

  function start() {
    if (state.started) return;
    state.started = true;
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") sendHeartbeat();
    });
    window.addEventListener("pageshow", sendHeartbeat);
    getStatus()
      .then(() => {
        startHeartbeatLoop();
        if (state.session) sendHeartbeat();
      })
      .catch((error) => {
        state.error = error.message;
      });
  }

  window.TSLAttendance = { start, onRoute };
})();
