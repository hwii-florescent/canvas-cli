# Canvas CLI Setup Summary

## Goal

We created a simple local CLI tool for UF Canvas so agents can fetch:

- Assignment name
- Course name
- Due date
- Assignment instructions
- Attached Canvas file metadata
- Submission status
- Assignment URL
- File submission support for online-upload assignments

The goal was to keep the setup simple and avoid building a full MCP server, database, or background polling system.

---

## What We Built

We created a command-line tool called:

```bash
canvas
```

The CLI uses a saved UF Canvas browser session for read-only Canvas API
requests, authenticated file downloads, and explicitly confirmed file
submissions.

This avoids needing a Canvas API token.

---

## Authentication

Before the first assignment fetch, configure the local username and
Keychain item:

```bash
canvas auth setup
```

The command prompts for the GatorLink username, then lets macOS
`/usr/bin/security` prompt for the UF password. The config file stores the
trimmed username and persistent student mode at:

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
Duo verification code: 455; enter it in Duo Mobile.
Duo verification succeeded.
```

For UF Verified Duo Push, the second line contains the three-digit code
currently shown by the headless login page. Enter that exact code in the
Duo Mobile notification. The CLI waits up to 180 seconds, then verifies
the authenticated Canvas API session and fetches assignments in the same
headless browser context. It never opens a headed browser, requests
passcodes or calls, or retries the primary password or push.
Missing credentials, a rejected password, or an unavailable Duo Push
returns a setup/authentication error instead.

The terminal also shows a live loading bar during authentication,
course loading, and assignment loading. Status messages are written to
stderr, so `--json` output remains valid JSON on stdout.

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

### Student enrollment mode

The default persistent student mode is `on`, so ordinary `canvas` commands
only include active courses where your Canvas enrollment type is `student`.
Change the mode with:

```bash
canvas student off
canvas student on
canvas student status
```

`canvas student off` persists until `canvas student on` is run. With the mode
off, assignment and course-list requests include all active enrollment types.
These mode commands only read or update local configuration; they do not fetch
Canvas data.

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

By default, only active courses where your Canvas enrollment type is `student`
are listed. When student mode is off, all active enrollment types are included.

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

### Explore course navigation

List the sections available in a course, including their clickable Canvas URLs:

```bash
canvas course 574892
canvas course 574892 --json
```

The navigation response is course- and user-specific. It contains only the
sections Canvas exposes for that course, so a course without Files, Grades, or
another feature will not list that section. Common sections include Home,
Announcements, Assignments, Discussions, Modules, Pages, and course-specific
tools. External links are displayed but are not fetched by the CLI.

### Read a specific Canvas resource or course section

Use `canvas fetch` with a Canvas URL when you need one resource instead of the
upcoming-assignment list:

```bash
canvas fetch https://ufl.instructure.com/courses/570905/quizzes/1650213
canvas fetch https://ufl.instructure.com/courses/574892/pages/project-setup-intellij-slash-gradle
canvas fetch https://ufl.instructure.com/courses/575787/files
canvas fetch https://ufl.instructure.com/courses/574892/announcements
```

Assignment, quiz, page, and file URLs use Canvas's authenticated APIs. Other
same-host URLs under `/courses/COURSE_ID/`, including announcements, home,
modules, discussions, and grades, are opened in the authenticated headless
browser and converted to readable text. This remains read-only.

Download those attached Canvas files to a local directory with:

```bash
canvas fetch \
  https://ufl.instructure.com/courses/570905/quizzes/1650213 \
  --download-dir ./canvas-files \
  --json
```

The download uses the authenticated browser session and only performs
read-only `GET` requests against `ufl.instructure.com`; it does not submit or
modify anything in Canvas. `--download-dir` also works with ordinary
assignment-list commands, fetched pages/sections, and course `/files` URLs.
For a file listing, each listed file is downloaded. External links are
displayed but are not downloaded. If filenames collide, a numeric suffix such
as `(2)` is added instead of overwriting an existing file.

### Submit files to an assignment

The CLI supports Canvas `online_upload` submissions only. It does not submit
text, URLs, or other submission formats.

Preview a file submission without uploading or changing Canvas:

```bash
canvas submit \
  https://ufl.instructure.com/courses/570905/assignments/123456 \
  --file ./report.pdf \
  --dry-run \
  --json
```

Submit one or more files:

```bash
canvas submit \
  https://ufl.instructure.com/courses/570905/assignments/123456 \
  --file ./report.pdf
```

Repeat `--file` for additional files. Before any upload, the CLI validates
that the local files exist, the assignment accepts file uploads, and the file
extensions are allowed. It then displays the exact assignment and files and
requires typing `SUBMIT`. No Canvas write request occurs before that
confirmation.

Quiz URLs are rejected by `canvas submit`. The CLI can read quiz instructions
and download quiz attachments, but it does not start or answer quizzes. Use
the Canvas quiz page in Chrome/Chromium for that workflow.

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
    "attachments": [],
    "url": "https://ufl.instructure.com/courses/...",
    "submission_status": "unsubmitted"
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
JSON assignment output. The remaining course, title, due-date, URL, and
attachments fields are unchanged. Combine it with `--course`, `--status`, and
`--days` as needed.

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
Read-only Canvas API requests, authenticated downloads, and confirmed file submissions
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

This file contains the GatorLink username and persistent student mode and must remain mode `0600`.

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
