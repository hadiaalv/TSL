const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

let history = [];
let businessesCache = [];
let boardTasks = [];
let activeBoard = null;
let teamMembers = [];
let currentUserId = "";
let currentCanManage = false;
let currentCanManageTasks = false;
let currentUserRole = "";
let currentDepartmentIds = [];
let timelogSummaryText = "";
let notificationSeen = null;
let notificationSoundEnabled = false;
let notificationAudioReady = false;
let upcomingEventsCache = [];
let brandWorkloadCache = [];

const SIDEBAR_STATE_KEY = "tsl.sidebar.collapsed";
const SIDEBAR_SECTION_KEY = "tsl.sidebar.sections";
const MAX_LOGO_SIZE = 4 * 1024 * 1024;

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const isFormData = opts.body instanceof FormData;
  if (!isFormData && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, {
    headers,
    credentials: "same-origin",
    ...opts,
  });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || res.statusText);
  return data;
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatTimestamp(value) {
  const timestamp = String(value ?? "");
  if (!timestamp) return "Unknown";
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(timestamp) ? timestamp : `${timestamp}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleString();
}

function formatEventDate(value) {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function brandInitials(name) {
  const parts = String(name || "")
    .trim()
    .split(/\s+/)
    .slice(0, 2);
  return parts.map((part) => part[0] || "").join("").toUpperCase() || "BR";
}

function logoMarkup(brand, cls = "brand-logo") {
  if (brand.logo_url) {
    return `<img class="${cls}" src="${esc(brand.logo_url)}" alt="${esc(brand.name)} logo" loading="lazy" />`;
  }
  return `<span class="${cls} is-fallback" aria-label="${esc(brand.name)} logo fallback">${esc(brandInitials(brand.name))}</span>`;
}

function dateTimeLocalNowPlus(hours = 1) {
  const date = new Date(Date.now() + hours * 3600 * 1000);
  date.setSeconds(0, 0);
  const offset = date.getTimezoneOffset();
  const local = new Date(date.getTime() - offset * 60000);
  return local.toISOString().slice(0, 16);
}

function setStatus(message) {
  const el = $("#status");
  if (el) el.textContent = message || "";
}

function setPullMessage(message) {
  const el = $("#google-status");
  if (el) el.textContent = message;
}

function sidebarSectionState() {
  try {
    return JSON.parse(localStorage.getItem(SIDEBAR_SECTION_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveSidebarSectionState(next) {
  localStorage.setItem(SIDEBAR_SECTION_KEY, JSON.stringify(next));
}

function applySidebarSectionState(section, expanded) {
  const content = section.querySelector(".sidebar-section-content");
  const toggle = section.querySelector(".sidebar-section-toggle");
  if (!content || !toggle) return;
  section.classList.toggle("collapsed", !expanded);
  toggle.setAttribute("aria-expanded", String(expanded));
  content.style.maxHeight = expanded ? `${content.scrollHeight + 16}px` : "0px";
}

function initSidebarState() {
  const collapsed = localStorage.getItem(SIDEBAR_STATE_KEY) === "1";
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  $("#sidebar-collapse-toggle")?.setAttribute("aria-expanded", String(!collapsed));

  const savedSections = sidebarSectionState();
  $$("[data-sidebar-section]").forEach((section) => {
    const key = section.dataset.sidebarSection;
    const expanded = savedSections[key] !== false;
    applySidebarSectionState(section, expanded);
    section.querySelector(".sidebar-section-toggle")?.addEventListener("click", () => {
      const next = !section.classList.contains("collapsed");
      applySidebarSectionState(section, !next);
      const current = sidebarSectionState();
      current[key] = !next;
      saveSidebarSectionState(current);
    });
  });

  $("#sidebar-collapse-toggle")?.addEventListener("click", () => {
    const nextCollapsed = !document.body.classList.contains("sidebar-collapsed");
    document.body.classList.toggle("sidebar-collapsed", nextCollapsed);
    localStorage.setItem(SIDEBAR_STATE_KEY, nextCollapsed ? "1" : "0");
    $("#sidebar-collapse-toggle").setAttribute("aria-expanded", String(!nextCollapsed));
  });

  $("#mobile-sidebar-toggle")?.addEventListener("click", () => {
    const nextOpen = !document.body.classList.contains("mobile-sidebar-open");
    document.body.classList.toggle("mobile-sidebar-open", nextOpen);
    $("#mobile-sidebar-toggle").setAttribute("aria-expanded", String(nextOpen));
  });
  $("#mobile-sidebar-scrim")?.addEventListener("click", () => {
    document.body.classList.remove("mobile-sidebar-open");
    $("#mobile-sidebar-toggle")?.setAttribute("aria-expanded", "false");
  });
}

const PAGES = {
  home: "Overview", brands: "Brands", work: "Open Work", timelog: "Time Logs", calendar: "Calendar",
  ai: "AI Assistant", chat: "Team Chat", approvals: "Approvals", integrations: "Settings",
  workspace: "Tasks", notifications: "Notifications", reports: "Reports", deptmgmt: "Department Management",
};

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

function parseUtc(value) {
  return new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`);
}

function taskTimerMarkup(task) {
  const tracked = Number(task.tracked_seconds || 0);
  if (!task.timer_started_at && !tracked) return "";
  const running = Boolean(task.timer_started_at);
  return `<span class="task-timer${running ? " running" : ""}" data-tracked="${tracked}"${running ? ` data-timer-start="${esc(task.timer_started_at)}"` : ""}>⏱ ${esc(formatDuration(tracked))}</span>`;
}

function tickTaskTimers() {
  $$(".task-timer[data-timer-start]").forEach((el) => {
    const started = parseUtc(el.dataset.timerStart).getTime();
    if (Number.isNaN(started)) return;
    const live = (Date.now() - started) / 1000;
    el.textContent = `⏱ ${formatDuration(Number(el.dataset.tracked || 0) + live)}`;
  });
}

setInterval(tickTaskTimers, 1000);

function currentRoute() {
  const hash = (location.hash || "#home").replace(/^#/, "");
  if (hash.startsWith("brand/")) return { view: "brand", businessId: hash.slice(6) };
  if (hash.startsWith("tasks/")) return { view: "workspace", scope: hash.slice(6), nav: hash };
  if (hash.startsWith("dept/")) return { view: "workspace", scope: "dept", departmentId: hash.slice(5), nav: hash };
  if (PAGES[hash]) return { view: hash };
  return { view: "home" };
}

function routeAllowedForRole(route) {
  if (!currentUserRole || currentUserRole === "super_admin") return true;
  if (currentUserRole === "admin") return !["deptmgmt", "integrations"].includes(route.view);
  if (route.view === "workspace") return true;
  if (route.view === "brand") {
    return businessesCache.some((business) => business.id === route.businessId);
  }
  return ["home", "brands", "work", "timelog", "notifications"].includes(route.view);
}

function setRoute(route, { replace = false } = {}) {
  const target = `#${route}`;
  if (replace) {
    history.replaceState({}, "", target);
  } else if (location.hash !== target) {
    location.hash = route;
  }
}

function applyRouteView(route) {
  Object.keys(PAGES).forEach((view) => {
    $(`#${view}-view`)?.classList.toggle("hidden", route.view !== view);
  });
  $("#board-modal")?.classList.toggle("hidden", route.view !== "brand");
  const navView = route.nav || (route.view === "brand" ? "brands" : route.view);
  $$("[data-route]").forEach((link) => link.classList.toggle("active", link.dataset.route === navView));
  const title = $("#page-title");
  if (title) title.textContent = route.view === "brand" ? (activeBoard?.name || "Brand") : PAGES[route.view];
  $("#home-welcome")?.classList.toggle("hidden", route.view !== "home");
  document.body.classList.remove("mobile-sidebar-open");
  window.scrollTo(0, 0);
}

function renderWorkSummary(brief) {
  const summary = brief?.summary || {};
  const target = $("#work-summary");
  if (!target) return;
  target.innerHTML = `
    <div class="summary-item"><strong>${summary.pending_tasks || 0}</strong><span>Pending</span></div>
    <div class="summary-item"><strong>${summary.in_progress_tasks || 0}</strong><span>In progress</span></div>
    <div class="summary-item is-alert"><strong>${summary.overdue || 0}</strong><span>Overdue</span></div>
    <div class="summary-item"><strong>${summary.completed_tasks || 0}</strong><span>Completed</span></div>
  `;
}

function mdInline(raw) {
  const codes = [];
  let t = esc(raw).replace(/`([^`\n]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
  t = t
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\*\*\*([^*\n]+)\*\*\*/g, "<strong><em>$1</em></strong>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/(^|[^_\w])_([^_\s][^_\n]*?)_(?![_\w])/g, "$1<em>$2</em>")
    .replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  return t.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
}

function renderMarkdown(text) {
  const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  const listStack = [];
  const closeLists = (to = 0) => {
    while (listStack.length > to) out.push(`</${listStack.pop().tag}>`);
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      closeLists();
      const code = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i += 1;
      out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || "")) {
      closeLists();
      const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
      out.push(`<table class="md-table"><thead><tr>${head.map((c) => `<th>${mdInline(c)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${mdInline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/);
    const rule = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line);
    const item = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    const quote = line.match(/^\s*>\s?(.*)$/);
    if (heading) {
      closeLists();
      const level = Math.min(heading[1].length + 1, 6);
      out.push(`<h${level} class="md-h">${mdInline(heading[2])}</h${level}>`);
    } else if (rule) {
      closeLists();
      out.push("<hr />");
    } else if (item) {
      const depth = Math.floor(item[1].replace(/\t/g, "  ").length / 2);
      const tag = /\d/.test(item[2]) ? "ol" : "ul";
      while (listStack.length > depth + 1) out.push(`</${listStack.pop().tag}>`);
      if (listStack.length === depth + 1 && listStack[depth].tag !== tag) out.push(`</${listStack.pop().tag}>`);
      while (listStack.length < depth + 1) {
        out.push(`<${tag}>`);
        listStack.push({ tag });
      }
      out.push(`<li>${mdInline(item[3])}</li>`);
    } else if (quote) {
      closeLists();
      out.push(`<blockquote>${mdInline(quote[1])}</blockquote>`);
    } else if (!line.trim()) {
      closeLists();
    } else {
      closeLists();
      out.push(`<p>${mdInline(line)}</p>`);
    }
    i += 1;
  }
  closeLists();
  return out.join("");
}

function addBubble(role, text, tools) {
  const chatEl = $("#chat");
  if (!chatEl) return;
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  if (role === "assistant") div.classList.add("md");
  div.innerHTML = role === "assistant" ? renderMarkdown(text) : esc(text);
  if (tools?.length) {
    const t = document.createElement("div");
    t.className = "tools";
    t.textContent = "Tools: " + tools.map((x) => x.tool).join(", ");
    div.appendChild(t);
  }
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

async function refreshBrief() {
  const brief = await api("/api/brief");
  brandWorkloadCache = brief.brand_workload || [];
  const s = brief.summary;
  const stats = $("#brief-stats");
  if (stats) {
    stats.innerHTML = [
      ["open", s.open_tasks, ""],
      ["overdue", s.overdue, "hot"],
      ["due today", s.due_today, "warn"],
      ["follow-ups", s.followups_due, "warn"],
      ["waiting", s.waiting_on, ""],
      ["decisions", s.decisions, ""],
      ["approvals", s.pending_approvals, ""],
    ]
      .map(([label, n, cls]) => `<div class="stat ${cls}"><b>${n}</b><span>${label}</span></div>`)
      .join("");
  }
  renderWorkSummary(brief);
  renderBrandSurface();
}

function buildSidebarBusinessRows() {
  const target = $("#biz-list");
  if (!target) return;
  target.innerHTML = businessesCache.length
    ? businessesCache
        .map(
          (business) => `
      <li>
        <button class="biz-open" type="button" data-id="${esc(business.id)}">
          <span class="biz-logo">${logoMarkup(business, "brand-logo-sm")}</span>
          <span class="biz-copy">
            <strong>${esc(business.name)}</strong>
            <span class="slug">${esc(business.status)} · ${esc(business.slug)}</span>
          </span>
        </button>
      </li>`
        )
        .join("")
    : `<li><p class="muted">No brands yet.</p></li>`;
  target.querySelectorAll(".biz-open").forEach((button) => {
    button.addEventListener("click", () => setRoute(`brand/${button.dataset.id}`));
  });
}

function pendingCount(businessId) {
  return (boardTasks || []).filter(
    (task) => task.business_id === businessId && !["done", "cancelled"].includes(task.status)
  ).length;
}

function pendingTaskCounts() {
  const counts = new Map();
  (boardTasks || []).forEach((task) => {
    if (!task.business_id || ["done", "cancelled"].includes(task.status)) return;
    counts.set(task.business_id, (counts.get(task.business_id) || 0) + 1);
  });
  return counts;
}

function pendingBadge(businessId) {
  const n = pendingCount(businessId);
  return n ? `<span class="pending-badge">${n} open</span>` : "";
}

function renderPendingBrandTiles(list, target, counts, workloadByBrand) {
  if (!target) return;
  target.innerHTML = list
    .map((business) => {
      const workload = workloadByBrand.get(business.id);
      const open = counts.get(business.id) || 0;
      const pending = workload ? workload.pending_tasks || 0 : 0;
      return `
        <article class="brand-tile pending-brand-tile" data-brand-card="${esc(business.id)}">
          <button type="button" class="brand-open-card" data-brand-id="${esc(business.id)}">
            <span class="brand-cover">${logoMarkup({ ...business, logo_url: business.logo_url || workload?.logo_url }, "brand-logo-lg")}</span>
            <span class="pending-brand-copy">
              <span class="brand-title">${esc(business.name)}</span>
              <span class="pending-brand-metrics">
                ${workload
                  ? (pending ? `<span class="pending-badge">${pending} pending</span>` : "")
                  : `<span class="pending-badge">${open} open</span>`}
                ${workload?.in_progress_tasks ? `<span class="workload-badge in-progress">${workload.in_progress_tasks} in progress</span>` : ""}
                ${workload?.overdue_tasks ? `<span class="workload-badge overdue">${workload.overdue_tasks} overdue</span>` : ""}
                ${workload?.completed_tasks ? `<span class="workload-badge completed">${workload.completed_tasks} completed</span>` : ""}
              </span>
            </span>
          </button>
        </article>`;
    })
    .join("");
}

function renderBrandTiles(list, target) {
  if (!target) return;
  if (!list.length) {
    target.innerHTML = `<p class="muted">No brands to show here yet.</p>`;
    return;
  }
  target.innerHTML = list
    .map(
      (business) => `
      <article class="brand-tile" data-brand-card="${esc(business.id)}">
        <button type="button" class="brand-open-card" data-brand-id="${esc(business.id)}">
          <span class="brand-cover">${logoMarkup(business, "brand-logo-lg")}</span>
          <span class="brand-title">${esc(business.name)}${pendingBadge(business.id)}</span>
        </button>
      </article>`
    )
    .join("");
}

function attachBrandTileEvents(scope = document) {
  scope.querySelectorAll("[data-brand-id]").forEach((button) => {
    button.addEventListener("click", () => setRoute(`brand/${button.dataset.brandId}`));
  });
  scope.querySelectorAll("[data-star-id]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const businessId = button.dataset.starId;
      const current = businessesCache.find((item) => item.id === businessId);
      if (!current) return;
      button.disabled = true;
      try {
        await api(`/api/businesses/${encodeURIComponent(businessId)}/star`, {
          method: "PUT",
          body: JSON.stringify({ starred: !current.is_starred }),
        });
        current.is_starred = !current.is_starred;
        renderBrandSurface();
      } catch (error) {
        setStatus(`Could not update star: ${error.message}`);
      } finally {
        button.disabled = false;
      }
    });
  });
}

