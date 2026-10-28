# Office Attendance

Office attendance is stored separately from task time entries. A session is
created by an explicit check-in, and only its persisted active intervals
contribute to confirmed attendance totals. Check-out closes the current
interval; a paused session can be checked out without counting the unknown gap.

## Tracking behavior

The browser sends authenticated heartbeats every 45 seconds by default. The
server, not the browser clock, timestamps check-ins, check-outs, and heartbeats.
If heartbeats stop for more than the configured inactivity timeout (180 seconds
by default), the server closes the confirmed interval at the last received
heartbeat and records an `unknown_tracking_gap` interruption. A new heartbeat
starts a new interval. Repeated heartbeat request IDs are idempotent.

The default organization timezone is `Asia/Karachi`; working hours, working
days, late-arrival grace, heartbeat timing, and optional overtime calculation
can be configured by a Super Admin on the Office Attendance page.

This deployment is browser-only. It does not have an operating-system tracking
agent and therefore cannot prove computer sleep, shutdown, physical office
presence, or the cause of an unexplained tracking gap. Gaps are labeled
uncertain instead of being called sleep events. A fresh heartbeat confirms that
the browser has become active again, not that the employee is physically in the
office. Exact OS sleep/wake detection would require a separately installed,
secure desktop agent.

## Access and administration

- Employees can check in/out and read their own attendance reports.
- Admins can read attendance for themselves and active members of their
  department scope.
- Super Admins can view organization-wide records, configure policy, resolve
  exceptions, add reason-required attendance corrections, and review the
  attendance audit history.
- Manual corrections are additive adjustments; the original interval records
  remain intact. Each adjustment and exception resolution is audited.
- Monthly CSV exports contain persisted report data and respect the same
  server-side authorization checks as the report API.

Leave, holiday, and approved-break records are not available in the existing
workspace data model, so reports explicitly mark those inputs unavailable
rather than inferring them. Overtime is zero unless enabled in the attendance
policy.

## API

- `GET /api/attendance/status`
- `POST /api/attendance/check-in`
- `POST /api/attendance/heartbeat`
- `POST /api/attendance/check-out`
- `GET /api/attendance/report?month=YYYY-MM`
- `GET /api/attendance/policy`
- Super Admin only: `PUT /api/attendance/policy`
- Super Admin only: attendance corrections, exception resolution, and audit
  history under `/api/attendance`.

The attendance tables and indexes are created idempotently by the existing
SQLite initialization/migration path. No task-time records are modified.
