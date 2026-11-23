# Changelog

## 0.1.0 — 2026-11-23

First release.

- Prompt notifications for approvals, clarify questions and sudo password prompts,
  using the redacted `on_human_input_request` prompt. Critical urgency by default.
- The prompt notification closes automatically once it is answered
  (`on_human_input_resolved` plus `CloseNotification`).
- "Task complete" and "task stopped" notifications for turns longer than
  `completion_min_seconds` (default 30 s). User interrupts are ignored.
- Subagent sessions are skipped. Cron is skipped by default.
- If the session bus address is missing, the plugin falls back to the standard
  per-user socket, so gateway-run sessions also notify.