function renderBrandSurface() {
  const pendingCounts = pendingTaskCounts();
  const workloadByBrand = new Map(brandWorkloadCache.map((workload) => [workload.id, workload]));
  const query = ($("#brand-search")?.value || "").trim().toLowerCase();
  const filtered = businessesCache.filter((business) => {
    if (!query) return true;
    return (
      business.name.toLowerCase().includes(query)
      || String(business.slug || "").toLowerCase().includes(query)
    );
  });
  const pendingBrands = businessesCache
    .filter((business) => {
      const workload = workloadByBrand.get(business.id);
      return workload
        ? (workload.pending_tasks || 0) + (workload.in_progress_tasks || 0) > 0
        : pendingCount(business.id) > 0;
    })
    .sort((x, y) => {
      const xWorkload = workloadByBrand.get(x.id);
      const yWorkload = workloadByBrand.get(y.id);
      return (
        (yWorkload?.overdue_tasks || 0) - (xWorkload?.overdue_tasks || 0)
        || ((yWorkload?.pending_tasks || 0) + (yWorkload?.in_progress_tasks || 0))
          - ((xWorkload?.pending_tasks || 0) + (xWorkload?.in_progress_tasks || 0))
        || x.name.localeCompare(y.name, undefined, { sensitivity: "base" })
      );
    });
  const recent = businessesCache
    .filter((business) => business.last_visited_at)
    .sort((a, b) => String(b.last_visited_at || "").localeCompare(String(a.last_visited_at || "")))
    .slice(0, 8);

  renderPendingBrandTiles(pendingBrands, $("#pending-brands"), pendingCounts, workloadByBrand);
  const pendingSection = $("#pending-section");
  if (pendingSection) pendingSection.hidden = !pendingBrands.length;
  const pendingTotal = $("#pending-total");
  if (pendingTotal) {
    const taskTotal = pendingBrands.reduce((total, business) => {
      const workload = workloadByBrand.get(business.id);
      return total + (
        workload
          ? (workload.pending_tasks || 0) + (workload.in_progress_tasks || 0)
          : pendingCounts.get(business.id) || 0
      );
    }, 0);
    pendingTotal.textContent = `${taskTotal} open ${taskTotal === 1 ? "task" : "tasks"}`;
  }
  renderBrandTiles(recent, $("#recent-brands"));

  const allTarget = $("#all-brands");
  if (allTarget) {
    allTarget.innerHTML = filtered.length
      ? filtered
          .map(
            (business) => `
        <article class="brand-tile" data-brand-card="${esc(business.id)}">
          <button type="button" class="brand-open-card" data-brand-id="${esc(business.id)}">
            <span class="brand-cover">${logoMarkup(business, "brand-logo-lg")}</span>
            <span class="brand-title">${esc(business.name)}${pendingBadge(business.id)}</span>
          </button>
        </article>`
          )
          .join("")
      : `<p class="muted">No matching brands found.</p>`;
    if (currentCanManage) {
      allTarget.insertAdjacentHTML(
        "beforeend",
        `<article class="brand-tile add-brand-tile"><button id="add-brand-card" type="button">＋ Add brand</button></article>`
      );
    }
  }

  attachBrandTileEvents(document);
  $("#add-brand-card")?.addEventListener("click", () => openAddBrandModal());
  buildSidebarBusinessRows();
}

async function refreshBusinesses() {
  const list = await api("/api/businesses");
  businessesCache = list;
  renderBrandSurface();
  populateBrandSelects();
}

function renderTaskCard(task) {
  const status = String(task.status || "").toLowerCase();
  const statusLabel = status.replace(/[_-]/g, " ");
  const due = task.deadline ? String(task.deadline).slice(0, 10) : "";
  const overdue = due && due < new Date().toISOString().slice(0, 10);
  const owner = task.owner_username || task.owner || "";
  return `<article class="card task-card ${esc(status)}${overdue ? " is-overdue" : ""}" data-task-id="${esc(task.id)}">
      <div class="task-card-top">
        <span class="task-brand">${esc(task.business_name)}</span>
      </div>
      <div class="title">${esc(task.title)}</div>
      <div class="meta">
        <span class="task-status ${esc(status)}">${esc(statusLabel)}</span>
        ${due ? `<span class="task-due${overdue ? " overdue" : ""}">${overdue ? "Overdue · " : "Due "}${esc(due)}</span>` : ""}
        ${owner ? `<span class="task-owner">@${esc(owner)}</span>` : ""}
      </div>
      <button type="button" class="task-done-btn" data-mark-done="${esc(task.id)}">✓ Mark done</button>
    </article>`;
}

