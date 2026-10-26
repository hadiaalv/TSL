async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    credentials: "same-origin",
    ...opts,
  });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

const $ = (sel) => document.querySelector(sel);
const TITLES = {
  overview: ["Control room", "Overview"],
  users: ["Access", "Users"],
  businesses: ["Portfolio", "Businesses"],
  work: ["Tracking", "Open work"],
  approvals: ["Queue", "Approvals"],
  google: ["Integrations", "Google sync"],
};

let overview = null;

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtWhen(ts) {
  if (!ts) return "Never";
  const d = new Date(ts.endsWith("Z") ? ts : ts + "Z");
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function setView(name) {
  document.querySelectorAll(".nav-item").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.toggle("active", v.id === `view-${name}`);
  });
  const [eye, title] = TITLES[name] || ["Admin", name];
  $("#view-eyebrow").textContent = eye;
  $("#view-title").textContent = title;
  history.replaceState({}, "", `#${name}`);
}

function renderStats(stats) {
  const cards = [
    { n: stats.users_active ?? 0, l: "Active users", cls: "" },
    { n: stats.google_connected ?? 0, l: "Google connected", cls: "ok" },
    { n: stats.businesses ?? 0, l: "Businesses", cls: "" },
    { n: stats.open_tasks ?? 0, l: "Open tasks", cls: "" },
    { n: stats.overdue ?? 0, l: "Overdue", cls: stats.overdue ? "hot" : "" },
    { n: stats.due_today ?? 0, l: "Due today", cls: stats.due_today ? "warn" : "" },
    { n: stats.followups_due ?? 0, l: "Follow-ups due", cls: stats.followups_due ? "warn" : "" },
    { n: stats.pending_approvals ?? 0, l: "Pending approvals", cls: stats.pending_approvals ? "warn" : "" },
  ];
  $("#stat-grid").innerHTML = cards
    .map(
      (c) =>
        `<div class="stat-card ${c.cls}"><b>${c.n}</b><span>${escapeHtml(c.l)}</span></div>`
    )
    .join("");
}

function renderAttention(attn) {
  const blocks = [
    ["Overdue", attn.overdue],
    ["Due today", attn.due_today],
    ["Follow-ups due", attn.followups_due],
    ["Waiting / delegated", attn.waiting_on],
    ["Decisions", attn.decisions],
  ];
  $("#attention-lists").innerHTML = blocks
    .map(([label, items]) => {
      const list = items || [];
      if (!list.length) {
        return `<div class="attn-block"><h3>${label}</h3><div class="empty">None</div></div>`;
      }
      return `<div class="attn-block"><h3>${label}</h3>${list
        .slice(0, 6)
        .map(
          (t) =>
            `<div class="attn-item">${escapeHtml(t.title)} <span class="row-sub">· ${escapeHtml(
              t.business_name || ""
            )}</span></div>`
        )
        .join("")}</div>`;
    })
    .join("");
}

function renderByBusiness(map) {
  const entries = Object.entries(map || {}).sort((a, b) => b[1] - a[1]);
  if (!entries.length) {
    $("#by-business").innerHTML = `<div class="empty">No open work yet.</div>`;
    return;
  }
  const max = Math.max(...entries.map(([, n]) => n), 1);
  $("#by-business").innerHTML = entries
    .map(
      ([name, n]) => `<div class="biz-bar-row">
        <span>${escapeHtml(name)}</span>
        <strong>${n}</strong>
        <div class="biz-bar-track"><div class="biz-bar-fill" style="width:${Math.round(
          (n / max) * 100
        )}%"></div></div>
      </div>`
    )
    .join("");
}

