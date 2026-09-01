---
name: canvas-cli
description: Use when an agent needs to inspect UF Canvas courses, list course navigation, read assignments, quizzes, pages, files, or other course sections, download Canvas files, or prepare an assignment file submission.
version: 1.0.0
---

# Canvas CLI

Use the installed `canvas` command instead of rediscovering Canvas endpoints or browser flows.

## Fast path

```bash
canvas --help
canvas course COURSE_ID --json
canvas fetch CANVAS_URL --json
```

The installed command is `$HOME/.local/bin/canvas`. JSON goes to stdout; authentication and progress messages go to stderr.

## Authentication and course mode

The first real operation may require setup and UF/MFA authentication:

```bash
canvas auth setup
canvas student status
```

Student mode defaults to `on`, which limits ordinary course listing and assignment queries to active student enrollments. `canvas course COURSE_ID` uses the supplied numeric course ID and lists the tabs Canvas exposes for that course and user.

## Discover course sections

```bash
canvas course 574892
canvas course 574892 --json
```

The result is dynamic. Do not assume a course has Files, Grades, Discussions, Modules, or any other tab. Use the returned `sections[*].url` values as the source of truth. External tool links may be displayed, but the CLI does not fetch them while listing.

## Read course content

`fetch` accepts assignment, quiz, page, file, and same-host course-section URLs:

```bash
canvas fetch https://ufl.instructure.com/courses/574892/pages/PAGE_SLUG --json
canvas fetch https://ufl.instructure.com/courses/575787/files --json
canvas fetch https://ufl.instructure.com/courses/574892/announcements --json
canvas fetch https://ufl.instructure.com/courses/574892/modules --json
```

Assignment, quiz, page, and file targets use authenticated Canvas API reads. Other URLs under `/courses/COURSE_ID/` are opened in the authenticated headless browser and converted to readable text. Only same-host course URLs are accepted; external redirects and redirects to another course are blocked.

Download Canvas file links or a course file listing with:

```bash
canvas fetch CANVAS_URL --download-dir ./canvas-files --json
```

For a `/files` target, every listed file is downloaded. For a page or other section, Canvas file links found in the readable content are downloaded. External links are never downloaded.

For the upcoming assignment list:

```bash
canvas --json
canvas --days all --json
canvas --course COURSE_CODE --json
canvas --status all --json
```

## Quiz limitation

The CLI can read quiz instructions and attachments, but it does not start or answer quizzes. Use the Canvas quiz page in Chrome/Chromium for interactive quiz work.

## Assignment file submissions

Only `online_upload` assignments are supported:

```bash
canvas submit ASSIGNMENT_URL \
  --file ./report.pdf \
  --dry-run --json
```

Repeat `--file` for multiple files. A real submission validates the assignment and files, then requires an interactive uppercase `SUBMIT` confirmation before any Canvas write. Never use a real assignment as a test target; use mocks and `--dry-run`.

## Maintaining the CLI

Source and tests are in the repository root:

```bash
./install-canvas-cli.sh
PLAYWRIGHT_BROWSERS_PATH=$HOME/.local/share/canvas-cli/ms-playwright \
  $HOME/.local/share/canvas-cli/venv/bin/python -m unittest test_canvas_cli
```

Run the installer after changing `canvas` because the test suite imports the installed copy from `$HOME/.local/share/canvas-cli/canvas.py`.