async function setTaskStatus(taskId, status) {
  await api(`/api/tasks/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
  const jobs = [refreshTasks(), refreshBrief()];
  if (activeBoard) jobs.push(loadBoard(activeBoard));
  await Promise.all(jobs);
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-mark-done]");
  if (!button) return;
  event.preventDefault();
  button.disabled = true;
  try {
    await setTaskStatus(button.dataset.markDone, "done");
  } catch (error) {
    button.disabled = false;
    setStatus(`Could not update task: ${error.message}`);
  }
});

document.addEventListener("change", async (event) => {
  const select = event.target.closest("[data-task-status]");
  if (!select) return;
  select.disabled = true;
  try {
    await setTaskStatus(select.dataset.taskStatus, select.value);
  } catch (error) {
    select.disabled = false;
    const el = $("#board-status");
    if (el) el.textContent = `Could not update status: ${error.message}`;
  }
});

function renderOpenWork(tasks) {
  const sidebar = $("#task-board");
  const list = $("#work-list");
  const count = $("#open-work-count");
  if (count) count.textContent = String(tasks.length);
  if (sidebar) {
    sidebar.innerHTML = tasks.length
      ? tasks.slice(0, 14).map(renderTaskCard).join("")
      : `<p class="muted">No open work.</p>`;
  }
  if (list) {
    list.innerHTML = tasks.length
      ? tasks.map(renderTaskCard).join("")
      : `<p class="muted">No in-progress or pending items right now.</p>`;
  }
}

async function refreshTasks() {
  const tasks = await api("/api/tasks?open_only=true");
  boardTasks = tasks;
  renderOpenWork(tasks);
  renderBrandSurface();
  if (activeBoard) {
    const fresh = businessesCache.find((item) => item.id === activeBoard.id);
    if (fresh) {
      activeBoard = fresh;
      await loadBoard(fresh);
      await renderBrandEvents(fresh.id);
    }
  }
}

function renderUpcomingEvents(events) {
  const target = $("#upcoming-events-list");
  if (!target) return;
  target.innerHTML = events.length
    ? events
        .map(
          (event) => `
      <article class="event-card">
        <span class="event-date">${esc(formatEventDate(event.starts_at))}</span>
        <h3>${esc(event.title)}</h3>
        <p>${esc(event.business_name)}</p>
        <p class="muted">${esc((event.event_type || "event").replace(/_/g, " "))}</p>
      </article>`
        )
        .join("")
    : `<p class="muted">No upcoming events yet. Schedule one to get started.</p>`;
}

async function renderBrandEvents(businessId) {
  const target = $("#brand-events-list");
  if (!target) return;
  const scoped = upcomingEventsCache.filter((item) => item.business_id === businessId).slice(0, 8);
  if (!scoped.length) {
    target.innerHTML = `<p class="muted">No upcoming events for this brand.</p>`;
    return;
  }
  target.innerHTML = scoped
    .map(
      (event) => `
      <article class="event-card">
        <span class="event-date">${esc(formatEventDate(event.starts_at))}</span>
        <h3>${esc(event.title)}</h3>
        ${event.notes ? `<p>${esc(event.notes)}</p>` : ""}
      </article>`
    )
    .join("");
}

async function refreshEvents() {
  const result = await api("/api/events?limit=60");
  upcomingEventsCache = result || [];
  renderUpcomingEvents(upcomingEventsCache);
  if (activeBoard) {
    await renderBrandEvents(activeBoard.id);
  }
}

async function refreshActivity() {
  const rows = await api("/api/activity");
  const target = $("#recent-activity");
  if (!target) return;
  target.innerHTML = rows.length
    ? rows
        .map(
          (row) => `
      <article class="activity-item">
        <div class="activity-logo">${logoMarkup({ name: row.business_name, logo_url: row.business_logo_url }, "brand-logo-xs")}</div>
        <div>
          <p><strong>${esc(row.business_name)}</strong> · ${esc(row.title)}</p>
          <p class="muted">${esc(row.status)} · ${esc(formatTimestamp(row.updated_at))}</p>
        </div>
      </article>`
        )
        .join("")
    : `<p class="muted">No recent activity yet.</p>`;
}

function boardStatus(status) {
  if (status === "in_progress" || status === "delegated") return "in_progress";
  if (status === "waiting_on" || status === "blocked") return "waiting_on";
  if (status === "done" || status === "cancelled") return "done";
  return "open";
}

function renderBoardTask(task) {
  const commentsId = `task-comments-${task.id}`;
  return `<article class="board-task" draggable="true" data-task-id="${esc(task.id)}">
    <h4>${esc(task.title)}</h4>
    <div class="board-task-meta">
      <span>${esc(task.category || "other")}</span>
      ${task.owner && task.owner !== "me" ? `<span>${esc(task.owner)}</span>` : ""}
      ${task.deadline ? `<span>Due ${esc(task.deadline.slice(0, 10))}</span>` : ""}
      ${taskTimerMarkup(task)}
    </div>
    ${task.notes ? `<p class="muted">${esc(task.notes)}</p>` : ""}
    <label class="task-status-control">Status
      <select data-task-status="${esc(task.id)}" aria-label="Change status">
        ${[["open", "To Do"], ["in_progress", "In Progress"], ["waiting_on", "Review"], ["done", "Completed"]]
          .map(([value, label]) => `<option value="${value}"${boardStatus(task.status) === value ? " selected" : ""}>${label}</option>`).join("")}
      </select>
    </label>
    <button class="task-comments-toggle" type="button" data-comments-toggle="${esc(task.id)}">Comments & mentions</button>
    <section id="${commentsId}" class="task-comments" data-comments-task="${esc(task.id)}">
      <div class="task-comments-list"><span class="muted">Loading comments…</span></div>
      <form class="task-comment-form" data-comment-form="${esc(task.id)}">
        <textarea aria-label="Add a comment, use @username to mention someone" placeholder="Add a comment… Use @username to mention a teammate" maxlength="2000" required></textarea>
        <div class="mention-suggestions" aria-live="polite"></div>
        <button type="submit">Comment</button>
      </form>
    </section>
  </article>`;
}

function taskColumn(title, value) {
  const items = boardTasks.filter((task) => task.business_id === activeBoard.id && boardStatus(task.status) === value);
  return `<section class="board-column" data-drop-status="${value}">
    <h3><span class="column-title">${title}</span><span class="column-count">${items.length}</span></h3>
    ${items.length ? items.map(renderBoardTask).join("") : `<p class="column-empty">Drop cards here</p>`}
  </section>`;
}

async function refreshTeamMembers() {
  teamMembers = await api("/api/team/members");
  const select = $("#board-task-owner");
  if (select) {
    select.innerHTML = `<option value="me">Unassigned / me</option>` + teamMembers
      .map((member) => `<option value="${esc(member.username)}">${esc(member.display_name)} (@${esc(member.username)})</option>`)
      .join("");
  }
}

async function loadTaskComments(taskId) {
  const comments = await api(`/api/tasks/${encodeURIComponent(taskId)}/comments`);
  const container = $(`[data-comments-task="${CSS.escape(taskId)}"] .task-comments-list`);
  if (!container) return;
  container.innerHTML = comments.length
    ? comments.map((comment) => `<p class="task-comment"><strong>${esc(comment.author_display_name || comment.author_username)}</strong> <time>${esc(formatTimestamp(comment.created_at))}</time><br>${esc(comment.content)}</p>`).join("")
    : `<p class="muted task-comment">No comments yet.</p>`;
}

function bindBoardInteractions() {
  const columns = $("#board-columns");
  if (!columns) return;
  columns.querySelectorAll(".board-task").forEach((card) => {
    card.addEventListener("dragstart", (event) => {
      card.classList.add("dragging");
      event.dataTransfer.setData("text/plain", card.dataset.taskId);
      event.dataTransfer.effectAllowed = "move";
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
  });
  columns.querySelectorAll(".board-column").forEach((column) => {
    column.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
    });
    column.addEventListener("drop", async (event) => {
      event.preventDefault();
      const taskId = event.dataTransfer.getData("text/plain");
      const task = boardTasks.find((item) => item.id === taskId);
      const nextStatus = column.dataset.dropStatus;
      if (!task || boardStatus(task.status) === nextStatus) return;
      try {
        await api(`/api/tasks/${encodeURIComponent(taskId)}`, {
          method: "PATCH",
          body: JSON.stringify({ status: nextStatus }),
        });
        await Promise.all([loadBoard(activeBoard), refreshTasks(), refreshBrief()]);
      } catch (error) {
        $("#board-status").textContent = `Could not move card: ${error.message}`;
      }
    });
  });
  columns.querySelectorAll("[data-comments-toggle]").forEach((button) => {
    button.addEventListener("click", async () => {
      const section = $(`[data-comments-task="${CSS.escape(button.dataset.commentsToggle)}"]`);
      section.classList.toggle("open");
      if (section.classList.contains("open")) {
        try {
          await loadTaskComments(button.dataset.commentsToggle);
        } catch (error) {
          section.querySelector(".task-comments-list").textContent = `Could not load comments: ${error.message}`;
        }
      }
    });
  });
  columns.querySelectorAll("[data-comment-form]").forEach((form) => {
    const textarea = form.querySelector("textarea");
    const suggestions = form.querySelector(".mention-suggestions");
    textarea.addEventListener("input", () => {
      const match = textarea.value.match(/(?:^|\s)@([A-Za-z0-9_.-]*)$/);
      if (!match) {
        suggestions.innerHTML = "";
        return;
      }
      const prefix = match[1].toLowerCase();
      const options = teamMembers.filter((member) =>
        member.username.toLowerCase().startsWith(prefix)
      );
      suggestions.innerHTML = options
        .slice(0, 5)
        .map((member) => `<button type="button" data-mention="${esc(member.username)}">@${esc(member.username)}</button>`)
        .join("");
      suggestions.querySelectorAll("button").forEach((chip) => {
        chip.addEventListener("click", () => {
          textarea.value = textarea.value.replace(/(?:^|\s)@([A-Za-z0-9_.-]*)$/, ` @${chip.dataset.mention} `);
          textarea.focus();
          suggestions.innerHTML = "";
        });
      });
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = form.querySelector("button[type=submit]");
      submit.disabled = true;
      const content = textarea.value.trim();
      if (!content) {
        submit.disabled = false;
        return;
      }
      try {
        await api(`/api/tasks/${encodeURIComponent(form.dataset.commentForm)}/comments`, {
          method: "POST",
          body: JSON.stringify({ content }),
        });
        textarea.value = "";
        suggestions.innerHTML = "";
        await loadTaskComments(form.dataset.commentForm);
        await refreshNotifications();
      } catch (error) {
        $("#board-status").textContent = `Could not save comment: ${error.message}`;
      } finally {
        submit.disabled = false;
      }
    });
  });
}

async function loadBoard(business) {
  const tasks = await api(`/api/tasks?business_id=${encodeURIComponent(business.id)}&open_only=false`);
  boardTasks = boardTasks.filter((task) => task.business_id !== business.id).concat(tasks);
  const columns = $("#board-columns");
  if (!columns) return;
  columns.innerHTML = [
    taskColumn("To Do", "open"),
    taskColumn("In Progress", "in_progress"),
    taskColumn("Review", "waiting_on"),
    taskColumn("Completed", "done"),
  ].join("");
  bindBoardInteractions();
}

async function openBoard(businessId, focusTaskId = "") {
  let business = businessesCache.find((item) => item.id === businessId);
  if (!business) {
    await refreshBusinesses();
    business = businessesCache.find((item) => item.id === businessId);
  }
  if (!business) throw new Error("Brand not found.");
  activeBoard = business;
  await api(`/api/businesses/${encodeURIComponent(business.id)}/visit`, { method: "POST", body: "{}" });
  await refreshBusinesses();
  $("#board-title").textContent = business.name;
  $("#board-description").textContent = business.description || "Organize this brand's work together.";
  $("#board-brand-logo").innerHTML = logoMarkup(business, "brand-logo-header-mark");
  $("#board-status").textContent = "";
  await Promise.all([refreshTeamMembers(), loadBoard(business), refreshEvents()]);
  setBrandTab("board");
  resetSopForm();
  await Promise.all([refreshBrandLinks(), refreshBrandSops()]);

  $$(".biz-open").forEach((button) => button.classList.toggle("active", button.dataset.id === business.id));

  if (focusTaskId) {
    const task = $(`[data-task-id="${CSS.escape(focusTaskId)}"]`);
    if (task) {
      task.classList.add("notification-focus");
      task.scrollIntoView({ block: "center", behavior: "smooth" });
      task.querySelector(".task-comments-toggle")?.click();
    }
  }
}

const LINK_ICONS = {
  website: "🌐", instagram: "📷", facebook: "👍", tiktok: "🎵", youtube: "▶",
  linkedin: "💼", x: "✕", shop: "🛍", drive: "📁", other: "🔗",
};

async function refreshBrandLinks() {
  const target = $("#brand-links-list");
  if (!target || !activeBoard) return;
  const links = await api(`/api/businesses/${encodeURIComponent(activeBoard.id)}/links`);
  target.innerHTML = links.length
    ? links
        .map((link) => {
          const name = link.label || link.kind.charAt(0).toUpperCase() + link.kind.slice(1);
          return `<span class="brand-link-chip"><a href="${esc(link.url)}" target="_blank" rel="noopener noreferrer">${LINK_ICONS[link.kind] || "🔗"} ${esc(name)}</a><button type="button" data-remove-link="${esc(link.id)}" aria-label="Remove ${esc(name)}">×</button></span>`;
        })
        .join("")
    : `<p class="muted">No links yet. Add the website, Instagram, Facebook and more below.</p>`;
}

function bindBrandLinks() {
  $("#brand-link-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!activeBoard) return;
    try {
      await api(`/api/businesses/${encodeURIComponent(activeBoard.id)}/links`, {
        method: "POST",
        body: JSON.stringify({
          kind: $("#brand-link-kind").value,
          url: $("#brand-link-url").value,
          label: $("#brand-link-label").value,
        }),
      });
      $("#brand-link-url").value = "";
      $("#brand-link-label").value = "";
      await refreshBrandLinks();
    } catch (error) {
      $("#board-status").textContent = `Could not add link: ${error.message}`;
    }
  });
  $("#brand-links-list")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-remove-link]");
    if (!button || !activeBoard) return;
    await api(`/api/businesses/${encodeURIComponent(activeBoard.id)}/links/${encodeURIComponent(button.dataset.removeLink)}`, { method: "DELETE" });
    await refreshBrandLinks();
  });
}

let brandSopsCache = [];
let editingSopId = "";

function setBrandTab(name) {
  $$(".brand-tab").forEach((tab) => {
    const on = tab.dataset.brandTab === name;
    tab.classList.toggle("active", on);
    tab.setAttribute("aria-selected", on ? "true" : "false");
  });
  $("#brand-tab-board")?.classList.toggle("hidden", name !== "board");
  $("#brand-tab-sops")?.classList.toggle("hidden", name !== "sops");
}

function resetSopForm() {
  editingSopId = "";
  $("#brand-sop-form")?.reset();
  $("#brand-sop-submit").textContent = "Add SOP";
  $("#brand-sop-cancel").classList.add("hidden");
}

async function refreshBrandSops() {
  const target = $("#brand-sops-list");
  if (!target || !activeBoard) return;
  brandSopsCache = await api(`/api/businesses/${encodeURIComponent(activeBoard.id)}/sops`);
  const badge = $("#brand-sops-count");
  if (badge) badge.textContent = String(brandSopsCache.length);
  target.innerHTML = brandSopsCache.length
    ? brandSopsCache
        .map((sop) => {
          const link = sop.url
            ? `<a class="brand-sop-link" href="${esc(sop.url)}" target="_blank" rel="noopener noreferrer">🔗 Open document</a>`
            : "";
          const body = sop.content ? `<div class="brand-sop-body">${esc(sop.content)}</div>` : "";
          const actions = currentCanManage
            ? `<span><button type="button" class="text-link" data-edit-sop="${esc(sop.id)}">Edit</button><button type="button" class="text-link" data-delete-sop="${esc(sop.id)}">Delete</button></span>`
            : "";
          return `<article class="brand-sop-card"><div class="brand-sop-head"><strong>${esc(sop.title)}</strong>${actions}</div>${body}${link}</article>`;
        })
        .join("")
    : `<p class="muted">${currentCanManage ? "No SOPs yet. Add the first one below." : "No SOPs available yet."}</p>`;
}

function bindBrandTabs() {
  $$(".brand-tab").forEach((tab) => tab.addEventListener("click", () => setBrandTab(tab.dataset.brandTab)));
  $("#brand-sop-cancel")?.addEventListener("click", resetSopForm);
  $("#brand-sop-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!activeBoard) return;
    const base = `/api/businesses/${encodeURIComponent(activeBoard.id)}/sops`;
    const body = JSON.stringify({
      title: $("#brand-sop-title").value,
      content: $("#brand-sop-content").value,
      url: $("#brand-sop-url").value,
    });
    try {
      if (editingSopId) await api(`${base}/${encodeURIComponent(editingSopId)}`, { method: "PATCH", body });
      else await api(base, { method: "POST", body });
      resetSopForm();
      await refreshBrandSops();
    } catch (error) {
      $("#board-status").textContent = `Could not save SOP: ${error.message}`;
    }
  });
  $("#brand-sops-list")?.addEventListener("click", async (event) => {
    if (!activeBoard) return;
    const edit = event.target.closest("[data-edit-sop]");
    const del = event.target.closest("[data-delete-sop]");
    if (edit) {
      const sop = brandSopsCache.find((item) => item.id === edit.dataset.editSop);
      if (!sop) return;
      editingSopId = sop.id;
      $("#brand-sop-title").value = sop.title;
      $("#brand-sop-content").value = sop.content || "";
      $("#brand-sop-url").value = sop.url || "";
      $("#brand-sop-submit").textContent = "Save SOP";
      $("#brand-sop-cancel").classList.remove("hidden");
      $("#brand-sop-title").focus();
    } else if (del) {
      if (!confirm("Delete this SOP?")) return;
      await api(`/api/businesses/${encodeURIComponent(activeBoard.id)}/sops/${encodeURIComponent(del.dataset.deleteSop)}`, { method: "DELETE" });
      if (editingSopId === del.dataset.deleteSop) resetSopForm();
      await refreshBrandSops();
    }
  });
}

function runGlobalSearch() {
  const box = $("#global-search-results");
  const query = ($("#global-search")?.value || "").trim().toLowerCase();
  if (!box) return;
  if (!query) {
    box.classList.add("hidden");
    return;
  }
  const brands = businessesCache.filter((b) => b.name.toLowerCase().includes(query)).slice(0, 6);
  const tasks = boardTasks.filter((t) => t.title.toLowerCase().includes(query)).slice(0, 8);
  const events = upcomingEventsCache.filter((ev) => String(ev.title || "").toLowerCase().includes(query)).slice(0, 4);
  const group = (label, rows) => (rows.length ? `<div class="gs-group"><span>${label}</span>${rows.join("")}</div>` : "");
  const html =
    group("Brands", brands.map((b) => `<button type="button" data-go="brand/${esc(b.id)}">${esc(b.name)}</button>`))
    + group("Tasks", tasks.map((t) => `<button type="button" data-go="brand/${esc(t.business_id)}">${esc(t.title)} <small>${esc(t.business_name || "")}</small></button>`))
    + group("Events", events.map((ev) => `<button type="button" data-go="calendar">${esc(ev.title)}</button>`));
  box.innerHTML = html || `<p class="muted">No results for “${esc(query)}”.</p>`;
  box.classList.remove("hidden");
}

function bindGlobalSearch() {
  const input = $("#global-search");
  input?.addEventListener("input", runGlobalSearch);
  input?.addEventListener("focus", runGlobalSearch);
  $("#global-search-results")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-go]");
    if (!button) return;
    setRoute(button.dataset.go);
    input.value = "";
    $("#global-search-results").classList.add("hidden");
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".global-search")) $("#global-search-results")?.classList.add("hidden");
  });
}

async function uploadBrandLogo(businessId, file) {
  if (!file) return null;
  if (!/^image\/(png|jpeg|webp|svg\+xml)$/i.test(file.type)) {
    throw new Error("Upload a PNG, JPEG, WebP, or SVG image.");
  }
  if (file.size > MAX_LOGO_SIZE) {
    throw new Error("Logo files must be 4 MB or smaller.");
  }
  const form = new FormData();
  form.append("file", file);
  return api(`/api/businesses/${encodeURIComponent(businessId)}/logo`, {
    method: "POST",
    body: form,
  });
}

function setLogoPreview(input, target) {
  const file = input.files?.[0];
  if (!file) {
    target.innerHTML = `<span class="muted">No logo selected</span>`;
    return;
  }
  const src = URL.createObjectURL(file);
  target.innerHTML = `<img src="${esc(src)}" alt="Selected logo preview" class="logo-preview-img" />`;
}

function openAddBrandModal() {
  $("#biz-name").value = "";
  $("#biz-desc").value = "";
  $("#biz-ctx").value = "";
  $("#biz-logo-file").value = "";
  $("#biz-logo-preview").innerHTML = `<span class="muted">No logo selected</span>`;
  $("#modal").classList.remove("hidden");
  $("#biz-name").focus();
}

function populateBrandSelects() {
  const options = businessesCache
    .map((business) => `<option value="${esc(business.id)}">${esc(business.name)}</option>`)
    .join("");
  const eventSelect = $("#event-brand");
  const taskSelect = $("#quick-task-brand");
  if (eventSelect) eventSelect.innerHTML = options;
  if (taskSelect) taskSelect.innerHTML = options;
}

function openEventModal(defaultBusinessId = "") {
  const select = $("#event-brand");
  populateBrandSelects();
  if (defaultBusinessId && select) {
    select.value = defaultBusinessId;
  }
  $("#event-form-error").textContent = "";
  $("#event-title").value = "";
  $("#event-type").value = "event";
  $("#event-notes").value = "";
  $("#event-start").value = dateTimeLocalNowPlus(1);
  $("#event-modal").classList.remove("hidden");
}

async function openTaskModal(defaultBusinessId = "") {
  populateBrandSelects();
  if (defaultBusinessId) $("#quick-task-brand").value = defaultBusinessId;
  const departmentLabel = $("#quick-task-department-label");
  if (departmentLabel) departmentLabel.classList.toggle("hidden", !currentCanManageTasks);
  if (currentCanManageTasks) {
    const departments = await api("/api/departments");
    const available = currentCanManage
      ? departments
      : departments.filter((department) => currentDepartmentIds.includes(department.id));
    const departmentSelect = $("#quick-task-department");
    if (departmentSelect) {
      departmentSelect.innerHTML = [
        ...(currentCanManage ? [`<option value="">No department</option>`] : [`<option value="">Choose a department</option>`]),
        ...available.map((department) => `<option value="${esc(department.id)}">${esc(department.name)}</option>`),
      ].join("");
      departmentSelect.required = !currentCanManage;
    }
  }
  $("#quick-task-name").value = "";
  $("#quick-task-due").value = "";
  $("#quick-task-error").textContent = "";
  $("#quick-task-modal").classList.remove("hidden");
}

async function refreshApprovals() {
  const rows = await api("/api/approvals");
  const container = $("#approvals");
  if (!container) return;
  if (!rows.length) {
    container.innerHTML = `<p class="muted">None pending</p>`;
    return;
  }
  container.innerHTML = rows
    .map(
      (a) => `<div class="item" data-id="${esc(a.id)}">
        <div>${esc(a.summary)}</div>
        <div class="muted">${esc(a.action_type)}</div>
        <div class="actions">
          <button data-ok="1">Approve</button>
          <button data-ok="0" class="ghost">Reject</button>
        </div>
      </div>`
    )
    .join("");
  container.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.closest(".item").dataset.id;
      await api(`/api/approvals/${id}/decide`, {
        method: "POST",
        body: JSON.stringify({ approved: btn.dataset.ok === "1" }),
      });
      await refreshAll();
    });
  });
}

function setPullStatus(el, ts, count) {
  if (!el) return;
  if (!ts) {
    el.textContent = "Not pulled yet";
    el.className = "pull-never";
    return;
  }
  const d = new Date(ts.endsWith("Z") ? ts : `${ts}Z`);
  const when = Number.isNaN(d.getTime())
    ? ts
    : d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
  el.textContent = `OK · ${when} · ${count ?? 0}`;
  el.className = "pull-ok";
}

async function refreshGoogle() {
  const st = await api("/api/google/status");
  const el = $("#google-status");
  const link = $("#google-link");
  const disc = $("#btn-google-disconnect");
  const badge = $("#google-conn-badge");
  const emailEl = $("#google-email");
  const card = $("#google-account-card");
  const driveStatus = $("#drive-access-status");
  if (!el || !link || !badge || !emailEl || !card || !driveStatus) return;

  setPullStatus($("#pull-email-status"), st.last_email_pull_at, st.last_email_count);
  setPullStatus($("#pull-cal-status"), st.last_calendar_pull_at, st.last_calendar_count);
  setPullStatus(driveStatus, st.last_drive_pull_at, st.last_drive_count);
  const driveVerified = st.drive_status === "verified";
  const reconnectRequired = st.requires_reconnect
    || ["reconnect_required", "permission_missing"].includes(st.drive_status);

  if (st.connected) {
    card.classList.add("is-connected");
    badge.textContent = driveVerified ? "Google + Drive verified" : "Google connected";
    badge.className = driveVerified ? "g-badge on" : "g-badge off";
    emailEl.textContent = st.email || "Connected (email unavailable)";
    emailEl.classList.toggle("muted", !st.email);
    el.textContent = st.drive_error || (st.email ? `Google account: ${st.email}` : "Google account connected");
    link.textContent = "Reconnect Google account";
    link.classList.add("btn-connect-secondary");
    disc?.classList.remove("hidden");
    if (!driveVerified && st.drive_status) {
      driveStatus.textContent = st.drive_error || "Drive access has not been verified";
      driveStatus.className = "pull-never";
    } else if (driveVerified && !st.last_drive_pull_at) {
      driveStatus.textContent = "Connected and verified · not synced yet";
      driveStatus.className = "pull-ok";
    }
  } else if (st.oauth_configured) {
    card.classList.remove("is-connected");
    badge.textContent = reconnectRequired ? "Reconnect required" : "Not connected";
    badge.className = "g-badge off";
    emailEl.textContent = "No Google account linked";
    emailEl.classList.add("muted");
    el.textContent = st.connection_error
      || st.drive_error
      || (st.requires_reconnect
        ? "Google authorization expired or was revoked. Reconnect to restore access."
        : "Connect Google, then allow Gmail, Calendar, and Drive read-only access.");
    link.textContent = st.requires_reconnect ? "Reconnect Google account" : "Connect Google account";
    link.classList.remove("btn-connect-secondary");
    disc?.classList.toggle("hidden", !st.token_present);
  } else {
    card.classList.remove("is-connected");
    badge.textContent = "Not configured";
    badge.className = "g-badge off";
    emailEl.textContent = "OAuth client missing on server";
    emailEl.classList.add("muted");
    el.textContent = "Add Google client id/secret on the server";
    link.classList.add("btn-connect-secondary");
    disc?.classList.add("hidden");
  }
}

let notificationAudioContext = null;

function unlockNotificationAudio() {
  if (!window.AudioContext) return;
  if (!notificationAudioContext) notificationAudioContext = new AudioContext();
  if (notificationAudioContext.state === "suspended") notificationAudioContext.resume();
  notificationAudioReady = true;
}

function playNotificationSound() {
  if (!notificationAudioReady || !notificationSoundEnabled || !notificationAudioContext) return;
  try {
    const context = notificationAudioContext;
    if (context.state === "suspended") context.resume();
    [740, 988].forEach((freq, i) => {
      const start = context.currentTime + i * 0.18;
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.2, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.16);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start(start);
      oscillator.stop(start + 0.17);
    });
  } catch (error) {
    console.warn("Could not play notification sound:", error);
  }
}

async function refreshNotifications() {
  const data = await api("/api/notifications");
  const items = data.items || [];
  notificationSoundEnabled = !!data.sound_enabled;
  const soundToggle = $("#notification-sound");
  if (soundToggle) soundToggle.checked = notificationSoundEnabled;
  const badge = $("#notification-badge");
  if (badge) {
    badge.textContent = String(data.unread_count || 0);
    badge.classList.toggle("hidden", !data.unread_count);
  }
  const navBadge = $("#count-notifications");
  if (navBadge) {
    navBadge.textContent = String(data.unread_count || 0);
    navBadge.classList.toggle("hidden", !data.unread_count);
  }
  const list = $("#notification-list");
  if (!list) return;
  list.innerHTML = items.length
    ? items.map((item) => `<article class="notification-item ${item.read_at ? "" : "unread"}" data-id="${esc(item.id)}" data-url="${esc(item.target_url || "")}">
        <button type="button">
          <p>${esc(item.title)}</p>
          ${item.body ? `<p class="muted">${esc(item.body)}</p>` : ""}
          <time>${esc(formatTimestamp(item.created_at))}</time>
        </button>
      </article>`).join("")
    : `<p class="muted">No notifications.</p>`;
  list.querySelectorAll(".notification-item button").forEach((button) => {
    button.addEventListener("click", async () => {
      const item = button.closest(".notification-item");
      try {
        await api(`/api/notifications/${encodeURIComponent(item.dataset.id)}/read`, {
          method: "POST",
          body: "{}",
        });
        const url = item.dataset.url || "";
        const taskMatch = url.match(/[?&]task=([^&]+)/);
        if (url.endsWith("#chat")) {
          setRoute("chat");
          $("#notification-panel")?.classList.add("hidden");
        } else if (taskMatch) {
          $("#notification-panel")?.classList.add("hidden");
          await window.TSLWorkspace.openTask(decodeURIComponent(taskMatch[1]));
        }
      } catch (error) {
        setStatus(`Could not open notification: ${error.message}`);
      } finally {
        await refreshNotifications();
      }
    });
  });
  const currentIds = new Set(items.map((item) => item.id));
  if (notificationSeen && items.some((item) => !notificationSeen.has(item.id))) {
    playNotificationSound();
  }
  notificationSeen = currentIds;
}

async function refreshAll() {
  const jobs = [
    refreshBrief(),
    refreshBusinesses(),
    refreshTasks(),
    refreshEvents(),
    refreshActivity(),
    refreshNotifications(),
  ];
  if (currentCanManage) jobs.push(refreshGoogle());
  if (currentCanManageTasks) jobs.push(refreshApprovals());
  await Promise.all(jobs);
}

async function sendMessage(text) {
  if (!text.trim()) return;
  addBubble("user", text);
  history.push({ role: "user", content: text });
  setStatus("Thinking with Gemini…");
  $("#send").disabled = true;
  try {
    const res = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message: text, history }),
    });
    addBubble("assistant", res.reply || "(empty reply)", res.tools_used);
    history.push({ role: "assistant", content: res.reply || "" });
    await refreshAll();
  } catch (error) {
    addBubble("assistant", "Error: " + error.message);
  } finally {
    setStatus("");
    $("#send").disabled = false;
  }
}

async function refreshSession() {
  try {
    const me = await api("/api/auth/me");
    currentUserId = me.user?.id || "";
    currentCanManage = Boolean(me.user?.can_manage_users);
    currentCanManageTasks = Boolean(me.user?.can_manage_tasks);
    currentUserRole = me.user?.role || "operator";
    currentDepartmentIds = me.user?.department_ids || [];
    $$(".sa-only").forEach((el) => el.classList.toggle("hidden", !currentCanManage));
    $$(".admin-only").forEach((el) => el.classList.toggle("hidden", !currentCanManageTasks));
    document.body.classList.toggle("is-operator", currentUserRole === "operator");
    document.body.classList.toggle("not-sa", !currentCanManage);
    document.body.classList.toggle("not-admin", !currentCanManageTasks);
    const label = me.user?.display_name || me.user?.username || "";
    const roleName = { super_admin: "Super Admin", admin: "Admin", operator: "Operator" }[currentUserRole];
    const role = roleName ? ` · ${roleName}` : "";
    const el = $("#session-user");
    if (el) el.textContent = label + role;
    const welcome = $("#home-welcome");
    if (welcome) welcome.textContent = label ? `Welcome back, ${label}. Here’s your portfolio at a glance.` : "Your portfolio at a glance";
    window.TSLWorkspace?.onSession(me.user);
    renderBrandSurface();
  } catch {
    // redirect handled in api()
  }
}

function attachGlobalHandlers() {
  $("#brand-search")?.addEventListener("input", renderBrandSurface);

  $("#composer")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#input");
    const text = input.value;
    input.value = "";
    sendMessage(text);
  });
  $$(".quick button").forEach((button) => {
    button.addEventListener("click", () => sendMessage(button.dataset.q || ""));
  });

  $("#btn-add-biz")?.addEventListener("click", openAddBrandModal);
  $("#quick-add-brand")?.addEventListener("click", openAddBrandModal);
  $("#btn-create-board")?.addEventListener("click", openAddBrandModal);
  $("#modal-cancel")?.addEventListener("click", () => $("#modal").classList.add("hidden"));
  $("#biz-logo-file")?.addEventListener("change", (event) => setLogoPreview(event.currentTarget, $("#biz-logo-preview")));

  $("#modal-save")?.addEventListener("click", async () => {
    const name = $("#biz-name").value.trim();
    if (!name) {
      $("#biz-name").focus();
      return;
    }
    const saveButton = $("#modal-save");
    saveButton.disabled = true;
    try {
      const created = await api("/api/businesses", {
        method: "POST",
        body: JSON.stringify({
          name,
          description: $("#biz-desc").value,
          context_md: $("#biz-ctx").value,
        }),
      });
      const logo = $("#biz-logo-file").files?.[0];
      if (logo) await uploadBrandLogo(created.id, logo);
      $("#modal").classList.add("hidden");
      await refreshAll();
      setRoute(`brand/${created.id}`);
    } catch (error) {
      setStatus(`Could not add brand: ${error.message}`);
    } finally {
      saveButton.disabled = false;
    }
  });

  $("#board-close")?.addEventListener("click", () => {
    activeBoard = null;
    setRoute("brands");
  });

  $("#edit-board-details")?.addEventListener("click", () => {
    $("#board-edit-name").value = activeBoard?.name || "";
    $("#board-edit-description").value = activeBoard?.description || "";
    $("#board-logo-file").value = "";
    $("#board-edit-form").classList.toggle("hidden");
  });

  $("#board-edit-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!activeBoard) return;
    const submit = event.currentTarget.querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      await api(`/api/businesses/${encodeURIComponent(activeBoard.id)}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: $("#board-edit-name").value,
          description: $("#board-edit-description").value,
        }),
      });
      const file = $("#board-logo-file").files?.[0];
      if (file) await uploadBrandLogo(activeBoard.id, file);
      $("#board-edit-form").classList.add("hidden");
      await refreshAll();
      await openBoard(activeBoard.id);
    } catch (error) {
      $("#board-status").textContent = `Could not update brand: ${error.message}`;
    } finally {
      submit.disabled = false;
    }
  });

  $("#remove-brand-logo")?.addEventListener("click", async () => {
    if (!activeBoard) return;
    try {
      await api(`/api/businesses/${encodeURIComponent(activeBoard.id)}/logo`, {
        method: "DELETE",
      });
      await refreshAll();
      await openBoard(activeBoard.id);
    } catch (error) {
      $("#board-status").textContent = `Could not remove logo: ${error.message}`;
    }
  });

  $("#board-task-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!activeBoard) return;
    const form = event.currentTarget;
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    $("#board-status").textContent = "Adding card…";
    try {
      await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify({
          business_id: activeBoard.id,
          title: $("#board-task-title").value.trim(),
          owner: $("#board-task-owner").value,
          deadline: $("#board-task-due").value || null,
          category: $("#board-task-category").value,
          status: "open",
          source: "board",
        }),
      });
      $("#board-task-title").value = "";
      $("#board-task-due").value = "";
      $("#board-status").textContent = "";
      await Promise.all([refreshTasks(), refreshBrief(), refreshActivity()]);
      await openBoard(activeBoard.id);
    } catch (error) {
      $("#board-status").textContent = `Could not add card: ${error.message}`;
    } finally {
      submit.disabled = false;
    }
  });

  $("#schedule-event")?.addEventListener("click", () => openEventModal());
  $("#quick-schedule-event")?.addEventListener("click", () => openEventModal());
  $("#brand-schedule-event")?.addEventListener("click", () => openEventModal(activeBoard?.id || ""));
  $("#event-cancel")?.addEventListener("click", () => $("#event-modal").classList.add("hidden"));

  $("#event-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const save = $("#event-save");
    save.disabled = true;
    $("#event-form-error").textContent = "";
    try {
      await api("/api/events", {
        method: "POST",
        body: JSON.stringify({
          business_id: $("#event-brand").value,
          title: $("#event-title").value.trim(),
          event_type: $("#event-type").value,
          starts_at: new Date($("#event-start").value).toISOString(),
          notes: $("#event-notes").value.trim(),
        }),
      });
      $("#event-modal").classList.add("hidden");
      await refreshEvents();
    } catch (error) {
      $("#event-form-error").textContent = error.message;
    } finally {
      save.disabled = false;
    }
  });

  $("#quick-add-task")?.addEventListener("click", () => openTaskModal().catch((error) => setStatus(`Could not open task form: ${error.message}`)));
  $("#work-add-task")?.addEventListener("click", () => openTaskModal().catch((error) => setStatus(`Could not open task form: ${error.message}`)));
  $("#quick-task-cancel")?.addEventListener("click", () => $("#quick-task-modal").classList.add("hidden"));
  $("#quick-task-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const save = $("#quick-task-save");
    save.disabled = true;
    $("#quick-task-error").textContent = "";
    try {
      await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify({
          business_id: $("#quick-task-brand").value,
          title: $("#quick-task-name").value.trim(),
          deadline: $("#quick-task-due").value || null,
          department_id: $("#quick-task-department")?.value || null,
          status: "open",
          category: "other",
          owner: "me",
          source: "manual",
        }),
      });
      $("#quick-task-modal").classList.add("hidden");
      await Promise.all([refreshTasks(), refreshBrief(), refreshActivity()]);
    } catch (error) {
      $("#quick-task-error").textContent = error.message;
    } finally {
      save.disabled = false;
    }
  });

  $("#calendar-open")?.addEventListener("click", () => {
    refreshEvents().catch((error) => setStatus(`Could not refresh events: ${error.message}`));
  });

  $("#notification-toggle")?.addEventListener("click", async () => {
    const panel = $("#notification-panel");
    const isOpen = panel.classList.toggle("hidden") === false;
    $("#notification-toggle").setAttribute("aria-expanded", String(isOpen));
    if (isOpen) {
      try {
        await refreshNotifications();
      } catch (error) {
        $("#notification-list").textContent = `Could not load notifications: ${error.message}`;
      }
    }
  });

  $("#notification-sound")?.addEventListener("change", async (event) => {
    const enabled = event.currentTarget.checked;
    try {
      const result = await api("/api/notifications/sound", {
        method: "PUT",
        body: JSON.stringify({ enabled }),
      });
      notificationSoundEnabled = result.sound_enabled;
      unlockNotificationAudio();
    } catch (error) {
      event.currentTarget.checked = !enabled;
      setStatus(`Could not save sound preference: ${error.message}`);
    }
  });

  $("#btn-import-biz")?.addEventListener("click", () => $("#import-modal").classList.remove("hidden"));
  $("#import-cancel")?.addEventListener("click", () => $("#import-modal").classList.add("hidden"));
  $("#import-save")?.addEventListener("click", async () => {
    const markdown = $("#import-md").value;
    if (!markdown.trim()) {
      setStatus("Paste brand brief markdown first.");
      return;
    }
    setStatus("Importing brands…");
    try {
      const res = await api("/api/businesses/import", {
        method: "POST",
        body: JSON.stringify({ markdown, skip_existing_names: true }),
      });
      $("#import-modal").classList.add("hidden");
      $("#import-md").value = "";
      addBubble(
        "assistant",
        `Imported ${res.created_count} brand(s)` +
          (res.skipped?.length ? ` (skipped existing: ${res.skipped.join(", ")})` : "") +
          "."
      );
      await refreshAll();
    } catch (error) {
      addBubble("assistant", "Import error: " + error.message);
    } finally {
      setStatus("");
    }
  });

  $("#btn-emails")?.addEventListener("click", async () => {
    setStatus("Fetching Gmail signals…");
    try {
      const data = await api("/api/google/emails");
      const n = data.proposed_actions?.length || 0;
      await refreshGoogle();
      setPullMessage(`Email pull OK — ${data.emails?.length || 0} messages. ${n} look actionable.`);
    } catch (error) {
      await refreshGoogle().catch(() => {});
      setPullMessage("Gmail: " + error.message);
    } finally {
      setStatus("");
    }
  });

  $("#btn-cal")?.addEventListener("click", async () => {
    setStatus("Fetching calendar…");
    try {
      const data = await api("/api/google/calendar");
      await refreshGoogle();
      setPullMessage(`Calendar pull OK — ${(data.events || []).length} upcoming events.`);
    } catch (error) {
      await refreshGoogle().catch(() => {});
      setPullMessage("Calendar: " + error.message);
    } finally {
      setStatus("");
    }
  });

  $("#btn-drive")?.addEventListener("click", async () => {
    const button = $("#btn-drive");
    setStatus("Fetching Google Drive files…");
    button.disabled = true;
    try {
      const data = await api("/api/google/drive?max_results=10");
      await refreshGoogle();
      addBubble("assistant", `Google Drive pull OK — ${data.count || 0} recent file(s) fetched.`);
    } catch (error) {
      addBubble("assistant", "Google Drive: " + error.message);
      await refreshGoogle();
    } finally {
      button.disabled = false;
      setStatus("");
    }
  });

  $("#btn-drive-import")?.addEventListener("click", async () => {
    const button = $("#btn-drive-import");
    setStatus("Matching brand folders and loading source links…");
    button.disabled = true;
    try {
      const data = await api("/api/google/drive/import", { method: "POST" });
      await Promise.all([refreshBusinesses(), refreshGoogle()]);
      addBubble("assistant", `Drive import finished — ${data.updated_count || 0} brand context(s) updated.`);
    } catch (error) {
      addBubble("assistant", "Google Drive import: " + error.message);
      await refreshGoogle();
    } finally {
      button.disabled = false;
      setStatus("");
    }
  });

  $("#btn-logout")?.addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", { method: "POST", body: "{}" });
    } catch {
      // ignore
    }
    window.location.href = "/login";
  });

  $("#btn-google-disconnect")?.addEventListener("click", async () => {
    try {
      await api("/api/google/disconnect", { method: "POST", body: "{}" });
      await refreshGoogle();
      addBubble("assistant", "Google disconnected for your account.");
    } catch (error) {
      addBubble("assistant", "Disconnect failed: " + error.message);
    }
  });
}