function renderUsers(users) {
  $("#users-count").textContent = `${users.length} total`;
  if (!users.length) {
    $("#users-table").innerHTML = `<div class="empty">No users yet.</div>`;
    return;
  }
  $("#users-table").innerHTML = users
    .map((u) => {
      const google = u.google_connected
        ? `<span class="badge on">Google on</span>`
        : `<span class="badge off">Google off</span>`;
      const statusCls = u.status === "active" ? "on" : "off";
      return `<div class="user-row" data-id="${escapeHtml(u.id)}">
        <div>
          <div class="row-title">${escapeHtml(u.username)}</div>
          <div class="row-sub">${escapeHtml(u.display_name || "")}</div>
        </div>
        <select data-field="role">
          ${["operator", "admin", "super_admin"]
            .map((r) => `<option value="${r}" ${u.role === r ? "selected" : ""}>${r}</option>`)
            .join("")}
        </select>
        <select data-field="status">
          <option value="active" ${u.status === "active" ? "selected" : ""}>active</option>
          <option value="disabled" ${u.status === "disabled" ? "selected" : ""}>disabled</option>
        </select>
        <div><span class="badge ${statusCls}">${escapeHtml(u.status)}</span>${google}</div>
        <div><button type="button" class="ghost save-btn" data-save>Save</button></div>
      </div>`;
    })
    .join("");

  $("#users-table").querySelectorAll("[data-save]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const row = btn.closest(".user-row");
      const id = row.dataset.id;
      const role = row.querySelector('[data-field="role"]').value;
      const status = row.querySelector('[data-field="status"]').value;
      try {
        await api(`/api/admin/users/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ role, status }),
        });
        await refresh();
      } catch (e) {
        alert(e.message);
      }
    });
  });
}

function renderBusinesses(list) {
  if (!list.length) {
    $("#biz-table").innerHTML = `<div class="empty">No brands yet. Create one from The Social Links.</div>`;
    return;
  }
  $("#biz-table").innerHTML = list
    .map((b) => {
      const active = (b.status || "active") === "active";
      return `<div class="data-row biz">
        <div>
          <div class="row-title">${escapeHtml(b.name)}</div>
          <div class="row-sub">${escapeHtml(b.slug || "")}</div>
        </div>
        <div class="row-sub">${escapeHtml((b.description || "").slice(0, 120))}</div>
        <div><span class="badge ${active ? "on" : "off"}">${escapeHtml(
          b.status || "active"
        )}</span></div>
      </div>`;
    })
    .join("");
}

function renderTasks(tasks) {
  $("#tasks-count").textContent = `${tasks.length} open`;
  if (!tasks.length) {
    $("#tasks-table").innerHTML = `<div class="empty">No open tasks.</div>`;
    return;
  }
  $("#tasks-table").innerHTML = tasks
    .map(
      (t) => `<div class="data-row task">
        <div>
          <div class="row-title">${escapeHtml(t.title)}</div>
          <div class="row-sub">${escapeHtml(t.business_name || "")}${
            t.deadline ? ` · due ${escapeHtml(String(t.deadline).slice(0, 10))}` : ""
          }</div>
        </div>
        <div><span class="badge">${escapeHtml(t.category || "task")}</span></div>
        <div class="row-sub">${escapeHtml(t.status || "")}</div>
      </div>`
    )
    .join("");
}

function renderApprovals(list) {
  $("#approvals-count").textContent = `${list.length} pending`;
  if (!list.length) {
    $("#approvals-table").innerHTML = `<div class="empty">None pending.</div>`;
    return;
  }
  $("#approvals-table").innerHTML = list
    .map(
      (a) => `<div class="data-row approval">
        <div>
          <div class="row-title">${escapeHtml(a.action_type || "Approval")}</div>
          <div class="row-sub">${escapeHtml((a.summary || "").slice(0, 140))}</div>
        </div>
        <div class="row-sub">${escapeHtml(a.created_at || "")}</div>
        <div><span class="badge warn">pending</span></div>
      </div>`
    )
    .join("");
}

function renderGoogle(users) {
  $("#google-table").innerHTML = users
    .map((u) => {
      const on = u.google_connected;
      return `<div class="data-row google">
        <div>
          <div class="row-title">${escapeHtml(u.username)}</div>
          <div class="row-sub">${escapeHtml(u.role)}</div>
        </div>
        <div>
          ${
            on
              ? `<div class="row-title">${escapeHtml(u.google_email || "Connected")}</div>`
              : `<div class="row-sub">Not connected</div>`
          }
          <div>${on ? `<span class="badge on">Connected</span>` : `<span class="badge off">Off</span>`}</div>
        </div>
        <div class="row-sub">Gmail<br/><strong>${
          on ? `${fmtWhen(u.last_email_pull_at)} · ${u.last_email_count || 0}` : "—"
        }</strong></div>
        <div class="row-sub">Calendar<br/><strong>${
          on ? `${fmtWhen(u.last_calendar_pull_at)} · ${u.last_calendar_count || 0}` : "—"
        }</strong></div>
      </div>`;
    })
    .join("");
}

function paint() {
  if (!overview) return;
  const { stats, users, businesses, tasks, approvals, attention } = overview;
  renderStats(stats || {});
  renderAttention(attention || {});
  renderByBusiness(stats?.by_business || {});
  renderUsers(users || []);
  renderBusinesses(businesses || []);
  renderTasks(tasks || []);
  renderApprovals(approvals || []);
  renderGoogle(users || []);
}

async function refresh() {
  overview = await api("/api/admin/overview");
  paint();
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => setView(btn.dataset.view));
});

document.querySelectorAll(".pw-toggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    const input = document.getElementById(btn.dataset.target);
    if (!input) return;
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.textContent = show ? "Hide" : "Show";
    btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
  });
});

$("#create-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#create-msg");
  const pw = $("#c-password").value;
  const pw2 = $("#c-password2").value;
  msg.className = "form-msg";
  if (pw !== pw2) {
    msg.textContent = "Passwords do not match.";
    msg.classList.add("err");
    return;
  }
  if (pw.length < 8) {
    msg.textContent = "Password must be at least 8 characters.";
    msg.classList.add("err");
    return;
  }
  msg.textContent = "Creating…";
  try {
    await api("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({
        username: $("#c-username").value.trim(),
        display_name: $("#c-display").value.trim(),
        password: pw,
        role: $("#c-role").value,
      }),
    });
    msg.textContent = "User created.";
    msg.classList.add("ok");
    e.target.reset();
    document.querySelectorAll(".pw-toggle").forEach((b) => {
      b.textContent = "Show";
    });
    await refresh();
    setView("users");
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("err");
  }
});

$("#admin-logout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST", body: "{}" });
  window.location.href = "/login";
});

(async () => {
  const me = await api("/api/auth/me");
  if (!me.user?.can_manage_users) {
    window.location.href = "/";
    return;
  }
  $("#admin-user").textContent = `${me.user.display_name || me.user.username} · ${me.user.role}`;
  await refresh();
  const hash = (location.hash || "#overview").replace("#", "");
  setView(TITLES[hash] ? hash : "overview");
})().catch((e) => {
  const msg = $("#create-msg");
  if (msg) {
    msg.textContent = e.message;
    msg.classList.add("err");
  }
});
