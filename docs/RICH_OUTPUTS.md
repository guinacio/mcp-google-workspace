# Rich list and detail outputs

Read-oriented tools return compact, action-ready envelopes by default. Raw provider payloads are intentionally not the standard list response; body-heavy tools provide an explicit full mode where appropriate.

| Service | List/detail output | Derived value |
| --- | --- | --- |
| Gmail | Sender identity, category, clean snippets, unread/attachment/newsletter/automation flags | `get_mail_digest`, `check_mail_updates`, and `read_emails` |
| Chat | Person-aware message authors, DM peers, member list, thread and attachment state | Cached People lookups, bounded to 10 concurrent resolutions |
| Tasks | Title, due date, overdue state, note preview, hierarchy and lifecycle state | `tasks_digest` groups overdue, upcoming, and unscheduled tasks |
| Drive | File kind, owner/modifier, sharing and lifecycle state, capability flags | No additional per-file requests; parent paths remain IDs to avoid fan-out |
| Calendar | Time window, organizer, attendee/RVSP state, meeting link, recurrence and attachment metadata | `get_calendar_digest` groups events requiring a response |
| Meet | Conference lifecycle, named participant identity, recording and transcript destinations | Uses identity data already included by Meet |
| Forms | Answers with question titles rather than opaque question IDs | One form-schema lookup per enriched response request |
| Keep | Preview text, checklist completion, attachment count and lifecycle timestamps | Detail reads retain full note text |

All compact representations preserve stable provider IDs for follow-up tools.