let slackState = { threadTs: "", channel: "", conversations: [], timer: null, connected: false, unread: {}, teamName: "", unreadSeen: null, pollTimer: null };

function stopSlackPolling() {
  clearInterval(slackState.timer);
  slackState.timer = null;
}

function slackInitials(name) {
  const parts = String(name || "?").replace(/^[#@]/, "").trim().split(/\s+/);
  return ((parts[0]?.[0] || "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

function slackColor(name) {
  let hash = 0;
  for (const ch of String(name)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return `hsl(${hash % 360} 45% 42%)`;
}

function slackEmoji(name) {
  return (window.SLACK_EMOJI_FLAT || {})[String(name).split("::")[0]] || "";
}

function formatSlackText(text) {
  return esc(text)
    .replace(/:([a-z0-9_+\-]+(?:::skin-tone-\d)?):/g, (match, name) => slackEmoji(name) || match)
    .replace(/&lt;(https?:\/\/[^|&]+?)\|([^&]*?)&gt;/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$2</a>')
    .replace(/&lt;(https?:\/\/[^&]+?)&gt;/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/&lt;#[A-Z0-9]+\|([^&]*?)&gt;/g, "#$1")
    .replace(/&lt;!(here|channel|everyone)[^&]*?&gt;/g, "@$1")
    .replace(/```([\s\S]+?)```/g, "<pre>$1</pre>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/(^|\s)\*([^*\n]+)\*(?=\s|$|[.,!?])/g, "$1<strong>$2</strong>")
    .replace(/(^|\s)_([^_\n]+)_(?=\s|$|[.,!?])/g, "$1<em>$2</em>");
}

function renderSlackConversations() {
  const filter = ($("#slack-filter")?.value || "").toLowerCase();
  const labels = { channel: "Channels", private: "Private channels", group: "Group messages", dm: "Direct messages" };
  const items = slackState.conversations.filter((c) => c.name.toLowerCase().includes(filter));
  const target = $("#slack-conversations");
  if (!items.length) {
    target.innerHTML = `<p class="muted">No conversations found.</p>`;
    return;
  }
  let html = "";
  let last = "";
  items.forEach((c) => {
    if (c.kind !== last) {
      html += `<h4>${labels[c.kind]}</h4>`;
      last = c.kind;
    }
    const count = slackState.unread[c.id]?.count || 0;
    const prefix = c.kind === "dm" ? `<span class="slack-dot"></span>` : c.kind === "private" ? "🔒 " : "";
    const label = c.kind === "channel" ? esc(c.name) : `${prefix}${esc(c.name)}`;
    html += `<button type="button" class="slack-conv${c.id === slackState.channel ? " active" : ""}${count ? " unread" : ""}" data-conv="${esc(c.id)}" data-name="${esc(c.name)}"><span class="slack-conv-name">${label}</span>${count ? `<span class="slack-badge">${count > 9 ? "9+" : count}</span>` : ""}</button>`;
  });
  target.innerHTML = html;
}

function renderSlackFiles(files) {
  if (!files?.length) return "";
  const proxy = (u, dl) => `/api/slack/file?url=${encodeURIComponent(u)}${dl ? "&download=true" : ""}`;
  return `<div class="slack-files">${files.map((f) => {
    const type = f.mimetype || "";
    if (type.startsWith("image/") && !type.includes("svg")) {
      return `<a href="${esc(proxy(f.url))}" target="_blank" rel="noopener"><img class="slack-img" loading="lazy" alt="${esc(f.name)}" src="${esc(proxy(f.thumb || f.url))}" /></a>`;
    }
    if (type.startsWith("video/")) return `<video class="slack-img" controls preload="metadata" src="${esc(proxy(f.url))}"></video>`;
    if (type.startsWith("audio/")) return `<audio controls src="${esc(proxy(f.url))}"></audio>`;
    const kb = f.size ? ` · ${f.size > 1048576 ? (f.size / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(f.size / 1024)) + " KB"}` : "";
    return `<a class="slack-file" href="${esc(proxy(f.url, true))}">📎 <span>${esc(f.name)}</span><small>${esc(kb)}</small></a>`;
  }).join("")}</div>`;
}

function slackAvatar(m) {
  if (m.avatar) return `<img class="slack-avatar" alt="" src="${esc(m.avatar)}" />`;
  return `<span class="slack-avatar" style="background:${slackColor(m.user)}">${esc(slackInitials(m.user))}</span>`;
}

function slackThreadLink(m, inThread) {
  if (inThread || !m.reply_count) return "";
  return `<button type="button" class="slack-replies" data-thread="${esc(m.ts)}">💬 ${m.reply_count} ${m.reply_count === 1 ? "reply" : "replies"}</button>`;
}

function slackReactions(m) {
  if (!m.reactions?.length) return "";
  return `<div class="slack-reactions">${m.reactions.map((r) => `<button type="button" class="slack-reaction${r.me ? " me" : ""}" data-react="${esc(r.name)}" data-ts="${esc(m.ts)}" data-me="${r.me ? 1 : 0}">${esc(slackEmoji(r.name) || `:${r.name}:`)} <span>${r.count}</span></button>`).join("")}</div>`;
}

function slackActions(m, inThread) {
  const reply = inThread ? "" : `<button type="button" data-thread="${esc(m.ts)}" title="Reply in thread">↪ Reply</button>`;
  return `<div class="slack-reply-btn"><button type="button" data-add-react="${esc(m.ts)}" title="Add reaction">😊</button>${reply}</div>`;
}

function renderSlackMessages(messages, inThread = false) {
  let html = "";
  let lastDay = "";
  let lastUser = "";
  let lastTime = 0;
  messages.forEach((m) => {
    const date = new Date(Number(m.ts) * 1000);
    const day = date.toDateString();
    if (day !== lastDay) {
      const label = day === new Date().toDateString() ? "Today" : date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
      html += `<div class="slack-day"><span>${esc(label)}</span></div>`;
      lastDay = day;
      lastUser = "";
    }
    const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const grouped = m.user === lastUser && Number(m.ts) - lastTime < 300;
    if (grouped) {
      html += `<div class="slack-msg grouped"><span class="slack-hover-time">${esc(time)}</span><div class="slack-body"><p>${formatSlackText(m.text)}</p>${renderSlackFiles(m.files)}${slackReactions(m)}${slackThreadLink(m, inThread)}</div>${slackActions(m, inThread)}</div>`;
    } else {
      html += `<div class="slack-msg">${slackAvatar(m)}<div class="slack-body"><div class="slack-head"><strong>${esc(m.user)}</strong><span class="slack-time">${esc(time)}</span></div><p>${formatSlackText(m.text)}</p>${renderSlackFiles(m.files)}${slackReactions(m)}${slackThreadLink(m, inThread)}</div>${slackActions(m, inThread)}</div>`;
    }
    lastUser = m.user;
    lastTime = Number(m.ts);
  });
  return html;
}

async function loadSlackMessages({ scroll = false } = {}) {
  if (!slackState.channel) return;
  const channel = slackState.channel;
  const box = $("#slack-messages");
  try {
    const messages = await api(`/api/slack/messages?channel=${encodeURIComponent(channel)}`);
    if (channel !== slackState.channel) return;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    box.innerHTML = messages.length ? renderSlackMessages(messages) : `<p class="muted slack-empty">No messages yet. Say hello!</p>`;
    if (scroll || atBottom) box.scrollTop = box.scrollHeight;
    if (slackState.unread[channel]) {
      delete slackState.unread[channel];
      renderSlackConversations();
      updateSlackNavBadge();
    }
  } catch (error) {
    box.innerHTML = `<p class="form-error">Could not load messages: ${esc(error.message)}</p>`;
  }
}

let emojiPickerHandler = null;

function closeEmojiPicker() {
  $("#emoji-picker").classList.add("hidden");
  emojiPickerHandler = null;
}

function openEmojiPicker(anchor, handler) {
  const picker = $("#emoji-picker");
  emojiPickerHandler = handler;
  const groups = window.SLACK_EMOJI || {};
  picker.innerHTML = `<input type="search" class="emoji-search" placeholder="Search emoji" aria-label="Search emoji" /><div class="emoji-grid">${Object.entries(groups).map(([group, items]) => `<h5>${esc(group)}</h5><div>${Object.entries(items).map(([name, char]) => `<button type="button" data-emoji-name="${esc(name)}" data-char="${esc(char)}" title=":${esc(name)}:">${char}</button>`).join("")}</div>`).join("")}</div>`;
  picker.classList.remove("hidden");
  const rect = anchor.getBoundingClientRect();
  const width = picker.offsetWidth;
  const height = picker.offsetHeight;
  picker.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`;
  picker.style.top = `${rect.top - height - 8 > 8 ? rect.top - height - 8 : Math.min(rect.bottom + 8, window.innerHeight - height - 8)}px`;
  picker.querySelector(".emoji-search").focus();
}

function filterEmojiPicker(term) {
  const q = term.trim().toLowerCase();
  $("#emoji-picker").querySelectorAll(".emoji-grid button").forEach((b) => {
    b.classList.toggle("hidden", !!q && !b.dataset.emojiName.includes(q));
  });
  $("#emoji-picker").querySelectorAll(".emoji-grid h5").forEach((h) => h.classList.toggle("hidden", !!q));
}

function insertAtCursor(input, text) {
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  input.value = input.value.slice(0, start) + text + input.value.slice(end);
  input.focus();
  input.setSelectionRange(start + text.length, start + text.length);
}

async function toggleSlackReaction(ts, name, remove) {
  try {
    await api("/api/slack/reactions", {
      method: "POST",
      body: JSON.stringify({ channel: slackState.channel, ts, name, remove }),
    });
    await loadSlackMessages();
    await loadSlackThread();
  } catch (error) {
    setStatus(`Could not update reaction: ${error.message}`);
  }
}

async function uploadSlackFile(file, target) {
  if (!file || !slackState.channel) {
    if (file) setStatus("Select a conversation first.");
    return;
  }
  const inThread = target === "thread" && slackState.threadTs;
  const input = $(inThread ? "#slack-thread-input" : "#slack-input");
  const form = new FormData();
  form.append("channel", slackState.channel);
  form.append("comment", input.value.trim());
  if (inThread) form.append("thread_ts", slackState.threadTs);
  form.append("file", file);
  setStatus(`Uploading ${file.name}…`);
  try {
    await api("/api/slack/upload", { method: "POST", body: form });
    input.value = "";
    setStatus(`Sent ${file.name}.`);
    await loadSlackMessages({ scroll: true });
    if (inThread) await loadSlackThread({ scroll: true });
  } catch (error) {
    setStatus(`Could not upload file: ${error.message}`);
  }
}

async function openSlackThread(ts) {
  slackState.threadTs = ts;
  const panel = $("#slack-thread-panel");
  panel.classList.remove("hidden");
  $("#slack-app").classList.add("with-thread");
  await loadSlackThread({ scroll: true });
  $("#slack-thread-input").focus();
}

function closeSlackThread() {
  slackState.threadTs = "";
  $("#slack-thread-panel").classList.add("hidden");
  $("#slack-app").classList.remove("with-thread");
}

async function loadSlackThread({ scroll = false } = {}) {
  if (!slackState.threadTs) return;
  const box = $("#slack-thread-messages");
  try {
    const messages = await api(`/api/slack/replies?channel=${encodeURIComponent(slackState.channel)}&ts=${encodeURIComponent(slackState.threadTs)}`);
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    box.innerHTML = renderSlackMessages(messages, true);
    if (scroll || atBottom) box.scrollTop = box.scrollHeight;
  } catch (error) {
    box.innerHTML = `<p class="form-error">Could not load thread: ${esc(error.message)}</p>`;
  }
}

function selectSlackConversation(id, name) {
  closeSlackThread();
  slackState.channel = id;
  const conv = slackState.conversations.find((c) => c.id === id);
  $("#slack-thread-title").textContent = name;
  $("#slack-input").disabled = false;
  $("#slack-input").placeholder = `Message ${conv?.kind === "channel" ? "#" + name.replace(/^#/, "") : name}`;
  $("#slack-form button[type=submit]").disabled = false;
  $("#slack-messages").innerHTML = `<p class="muted slack-empty">Loading…</p>`;
  renderSlackConversations();
  loadSlackMessages({ scroll: true });
  $("#slack-input").focus();
}

function updateSlackNavBadge() {
  const total = Object.values(slackState.unread).reduce((sum, u) => sum + u.count, 0);
  document.querySelectorAll('[href="#chat"]').forEach((link) => {
    let badge = link.querySelector(".slack-nav-badge");
    if (!total) {
      badge?.remove();
      return;
    }
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "slack-nav-badge";
      link.appendChild(badge);
    }
    badge.textContent = total > 99 ? "99+" : String(total);
  });
}

function showSlackDesktopNotification(info) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const where = info.kind === "dm" ? "" : ` in ${info.kind === "channel" ? "#" : ""}${info.name}`;
  try {
    const n = new Notification(`Slack: ${info.user}${where}`, { body: info.text, tag: `slack-${info.ts}` });
    n.onclick = () => {
      window.focus();
      setRoute("chat");
      n.close();
    };
  } catch (error) {
    console.warn("Desktop notification failed:", error);
  }
}

async function pollSlackUnread() {
  if (!slackState.connected || document.hidden && slackState.unreadSeen) return;
  try {
    const data = await api("/api/slack/unread");
    const next = data.items || {};
    const seen = slackState.unreadSeen;
    let fresh = false;
    for (const [cid, info] of Object.entries(next)) {
      if (seen && seen[cid] !== info.ts && cid !== slackState.channel) {
        fresh = true;
        showSlackDesktopNotification(info);
      }
    }
    slackState.unreadSeen = Object.fromEntries(Object.entries(next).map(([cid, info]) => [cid, info.ts]));
    slackState.unread = next;
    updateSlackNavBadge();
    if (currentRoute().view === "chat" && !$("#slack-app").classList.contains("hidden")) renderSlackConversations();
    if (fresh) {
      await refreshNotifications();
      playNotificationSound();
    }
  } catch (error) {
    console.warn("Slack unread check failed:", error.message);
  }
}

async function initSlackView() {
  const connect = $("#slack-connect");
  const app = $("#slack-app");
  const disconnect = $("#slack-disconnect");
  stopSlackPolling();
  let st;
  try {
    st = await api("/api/slack/status");
  } catch (error) {
    connect.classList.remove("hidden");
    app.classList.add("hidden");
    connect.innerHTML = `<p class="form-error">Could not load Slack status: ${esc(error.message)}</p>`;
    return;
  }
  slackState.connected = st.connected;
  slackState.teamName = st.team_name || "";
  disconnect.classList.toggle("hidden", !st.connected);
  if (!st.connected) {
    app.classList.add("hidden");
    connect.classList.remove("hidden");
    connect.innerHTML = st.configured
      ? `<h3>Connect your Slack</h3><p class="muted">Sign in with Slack once to read and send messages here. Your connection is private to you.</p><a class="btn-connect" href="/api/slack/auth">Connect Slack</a>`
      : `<h3>Slack isn't set up yet</h3><p class="muted">An admin needs to add <code>SLACK_CLIENT_ID</code> and <code>SLACK_CLIENT_SECRET</code> to the server <code>.env</code> (see README), then restart.</p>`;
    return;
  }
  connect.classList.add("hidden");
  app.classList.remove("hidden");
  $("#slack-team").textContent = slackState.teamName || "Slack";
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
  $("#slack-conversations").innerHTML = `<p class="muted">Loading…</p>`;
  try {
    slackState.conversations = await api("/api/slack/conversations");
    renderSlackConversations();
  } catch (error) {
    $("#slack-conversations").innerHTML = `<p class="form-error">${esc(error.message)}</p>`;
    return;
  }
  pollSlackUnread();
  slackState.timer = setInterval(() => {
    if (currentRoute().view === "chat" && !document.hidden) {
      loadSlackMessages();
      loadSlackThread();
    }
  }, 5000);
}

async function startSlackBackgroundPolling() {
  try {
    const st = await api("/api/slack/status");
    slackState.connected = st.connected;
  } catch (error) {
    return;
  }
  pollSlackUnread();
  slackState.pollTimer = setInterval(pollSlackUnread, 15000);
}

function bindSlackHandlers() {
  $("#slack-filter")?.addEventListener("input", renderSlackConversations);
  $("#slack-conversations")?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-conv]");
    if (btn) selectSlackConversation(btn.dataset.conv, btn.dataset.name);
  });
  const messageClicks = (event) => {
    const thread = event.target.closest("[data-thread]");
    if (thread) return openSlackThread(thread.dataset.thread);
    const chip = event.target.closest("[data-react]");
    if (chip) return toggleSlackReaction(chip.dataset.ts, chip.dataset.react, chip.dataset.me === "1");
    const add = event.target.closest("[data-add-react]");
    if (add) {
      event.stopPropagation();
      openEmojiPicker(add, (name) => toggleSlackReaction(add.dataset.addReact, name, false));
    }
  };
  $("#slack-messages")?.addEventListener("click", messageClicks);
  $("#slack-thread-messages")?.addEventListener("click", messageClicks);
  document.querySelectorAll("[data-emoji]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      const input = $(btn.dataset.emoji === "thread" ? "#slack-thread-input" : "#slack-input");
      if (input.disabled) return;
      openEmojiPicker(btn, (name, char) => insertAtCursor(input, char));
    });
  });
  const picker = $("#emoji-picker");
  picker?.addEventListener("click", (event) => {
    event.stopPropagation();
    const btn = event.target.closest("[data-emoji-name]");
    if (!btn) return;
    const handler = emojiPickerHandler;
    closeEmojiPicker();
    handler?.(btn.dataset.emojiName, btn.dataset.char);
  });
  picker?.addEventListener("input", (event) => {
    if (event.target.classList.contains("emoji-search")) filterEmojiPicker(event.target.value);
  });
  document.addEventListener("click", closeEmojiPicker);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeEmojiPicker();
  });
  let attachTarget = "main";
  document.querySelectorAll("[data-attach]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!slackState.channel) return setStatus("Select a conversation first.");
      attachTarget = btn.dataset.attach;
      $("#slack-file-input").click();
    });
  });
  $("#slack-file-input")?.addEventListener("change", (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    uploadSlackFile(file, attachTarget);
  });
  [["#slack-input", "main"], ["#slack-thread-input", "thread"]].forEach(([selector, target]) => {
    $(selector)?.addEventListener("paste", (event) => {
      const file = Array.from(event.clipboardData?.files || [])[0];
      if (file) {
        event.preventDefault();
        uploadSlackFile(file, target);
      }
    });
  });
  $("#slack-app")?.addEventListener("dragover", (event) => event.preventDefault());
  $("#slack-app")?.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    event.preventDefault();
    uploadSlackFile(file, event.target.closest("#slack-thread-panel") ? "thread" : "main");
  });
  $("#slack-thread-close")?.addEventListener("click", closeSlackThread);
  $("#slack-thread-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("#slack-thread-input");
    const text = input.value.trim();
    if (!text || !slackState.threadTs) return;
    input.disabled = true;
    try {
      await api("/api/slack/messages", {
        method: "POST",
        body: JSON.stringify({ channel: slackState.channel, text, thread_ts: slackState.threadTs }),
      });
      input.value = "";
      await loadSlackThread({ scroll: true });
      await loadSlackMessages();
    } catch (error) {
      setStatus(`Could not send reply: ${error.message}`);
    } finally {
      input.disabled = false;
      input.focus();
    }
  });
  $("#slack-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("#slack-input");
    const text = input.value.trim();
    if (!text || !slackState.channel) return;
    input.disabled = true;
    try {
      await api("/api/slack/messages", {
        method: "POST",
        body: JSON.stringify({ channel: slackState.channel, text }),
      });
      input.value = "";
      await loadSlackMessages({ scroll: true });
    } catch (error) {
      setStatus(`Could not send message: ${error.message}`);
    } finally {
      input.disabled = false;
      input.focus();
    }
  });
  $("#slack-disconnect")?.addEventListener("click", async () => {
    if (!confirm("Disconnect your Slack account from this workspace?")) return;
    try {
      await api("/api/slack/disconnect", { method: "POST" });
      slackState.channel = "";
      slackState.unread = {};
      slackState.unreadSeen = null;
      updateSlackNavBadge();
      await initSlackView();
    } catch (error) {
      setStatus(`Could not disconnect: ${error.message}`);
    }
  });
}

