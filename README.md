# Canvas CLI Setup Summary

## Goal

We created a simple local CLI tool for UF Canvas so agents can fetch:

- Assignment name
- Course name
- Due date
- Assignment instructions
- Submission status
- Assignment URL

The goal was to keep the setup simple and avoid building a full MCP server, database, or background polling system.

---

## What We Built

We created a command-line tool called:

```bash
canvas
```

The CLI uses a saved UF Canvas browser session and makes read-only Canvas API requests from that authenticated session.

This avoids needing a Canvas API token.

---

## Authentication

Before the first assignment fetch, configure the local username and
Keychain item:

```bash
canvas auth setup
```

The command prompts for the GatorLink username, then lets macOS
`/usr/bin/security` prompt for the UF password. The config file contains
only the trimmed username at:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/canvas-cli/config.json
```

The config directory is mode `0700` and the file is mode `0600`. The UF
password is stored in the macOS Keychain under service `canvas-cli.ufl`
with label `UF Canvas CLI`; it is never written to the config file or
passed as a command-line argument. Setup reads the Keychain item once
before it exits so any macOS access approval happens during setup.

Every assignment fetch runs Chromium headlessly. If the saved Canvas
session is valid, no login work is needed. If the session has expired,
the CLI navigates to the UF SAML login, fills the stored primary
credentials, and submits once. When UF requires MFA, it sends one Duo
Push and prints:

```text
Duo Push sent; approve it in Duo Mobile.
```

Approve that notification in Duo Mobile. The CLI waits up to 180 seconds,
then verifies the authenticated Canvas API session and fetches assignments
in the same headless browser context. It never opens a headed browser,
requests passcodes or calls, or retries the primary password or push.
Missing credentials, a rejected password, or an unavailable Duo Push
returns a setup/authentication error instead.

---

## Private Environment

The CLI does **not** install Playwright into the global Python environment.

Instead, it creates and manages its own private environment under:

```text
~/.local/share/canvas-cli/
```

This directory contains approximately:

```text
~/.local/share/canvas-cli/
├── venv/
├── ms-playwright/
├── canvas.py
└── .installed-v2
```

The private environment contains:

- Python virtual environment
- Playwright
- Chromium used by Playwright
- The Canvas Python implementation

---

## CLI Installation Location

The executable is installed at:

```text
~/.local/bin/canvas
```

`~/.local/bin` should be included in the shell `PATH`.

For zsh:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

This can be stored in:

```text
~/.zshrc
```

---

## What the CLI Returns

A normal call:

```bash
canvas
```

produces human-readable output similar to:

```text
1. Pharmacology Quiz 2
   Course: NUR3145
   Due: Tue, Sep 01, 2026 at 11:59 PM EDT
   Status: unsubmitted
   URL: https://ufl.instructure.com/courses/...

   Instructions:
     Complete Chapters 1–3...
     You have 30 minutes...
```

---

## Commands

### Show upcoming assignments

```bash
canvas
```

The default look-ahead window is 14 days. Day-window filters (`--days 14`,
`--days 7`, etc.) only return assignments with an upcoming deadline.

---

### Assignments due in the next 7 days

```bash
canvas --days 7
```

---

### Assignments due in the next 14 days

```bash
canvas --days 14
```

---

### Assignments due in the next 30 days

```bash
canvas --days 30
```

---

### Show all future assignments and assignments without due dates

```bash
canvas --days all
```

`--days all` (or `--days 0`) returns all future assignments as well as
assignments with no deadline (`due_at: null`, `due_display: "No due date"`).
Assignments without due dates do not appear in day-window queries like
`--days 7` or `--days 14`.

---
### Filter assignments by course

Run `--course` without a value to open an interactive terminal picker:

```bash
canvas --course
```

The picker lists active courses sorted by code/name. Use Up/Down (or `j`/`k`)
to move, Space to toggle multiple courses, and Enter to apply. Press `q`
or Escape to cancel. The selected courses are used to filter assignments.

The picker UI is written to stderr, so `canvas --course --json` keeps the
assignment or course JSON on stdout.

The picker uses the available terminal height as a scrolling viewport, so
long course lists stay compact. Long labels are shortened to fit the terminal
width. After Enter, it shows a loading screen, stops reading picker keys, and
discards queued input before displaying results.

For scripts or exact non-interactive selection, provide a course code or
name directly:

```bash
canvas --course NUR3145
canvas --course "Pharmacology"