function localDateValue(date = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function formatClock(value) {
  const date = parseUtc(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function loadTimeLog() {
  const dateInput = $("#timelog-date");
  const userSelect = $("#timelog-user");
  if (!dateInput || !userSelect) return;
  if (!dateInput.value) dateInput.value = localDateValue();
  if (!userSelect.options.length) {
    if (!teamMembers.length) teamMembers = await api("/api/team/members");
    const people = currentCanManage ? teamMembers : teamMembers.filter((m) => m.id === currentUserId);
    userSelect.innerHTML = (currentCanManage ? `<option value="all">Everyone</option>` : "") + people
      .map((m) => `<option value="${esc(m.id)}">${esc(m.display_name)}${m.id === currentUserId ? " (me)" : ""}</option>`)
      .join("");
    userSelect.value = currentUserId;
    userSelect.disabled = !currentCanManage;
  }
  const [y, m, d] = dateInput.value.split("-").map(Number);
  const dayStart = new Date(y, m - 1, d);
  const dayEnd = new Date(y, m - 1, d + 1);
  const report = await api(
    `/api/time/report?start=${encodeURIComponent(dayStart.toISOString())}&end=${encodeURIComponent(dayEnd.toISOString())}&user_id=${encodeURIComponent(userSelect.value)}`
  );
  renderTimeLog(report);
}

function renderTimeLog(report) {
  const userName = $("#timelog-user")?.selectedOptions[0]?.textContent.replace(" (me)", "") || "me";
  const anyRunning = report.entries.some((e) => e.running);
  $("#timelog-summary").innerHTML = `
    <div class="summary-item"><strong>${esc(formatDuration(report.worked_seconds))}</strong><span>Time worked${anyRunning ? " (timer running)" : ""}</span></div>
    <div class="summary-item"><strong>${report.tasks.length}</strong><span>Tasks worked on</span></div>
    <div class="summary-item"><strong>${esc(formatDuration(report.total_seconds))}</strong><span>Total task time</span></div>`;
  $("#timelog-tasks").innerHTML = report.tasks.length
    ? `<table class="timelog-table"><thead><tr><th>Task</th><th>Brand</th><th>Who</th><th>Status</th><th>Time</th><th>Sessions</th></tr></thead><tbody>${report.tasks
        .map(
          (t) => `<tr><td>${esc(t.task_title)}${t.running ? ` <span class="task-timer running">running</span>` : ""}</td><td>${esc(t.business_name)}</td><td>${esc((t.people || []).join(", "))}</td><td>${esc(({ open: "To Do", in_progress: "In Progress", waiting_on: "Review", blocked: "Review", delegated: "In Progress", done: "Completed", cancelled: "Completed" })[t.task_status] || t.task_status)}</td><td><strong>${esc(formatDuration(t.seconds))}</strong></td><td>${t.sessions}</td></tr>`
        )
        .join("")}</tbody></table>`
    : `<p class="muted">No time tracked on this day. Move a card to In Progress to start a timer.</p>`;
  $("#timelog-entries").innerHTML = report.entries.length
    ? report.entries
        .map(
          (e) => `<div class="timelog-entry"><span class="timelog-range">${esc(formatClock(e.started_at))} – ${e.running ? "now" : esc(formatClock(e.ended_at))}</span><span>${esc(e.task_title)} <span class="muted">· ${esc(e.business_name)} · ${esc(e.user_name)}</span></span><strong>${esc(formatDuration(e.seconds))}</strong></div>`
        )
        .join("")
    : "";
  const date = $("#timelog-date").value;
  timelogSummaryText = [
    `Work update for ${userName} on ${date}`,
    `Time worked: ${formatDuration(report.worked_seconds)}`,
    ...report.tasks.map((t) => `- ${t.task_title} (${t.business_name}${t.people?.length ? `, ${t.people.join(", ")}` : ""}): ${formatDuration(t.seconds)}`),
  ].join("\n");
}

async function applyRoute() {
  let route = currentRoute();
  if (!routeAllowedForRole(route)) {
    route = { view: "home" };
    setRoute("home", { replace: true });
  }
  applyRouteView(route);
  if (route.view === "timelog") loadTimeLog().catch((error) => setStatus(error.message));
  if (route.view === "chat") initSlackView();
  else stopSlackPolling();
  window.TSLWorkspace?.onRoute(route);
  if (route.view === "brand" && route.businessId) {
    await openBoard(route.businessId);
    applyRouteView(route);
  }
}

async function initialize() {
  initSidebarState();
  attachGlobalHandlers();
  bindSlackHandlers();
  bindBrandLinks();
  bindBrandTabs();
  bindGlobalSearch();
  $("#timelog-date")?.addEventListener("change", () => loadTimeLog().catch((error) => setStatus(error.message)));
  $("#timelog-user")?.addEventListener("change", () => loadTimeLog().catch((error) => setStatus(error.message)));
  $("#timelog-copy")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(timelogSummaryText);
      setStatus("Summary copied.");
    } catch {
      setStatus("Could not copy summary.");
    }
  });
  window.addEventListener("hashchange", () => {
    applyRoute().catch((error) => setStatus(error.message));
  });
  window.addEventListener("pointerdown", unlockNotificationAudio);
  window.addEventListener("keydown", unlockNotificationAudio);
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "TEXTAREA") {
      event.preventDefault();
      $("#global-search")?.focus();
    }
  });

  await refreshSession();
  await refreshAll();
  await applyRoute();

  if (new URLSearchParams(location.search).get("google") === "connected") {
    setStatus("Google connected.");
    window.history.replaceState({}, "", location.hash || "#home");
  }
  const slackParams = new URLSearchParams(location.search);
  const slackResult = slackParams.get("slack");
  if (slackResult) {
    const reason = slackParams.get("reason");
    setStatus(slackResult === "connected" ? "Slack connected." : `Slack connection failed${reason ? `: ${reason}` : ""}. Please try again.`);
    window.history.replaceState({}, "", location.pathname + (location.hash || "#chat"));
  }
  const linkedTaskId = new URLSearchParams(location.search).get("task");
  if (linkedTaskId) {
    try {
      window.history.replaceState({}, "", location.pathname + (location.hash || "#home"));
      await window.TSLWorkspace.openTask(linkedTaskId);
    } catch (error) {
      setStatus(`Could not open linked task: ${error.message}`);
    }
  }

  startSlackBackgroundPolling();
  window.setInterval(() => {
    refreshNotifications().catch((error) => {
      console.warn("Notification refresh failed:", error.message);
    });
  }, 10_000);
}

initialize().catch((error) => {
  setStatus(error.message);
});