Repeat `--course` to include more than one course:

```bash
canvas --course NUR3145 --course BIO101
```

### List active courses

List the active courses available for filtering:

```bash
canvas --list-courses
canvas --list-courses --json
```

Human-readable output includes the course code and name. JSON output is a
list of objects with `id`, `course_code`, and `name`. `--course` can also
be combined with `--list-courses` to narrow the list.

### Filter by submission status

The default status filter is `unsubmitted`, so ordinary assignment calls
show only assignments whose `submission_status` is exactly `unsubmitted`.

```bash
canvas --status unsubmitted
canvas --status submitted
canvas --status graded
canvas --status all
```

`--status submitted` shows only assignments with status `submitted`.
`--status graded` shows only assignments with status `graded`.
`--status all` includes every status returned by Canvas, including
`submitted`, `graded`, `missing`, `late`, and `unsubmitted`.

---

### JSON output

```bash
canvas --json
```

This is the recommended format for AI agents.

Example:

```json
[
  {
    "course": "NUR3145",
    "course_name": "Pharmacology",
    "title": "Quiz 2",
    "due_at": "2026-09-01T23:59:00-04:00",
    "due_display": "Tue, Sep 01, 2026 at 11:59 PM EDT",
    "instructions": "Complete Chapters 1-3...",
    "url": "https://ufl.instructure.com/courses/...",
    "submission_status": "unsubmitted"
  }
]
```

---

### Short assignment output

Use `--shorten` when only assignment identity, course, deadline, and URL are
needed:

```bash
canvas --shorten
canvas --shorten --json
```

This omits `instructions` and `submission_status` from both human-readable and
JSON assignment output. The remaining course, title, due-date, and URL fields
are unchanged. Combine it with `--course`, `--status`, and `--days` as needed.

---

### Combine JSON with a date range

```bash
canvas --days 7 --json
```

This is useful for an agent that only needs assignments due during the next week.

---

### Change timezone

The default timezone is:

```text
America/New_York
```

You can override it:

```bash
canvas --timezone America/Chicago
```

---

### Show help

```bash
canvas --help
```

---

## Recommended Agent Usage

For AI agents, use:

```bash
canvas --json
```

or:

```bash
canvas --days 14 --json
```

An agent can run the command whenever it needs to answer questions such as:

- What assignments do I have this week?
- What is due tomorrow?
- What are the instructions for my upcoming assignments?
- Which assignments are still unsubmitted?
- What deadlines are coming up?

The agent does not need Canvas credentials in its prompt or configuration.

---

## Current Architecture

```text
Agent / Terminal
      |
      v
   canvas CLI
      |
      v
Private Python + Playwright environment
      |
      v
Saved Chromium Canvas login
      |
      v
UF Canvas
      |
      v
Read-only Canvas API requests
```

There is currently:

- No MCP server
- No database
- No global Python dependency installation
- No background polling service
- No Canvas API token

The CLI only fetches information when the `canvas` command is run.

---

## Important Files

### CLI executable

```text
~/.local/bin/canvas
```

### Private app data

```text
~/.local/share/canvas-cli/
```

### Credential configuration

```text
${XDG_CONFIG_HOME:-$HOME/.config}/canvas-cli/config.json
```

This file contains only the GatorLink username and must remain mode `0600`.

### Saved browser login profile

The underlying Canvas script uses a persistent browser profile located at:

```text
~/.canvas-agent-browser
```

This directory contains the saved Chromium session used to remain logged into UF Canvas.

It should be treated as private and should not be committed to Git or shared with other people.

---

## Security Notes

Do not commit or share:

```text
~/.canvas-agent-browser
```

or the private Canvas CLI application data.

The local credential config stores only the GatorLink username. The UF
password is kept in the macOS Keychain as the generic password item with
service `canvas-cli.ufl` and label `UF Canvas CLI`; the CLI retrieves it
only in memory when a saved Canvas session needs to be renewed. Do not
copy the password into shell history, files, prompts, logs, or agent
configuration.

The persistent browser profile remains private because it contains the
authenticated Canvas session.

---

## Possible Future Improvements

The CLI is intentionally simple right now.

Possible additions later include commands such as:

```bash
canvas today
canvas week
canvas all
canvas overdue
```

For the current use case, the existing flags are enough and keep the tool lightweight.
