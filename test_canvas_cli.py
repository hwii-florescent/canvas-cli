#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import fcntl
import importlib.util
from datetime import datetime, timedelta, timezone
import io
import json
import os
import pty
import stat
import sys
import tempfile
import termios
import struct
import unittest
from pathlib import Path
from unittest import mock


SOURCE = Path.home() / ".local/share/canvas-cli/canvas.py"
SPEC = importlib.util.spec_from_file_location("installed_canvas_cli", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load installed Canvas CLI: {SOURCE}")
canvas = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = canvas
SPEC.loader.exec_module(canvas)


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.config_root = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": self.config_root.name},
            clear=False,
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.config_root.cleanup()

    @staticmethod
    def completed(command, *, returncode=0, stdout=""):
        return canvas.subprocess.CompletedProcess(
            command,
            returncode,
            stdout=stdout,
            stderr="",
        )

    def test_setup_writes_username_only_config_with_private_modes(self):
        calls = []
        secret = "not-written-to-config"

        def run(command, **kwargs):
            calls.append((command, kwargs))
            if len(calls) == 1:
                return self.completed(command)
            return self.completed(command, stdout=secret + "\n")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(canvas.subprocess, "run", side_effect=run),
            mock.patch("builtins.input", return_value="  gator123  "),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(canvas.configure_auth(), 0)

        config_dir = Path(self.config_root.name) / "canvas-cli"
        config_path = config_dir / "config.json"
        self.assertEqual(stat.S_IMODE(config_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(config_path.read_text()), {"username": "gator123"})
        self.assertNotIn(secret, config_path.read_text())
        self.assertNotIn(secret, stdout.getvalue())
        self.assertNotIn(secret, stderr.getvalue())

        self.assertEqual(
            calls[0][0],
            [
                "/usr/bin/security",
                "add-generic-password",
                "-U",
                "-a",
                "gator123",
                "-s",
                "canvas-cli.ufl",
                "-l",
                "UF Canvas CLI",
                "-w",
            ],
        )
        self.assertEqual(calls[0][0][-1], "-w")
        self.assertEqual(
            calls[1][0],
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                "gator123",
                "-s",
                "canvas-cli.ufl",
                "-w",
            ],
        )
        self.assertEqual(calls[1][0][-1], "-w")
        self.assertEqual(calls[0][1], {"check": False})
        self.assertEqual(
            calls[1][1],
            {"check": False, "capture_output": True, "text": True},
        )

    def test_load_credentials_reads_keychain_without_disclosing_password(self):
        config_path = Path(self.config_root.name) / "canvas-cli/config.json"
        config_path.parent.mkdir(mode=0o700)
        config_path.write_text('{"username":"gator123"}\n')
        config_path.chmod(0o600)
        secret = "keychain-only-password"

        with mock.patch.object(
            canvas.subprocess,
            "run",
            return_value=self.completed([], stdout=secret + "\n"),
        ) as run:
            credentials = canvas.load_credentials()

        self.assertEqual(credentials.username, "gator123")
        self.assertEqual(credentials.password, secret)
        self.assertEqual(
            run.call_args.args[0],
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                "gator123",
                "-s",
                "canvas-cli.ufl",
                "-w",
            ],
        )
        self.assertNotIn(secret, str(run.call_args.args[0]))

    def test_missing_malformed_and_missing_keychain_credentials_fail_generically(self):
        with self.assertRaisesRegex(canvas.AuthError, canvas.CREDENTIALS_ERROR):
            canvas.load_credentials()

        config_path = Path(self.config_root.name) / "canvas-cli/config.json"
        config_path.parent.mkdir(mode=0o700, exist_ok=True)
        config_path.write_text("not json")
        config_path.chmod(0o600)
        with self.assertRaisesRegex(canvas.AuthError, canvas.CREDENTIALS_ERROR):
            canvas.load_credentials()

        config_path.write_text('{"username":"gator123"}\n')
        secret = "private-secret"
        failed = self.completed([], returncode=1, stdout=secret + "\n")
        with mock.patch.object(canvas.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(canvas.AuthError, canvas.CREDENTIALS_ERROR) as error:
                canvas.load_credentials()
        self.assertNotIn(secret, str(error.exception))

    def test_setup_updates_existing_username_config(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[1] == "add-generic-password":
                return self.completed(command)
            return self.completed(command, stdout="pw\n")

        with (
            mock.patch.object(canvas.subprocess, "run", side_effect=run),
            mock.patch("builtins.input", side_effect=["first", "second"]),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            canvas.configure_auth()
            canvas.configure_auth()

        config_path = Path(self.config_root.name) / "canvas-cli/config.json"
        self.assertEqual(json.loads(config_path.read_text()), {"username": "second"})
        self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0][4], "first")
        self.assertEqual(calls[2][4], "second")

    def test_invalid_username_does_not_touch_keychain(self):
        with mock.patch("builtins.input", return_value="bad username"), mock.patch.object(
            canvas.subprocess, "run"
        ) as run:
            with self.assertRaisesRegex(canvas.AuthError, canvas.CREDENTIALS_ERROR):
                canvas.configure_auth()
        run.assert_not_called()

    def test_nonregular_config_path_is_rejected(self):
        config_path = Path(self.config_root.name) / "canvas-cli/config.json"
        config_path.parent.mkdir(mode=0o700)
        config_path.mkdir(mode=0o600)
        responses = [
            self.completed([]),
            self.completed([], stdout="pw\n"),
        ]
        with (
            mock.patch.object(canvas.subprocess, "run", side_effect=responses),
            mock.patch("builtins.input", return_value="gator123"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaisesRegex(canvas.AuthError, canvas.CREDENTIALS_ERROR):
                canvas.configure_auth()




class CourseAndStatusTests(unittest.TestCase):
    def setUp(self):
        self.courses = [
            {"id": 2, "course_code": "BIO101", "name": "Biology"},
            {"id": 1, "course_code": "NUR3145", "name": "Pharmacology"},
        ]

    def test_course_filter_matches_code_or_name_case_insensitively(self):
        filters = canvas.normalize_course_filters([" nur3145 ", "biology"])
        selected = canvas.filter_courses(self.courses, filters)
        self.assertEqual([course["id"] for course in selected], [2, 1])
        self.assertEqual(canvas.filter_courses(self.courses, {"missing"}), [])

    def test_course_list_human_output_includes_code_and_name(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            canvas.print_courses(self.courses)
        self.assertEqual(output.getvalue().splitlines(), [
            "1. BIO101 — Biology",
            "2. NUR3145 — Pharmacology",
        ])

    def test_interactive_course_picker_selects_multiple_courses(self):
        output = io.StringIO()
        keys = iter(["down", "toggle", "up", "toggle", "confirm"])

        selected = canvas.select_courses(
            self.courses,
            read_key=lambda: next(keys),
            stdin=io.StringIO(),
            stdout=output,
        )

        self.assertEqual([course["id"] for course in selected], [2, 1])
        self.assertIn("Select courses", output.getvalue())


    def test_picker_confirm_renders_loading_screen(self):
        output = io.StringIO()
        keys = iter(["toggle", "confirm"])

        selected = canvas.select_courses(
            self.courses,
            read_key=lambda: next(keys),
            stdin=io.StringIO(),
            stdout=output,
        )

        self.assertEqual([course["id"] for course in selected], [2])
        loading_screen = output.getvalue().rsplit("\x1b[2J\x1b[H", 1)[-1]
        self.assertIn("Loading Canvas data...", loading_screen)
        self.assertIn("Processing 1 selected course.", loading_screen)

    def test_interactive_course_picker_defaults_ui_to_stderr(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        keys = iter(["toggle", "confirm"])

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            selected = canvas.select_courses(
                self.courses,
                read_key=lambda: next(keys),
                stdin=io.StringIO(),
            )

        self.assertEqual([course["id"] for course in selected], [2])
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Select courses", stderr.getvalue())

    def test_interactive_course_picker_checks_default_stderr_tty(self):
        stdin = mock.Mock()
        stdin.isatty.return_value = True
        stdout = mock.Mock()
        stdout.isatty.return_value = True
        stderr = mock.Mock()
        stderr.isatty.return_value = False

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaisesRegex(
                RuntimeError,
                "Interactive course selection requires a terminal",
            ):
                canvas.select_courses(self.courses, stdin=stdin)

    def test_interactive_course_picker_requires_a_terminal(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "Interactive course selection requires a terminal",
        ):
            canvas.select_courses(
                self.courses,
                stdin=io.StringIO(),
                stdout=io.StringIO(),
            )


    def test_picker_render_uses_line_endings_and_fits_terminal_width(self):
        long_course = [{
            "id": 3,
            "course_code": "COP9999",
            "name": "A Very Long Course Name " * 5,
        }]

        class TerminalBuffer(io.StringIO):
            def fileno(self):
                return 42

        output = TerminalBuffer()
        with mock.patch.object(
            canvas.os,
            "get_terminal_size",
            return_value=os.terminal_size((30, 8)),
        ):
            canvas._render_course_picker(long_course, 0, set(), output)

        rendered = output.getvalue()
        body = rendered.removeprefix("\x1b[2J\x1b[H")
        self.assertNotIn("\r", body)
        self.assertIn("\n", body)
        self.assertIn("...", body)
        self.assertTrue(
            all(
                canvas._terminal_cell_width(line) <= 29
                for line in body.split("\n")
            )
        )


    def test_picker_render_uses_terminal_height_viewport(self):
        courses = [
            {"id": index, "course_code": f"C{index}", "name": f"Course {index}"}
            for index in range(6)
        ]
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 6, 50, 0, 0))
        output = os.fdopen(os.dup(slave), "w", encoding="utf-8", newline="")
        try:
            canvas._render_course_picker(courses, 4, {4}, output)
            output.close()
            processed = os.read(master, 65536).decode("utf-8")
            os.close(slave)
        finally:
            if not output.closed:
                output.close()
            try:
                os.close(slave)
            except OSError:
                pass
            os.close(master)

        body = processed.removeprefix("\x1b[2J\x1b[H")
        lines = [line for line in body.split("\n") if line.strip()]
        course_lines = [line for line in lines if "[ ]" in line or "[x]" in line]
        self.assertEqual(len(course_lines), 3)
        self.assertIn("Showing 3-5 of 6", body)
        self.assertNotIn("C0 — Course 0", body)
        self.assertNotIn("C1 — Course 1", body)
        self.assertIn("C4 — Course 4", body)
        self.assertNotIn("C5 — Course 5", body)

    def test_picker_terminal_session_manages_alternate_screen_and_flushes(self):
        master, slave = pty.openpty()
        terminal_in = mock.Mock()
        terminal_in.fileno.return_value = slave
        terminal_in.isatty.return_value = True
        terminal_out = io.StringIO()
        terminal_out.isatty = lambda: True

        try:
            with mock.patch.object(canvas, "_flush_terminal_input") as flush:
                with canvas._picker_terminal_session(terminal_in, terminal_out):
                    self.assertIn("\x1b[?1049h\x1b[?25l", terminal_out.getvalue())
                flush.assert_called_once_with(terminal_in)

            self.assertIn("\x1b[?25h\x1b[?1049l", terminal_out.getvalue())
        finally:
            os.close(master)
            os.close(slave)

    def test_terminal_width_falls_back_when_pty_size_is_zero(self):
        stream = mock.Mock()
        stream.fileno.return_value = 42
        with mock.patch.object(
            canvas.os,
            "get_terminal_size",
            return_value=os.terminal_size((0, 0)),
        ):
            self.assertEqual(canvas._terminal_width(stream), 80)

    def test_cbreak_preserves_terminal_output_processing(self):
        master, slave = pty.openpty()
        before = termios.tcgetattr(slave)
        terminal = mock.Mock()
        terminal.fileno.return_value = slave
        try:
            with canvas._cbreak_terminal(terminal):
                current = termios.tcgetattr(slave)
                self.assertFalse(current[3] & termios.ICANON)
                self.assertFalse(current[3] & termios.ECHO)
                self.assertTrue(current[1] & termios.OPOST)
                self.assertTrue(current[1] & termios.ONLCR)
            restored = termios.tcgetattr(slave)
            self.assertEqual(
                restored[3] & (termios.ICANON | termios.ECHO),
                before[3] & (termios.ICANON | termios.ECHO),
            )
            self.assertEqual(
                restored[1] & (termios.OPOST | termios.ONLCR),
                before[1] & (termios.OPOST | termios.ONLCR),
            )
        finally:
            os.close(master)
            os.close(slave)

    def test_picker_render_lines_start_at_column_zero_through_pty(self):
        master, slave = pty.openpty()
        attributes = termios.tcgetattr(slave)
        attributes[1] |= termios.OPOST | termios.ONLCR
        termios.tcsetattr(slave, termios.TCSANOW, attributes)
        output = os.fdopen(
            os.dup(slave),
            "w",
            encoding="utf-8",
            newline="",
        )
        try:
            canvas._render_course_picker(
                [
                    {"id": 1, "course_code": "AAA", "name": "Alpha"},
                    {"id": 2, "course_code": "BBB", "name": "Beta"},
                ],
                0,
                set(),
                output,
            )
            output.close()
            processed = os.read(master, 65536).decode("utf-8")
            os.close(slave)
        finally:
            if not output.closed:
                output.close()
            try:
                os.close(slave)
            except OSError:
                pass
            os.close(master)

        body = processed.removeprefix("\x1b[2J\x1b[H")
        column = 0
        for char in body:
            if char == "\r":
                column = 0
            elif char == "\n":
                self.assertEqual(column, 0)
            else:
                column += canvas._terminal_char_width(char)
        self.assertIn("\n", body)
    def test_picker_render_sanitizes_course_control_characters(self):
        output = io.StringIO()
        course = [{
            "id": 3,
            "course_code": "COP\n9999",
            "name": "Name\x1b[31m",
        }]

        canvas._render_course_picker([course[0]], 0, set(), output)

        body = output.getvalue().removeprefix("\x1b[2J\x1b[H")
        self.assertNotIn("\n9999", body)
        self.assertNotIn("\x1b[31m", body)
    def test_assignment_status_filters_default_submitted_and_all(self):
        due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        assignments = [
            {"name": "unsubmitted", "due_at": due_at, "submission": {}},
            {
                "name": "submitted",
                "due_at": due_at,
                "submission": {"submitted_at": "2026-01-01T00:00:00Z"},
            },
            {
                "name": "graded",
                "due_at": due_at,
                "submission": {"workflow_state": "graded"},
            },
            {
                "name": "missing",
                "due_at": due_at,
                "submission": {"missing": True},
            },
            {
                "name": "late",
                "due_at": due_at,
                "submission": {"late": True},
            },
        ]
        course = [self.courses[0]]

        batch_return = [{"courseId": course[0]["id"], "ok": True, "items": assignments}]
        with mock.patch.object(canvas, "batch_fetch_course_assignments", return_value=batch_return):
            default_rows = canvas.fetch_assignments(
                object(),
                0,
                canvas.ZoneInfo("UTC"),
                courses=course,
            )
            submitted_rows = canvas.fetch_assignments(
                object(),
                0,
                canvas.ZoneInfo("UTC"),
                courses=course,
                status_filter="submitted",
            )
            graded_rows = canvas.fetch_assignments(
                object(),
                0,
                canvas.ZoneInfo("UTC"),
                courses=course,
                status_filter="graded",
            )
            all_rows = canvas.fetch_assignments(
                object(),
                0,
                canvas.ZoneInfo("UTC"),
                courses=course,
                status_filter="all",
            )

        self.assertEqual([row["title"] for row in default_rows], ["unsubmitted"])
        self.assertEqual([row["title"] for row in submitted_rows], ["submitted"])
        self.assertEqual([row["title"] for row in graded_rows], ["graded"])
        self.assertEqual(
            {row["title"] for row in all_rows},
            {"unsubmitted", "submitted", "graded", "missing", "late"},
        )

    def test_main_course_filter_and_status_are_passed_to_assignment_fetch(self):
        context = mock.Mock()
        context.pages = [object()]
        sync = mock.Mock()
        sync.__enter__ = mock.Mock(return_value=object())
        sync.__exit__ = mock.Mock(return_value=None)
        courses = [
            {"id": 1, "course_code": "NUR3145", "name": "Pharmacology"},
            {"id": 2, "course_code": "BIO101", "name": "Biology"},
        ]
        with tempfile.TemporaryDirectory() as profile, mock.patch(
            "sys.argv",
            ["canvas", "--course", "nur3145", "--status", "submitted", "--json"],
        ), mock.patch.object(canvas, "PROFILE_DIR", Path(profile)), mock.patch.object(
            canvas, "sync_playwright", return_value=sync
        ), mock.patch.object(canvas, "launch_context", return_value=context), mock.patch.object(
            canvas, "ensure_login"
        ), mock.patch.object(canvas, "fetch_courses", return_value=courses), mock.patch.object(
            canvas, "fetch_assignments", return_value=[]
        ) as fetch, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(canvas.main(), 0)

        self.assertEqual(fetch.call_args.kwargs["status_filter"], "submitted")
        self.assertEqual(fetch.call_args.kwargs["courses"], [courses[0]])
        context.close.assert_called_once()

    def test_main_lists_filtered_courses_as_json(self):
        context = mock.Mock()
        context.pages = [object()]
        sync = mock.Mock()
        sync.__enter__ = mock.Mock(return_value=object())
        sync.__exit__ = mock.Mock(return_value=None)
        courses = [
            {"id": 1, "course_code": "NUR3145", "name": "Pharmacology"},
            {"id": 2, "course_code": "BIO101", "name": "Biology"},
        ]
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as profile, mock.patch(
            "sys.argv",
            ["canvas", "--list-courses", "--course", "biology", "--json"],
        ), mock.patch.object(canvas, "PROFILE_DIR", Path(profile)), mock.patch.object(
            canvas, "sync_playwright", return_value=sync
        ), mock.patch.object(canvas, "launch_context", return_value=context), mock.patch.object(
            canvas, "ensure_login"
        ), mock.patch.object(canvas, "fetch_courses", return_value=courses), mock.patch.object(
            canvas, "fetch_assignments"
        ) as fetch, contextlib.redirect_stdout(output):
            self.assertEqual(canvas.main(), 0)

        self.assertEqual(
            json.loads(output.getvalue()),
            [{"id": 2, "course_code": "BIO101", "name": "Biology"}],
        )
        fetch.assert_not_called()

    def test_main_bare_course_opens_picker(self):
        context = mock.Mock()
        context.pages = [object()]
        sync = mock.Mock()
        sync.__enter__ = mock.Mock(return_value=object())
        sync.__exit__ = mock.Mock(return_value=None)
        courses = [
            {"id": 1, "course_code": "NUR3145", "name": "Pharmacology"},
            {"id": 2, "course_code": "BIO101", "name": "Biology"},
        ]
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as profile, mock.patch(
            "sys.argv",
            ["canvas", "--course", "--json"],
        ), mock.patch.object(canvas, "PROFILE_DIR", Path(profile)), mock.patch.object(
            canvas, "sync_playwright", return_value=sync
        ), mock.patch.object(canvas, "launch_context", return_value=context), mock.patch.object(
            canvas, "ensure_login"
        ), mock.patch.object(canvas, "fetch_courses", return_value=courses), mock.patch.object(
            canvas, "_picker_terminal_session"
        ) as session, mock.patch.object(
            canvas, "_select_ordered_courses", return_value=[courses[1]]
        ) as select, mock.patch.object(
            canvas, "fetch_assignments", return_value=[]
        ) as fetch, contextlib.redirect_stdout(output):
            self.assertEqual(canvas.main(), 0)

        session.assert_called_once_with(sys.stdin, sys.stderr)
        self.assertEqual(select.call_args.args[1:], (sys.stdin, sys.stderr))
        self.assertEqual(fetch.call_args.kwargs["courses"], [courses[1]])

    def test_main_picker_keeps_json_on_stdout_with_tty_stderr(self):
        context = mock.Mock()
        context.pages = [object()]
        sync = mock.Mock()
        sync.__enter__ = mock.Mock(return_value=object())
        sync.__exit__ = mock.Mock(return_value=None)
        courses = [
            {"id": 1, "course_code": "NUR3145", "name": "Pharmacology"},
            {"id": 2, "course_code": "BIO101", "name": "Biology"},
        ]
        assignment = {
            "course": "BIO101",
            "course_name": "Biology",
            "title": "Assignment",
            "due_at": None,
            "due_display": "No due date",
            "instructions": "Read the prompt.",
            "url": "https://ufl.instructure.com/courses/2/assignments/3",
            "submission_status": "unsubmitted",
        }
        stdout = io.StringIO()

        class TTYBuffer(io.StringIO):
            def isatty(self):
                return True

        stderr = TTYBuffer()
        events = []

        class TerminalSession:
            def __enter__(self):
                events.append("enter")

            def __exit__(self, exc_type, exc_value, traceback):
                events.append("exit")

        def open_terminal(stdin, output):
            self.assertIs(stdin, sys.stdin)
            self.assertIs(output, stderr)
            self.assertTrue(output.isatty())
            return TerminalSession()

        def choose_courses(ordered, stdin, output):
            events.append("select")
            self.assertEqual([course["id"] for course in ordered], [2, 1])
            self.assertIs(stdin, sys.stdin)
            self.assertIs(output, stderr)
            return [courses[1]]

        def fetch_selected(*args, **kwargs):
            events.append("fetch")
            self.assertEqual(kwargs["courses"], [courses[1]])
            return [assignment]

        with tempfile.TemporaryDirectory() as profile, mock.patch(
            "sys.argv",
            ["canvas", "--course", "--json"],
        ), mock.patch.object(canvas, "PROFILE_DIR", Path(profile)), mock.patch.object(
            canvas, "sync_playwright", return_value=sync
        ), mock.patch.object(canvas, "launch_context", return_value=context), mock.patch.object(
            canvas, "ensure_login"
        ), mock.patch.object(canvas, "fetch_courses", return_value=courses), mock.patch.object(
            canvas, "_picker_terminal_session", side_effect=open_terminal
        ), mock.patch.object(
            canvas, "_select_ordered_courses", side_effect=choose_courses
        ), mock.patch.object(
            canvas, "fetch_assignments", side_effect=fetch_selected
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(canvas.main(), 0)

        self.assertEqual(events, ["enter", "select", "fetch", "exit"])
        self.assertEqual(json.loads(stdout.getvalue()), [assignment])
    def test_normalize_days_handles_default_integer_and_all(self):
        self.assertEqual(canvas.normalize_days(None), 14)
        self.assertEqual(canvas.normalize_days("all"), 0)
        self.assertEqual(canvas.normalize_days(" ALL "), 0)
        self.assertEqual(canvas.normalize_days("0"), 0)
        self.assertEqual(canvas.normalize_days("7"), 7)
        self.assertEqual(canvas.normalize_days(14), 14)
        with self.assertRaises(ValueError):
            canvas.normalize_days("-5")
        with self.assertRaises(ValueError):
            canvas.normalize_days(-1)
        with self.assertRaises(ValueError):
            canvas.normalize_days("not-a-day")

    def test_fetch_assignments_includes_null_due_dates_only_for_days_all(self):
        now = datetime.now(timezone.utc)
        assignments = [
            {
                "name": "No deadline task",
                "due_at": None,
                "submission": {},
            },
            {
                "name": "Due in 5 days",
                "due_at": (now + timedelta(days=5)).isoformat(),
                "submission": {},
            },
            {
                "name": "Due in 20 days",
                "due_at": (now + timedelta(days=20)).isoformat(),
                "submission": {},
            },
        ]
        course = [self.courses[0]]
        batch_return = [{"courseId": course[0]["id"], "ok": True, "items": assignments}]
        with mock.patch.object(canvas, "batch_fetch_course_assignments", return_value=batch_return):
            rows_14 = canvas.fetch_assignments(
                object(),
                days=14,
                tz=canvas.ZoneInfo("UTC"),
                courses=course,
            )
            rows_7 = canvas.fetch_assignments(
                object(),
                days=7,
                tz=canvas.ZoneInfo("UTC"),
                courses=course,
            )
            rows_all = canvas.fetch_assignments(
                object(),
                days="all",
                tz=canvas.ZoneInfo("UTC"),
                courses=course,
            )

        self.assertEqual([row["title"] for row in rows_14], ["Due in 5 days"])
        self.assertEqual([row["title"] for row in rows_7], ["Due in 5 days"])
        self.assertEqual(
            [row["title"] for row in rows_all],
            ["Due in 5 days", "Due in 20 days", "No deadline task"],
        )
        no_deadline_row = [r for r in rows_all if r["title"] == "No deadline task"][0]
        self.assertIsNone(no_deadline_row["due_at"])
        self.assertEqual(no_deadline_row["due_display"], "No due date")

    def test_fetch_assignments_isolates_course_errors_and_continues(self):
        due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        courses = [
            {"id": 10, "course_code": "GOOD101", "name": "Good Course"},
            {"id": 20, "course_code": "BAD202", "name": "Failing Course"},
        ]
        batch_return = [
            {
                "courseId": 10,
                "ok": True,
                "items": [{"name": "Good Task", "due_at": due_at, "submission": {}}],
            },
            {
                "courseId": 20,
                "ok": False,
                "error": "500 Internal Server Error",
                "items": [],
            },
        ]
        stderr = io.StringIO()
        with mock.patch.object(
            canvas,
            "batch_fetch_course_assignments",
            return_value=batch_return,
        ), contextlib.redirect_stderr(stderr):
            rows = canvas.fetch_assignments(
                object(),
                days=14,
                tz=canvas.ZoneInfo("UTC"),
                courses=courses,
            )

        self.assertEqual([r["title"] for r in rows], ["Good Task"])
        self.assertIn("Warning: skipped GOOD101" if False else "BAD202", stderr.getvalue())
        self.assertIn("500 Internal Server Error", stderr.getvalue())

    def test_main_days_argument_parsing(self):
        context = mock.Mock()
        context.pages = [object()]
        sync = mock.Mock()
        sync.__enter__ = mock.Mock(return_value=object())
        sync.__exit__ = mock.Mock(return_value=None)
        courses = [{"id": 1, "course_code": "NUR3145", "name": "Pharmacology"}]

        with tempfile.TemporaryDirectory() as profile, mock.patch(
            "sys.argv",
            ["canvas", "--json"],
        ), mock.patch.object(canvas, "PROFILE_DIR", Path(profile)), mock.patch.object(
            canvas, "sync_playwright", return_value=sync
        ), mock.patch.object(canvas, "launch_context", return_value=context), mock.patch.object(
            canvas, "ensure_login"
        ), mock.patch.object(canvas, "fetch_courses", return_value=courses), mock.patch.object(
            canvas, "fetch_assignments", return_value=[]
        ) as fetch, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(canvas.main(), 0)
            self.assertEqual(fetch.call_args.args[1], 14)

        with tempfile.TemporaryDirectory() as profile, mock.patch(
            "sys.argv",
            ["canvas", "--days", "all", "--json"],
        ), mock.patch.object(canvas, "PROFILE_DIR", Path(profile)), mock.patch.object(
            canvas, "sync_playwright", return_value=sync
        ), mock.patch.object(canvas, "launch_context", return_value=context), mock.patch.object(
            canvas, "ensure_login"
        ), mock.patch.object(canvas, "fetch_courses", return_value=courses), mock.patch.object(
            canvas, "fetch_assignments", return_value=[]
        ) as fetch, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(canvas.main(), 0)
            self.assertEqual(fetch.call_args.args[1], 0)


class ShortenTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "course": "NUR3145",
            "course_name": "Pharmacology",
            "title": "Quiz 2",
            "due_at": "2026-09-01T23:59:00+00:00",
            "due_display": "Tue, Sep 01, 2026 at 07:59 PM UTC",
            "instructions": "Complete the quiz.",
            "url": "https://ufl.instructure.com/courses/1/assignments/2",
            "submission_status": "unsubmitted",
        }

    def test_shorten_rows_omits_instructions_and_submission_status(self):
        shortened = canvas.shorten_rows([self.row])
        self.assertEqual(
            shortened,
            [{
                "course": "NUR3145",
                "course_name": "Pharmacology",
                "title": "Quiz 2",
                "due_at": "2026-09-01T23:59:00+00:00",
                "due_display": "Tue, Sep 01, 2026 at 07:59 PM UTC",
                "url": "https://ufl.instructure.com/courses/1/assignments/2",
            }],
        )
        self.assertEqual(
            set(shortened[0]),
            {"course", "course_name", "title", "due_at", "due_display", "url"},
        )

    def test_print_human_shortens_assignment_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            canvas.print_human([self.row], shorten=True)
        rendered = output.getvalue()
        self.assertIn("Quiz 2", rendered)
        self.assertIn("Course: NUR3145", rendered)
        self.assertIn("Due: Tue, Sep 01, 2026 at 07:59 PM UTC", rendered)
        self.assertIn("URL: https://ufl.instructure.com/courses/1/assignments/2", rendered)
        self.assertNotIn("Status:", rendered)
        self.assertNotIn("Instructions:", rendered)
        self.assertNotIn("Complete the quiz.", rendered)

    def test_main_shortens_json_assignment_output(self):
        context = mock.Mock()
        context.pages = [object()]
        sync = mock.Mock()
        sync.__enter__ = mock.Mock(return_value=object())
        sync.__exit__ = mock.Mock(return_value=None)
        courses = [{"id": 1, "course_code": "NUR3145", "name": "Pharmacology"}]
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as profile, mock.patch(
            "sys.argv",
            ["canvas", "--shorten", "--json"],
        ), mock.patch.object(canvas, "PROFILE_DIR", Path(profile)), mock.patch.object(
            canvas, "sync_playwright", return_value=sync
        ), mock.patch.object(canvas, "launch_context", return_value=context), mock.patch.object(
            canvas, "ensure_login"
        ), mock.patch.object(canvas, "fetch_courses", return_value=courses), mock.patch.object(
            canvas, "fetch_assignments", return_value=[self.row]
        ), contextlib.redirect_stdout(output):
            self.assertEqual(canvas.main(), 0)

        result = json.loads(output.getvalue())
        self.assertEqual(result, [canvas.shorten_rows([self.row])[0]])


class AuthenticationLogicTests(unittest.TestCase):
    def test_primary_form_latches_initial_loading_before_control_scans(self):
        page = mock.Mock()
        username = mock.Mock()
        password = mock.Mock()
        submit = mock.Mock()
        missing = (None, None, None)

        with mock.patch.object(
            canvas,
            "_primary_form_controls",
            side_effect=[missing, missing, missing, (username, password, submit)],
        ) as controls, mock.patch.object(
            canvas,
            "_page_has_text",
            side_effect=[True, False],
        ) as loading:
            canvas.fill_primary_credentials(
                page,
                canvas.Credentials("gator123", "secret"),
            )

        self.assertEqual(controls.call_count, 4)
        self.assertEqual(loading.call_count, 1)
        username.fill.assert_called_once_with("gator123")
        password.fill.assert_called_once_with("secret")
        submit.click.assert_called_once_with()


class BrowserFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = canvas.sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def test_primary_fallback_locators_fill_and_submit_once(self):
        self.page.set_content(
            """
            <form onsubmit="window.submitCount = (window.submitCount || 0) + 1; return false;">
              <span>Username</span>
              <input name="j_username">
              <span>Password</span>
              <input name="j_password" type="password">
              <button type="submit">Sign in</button>
            </form>
            """
        )
        credentials = canvas.Credentials("gator123", "secret")

        canvas.fill_primary_credentials(self.page, credentials)

        self.assertEqual(self.page.input_value('input[name="j_username"]'), "gator123")
        self.assertEqual(self.page.input_value('input[name="j_password"]'), "secret")
        self.assertEqual(self.page.evaluate("window.submitCount"), 1)

    def test_primary_form_waits_for_uf_session_redirect(self):
        self.page.set_content(
            """
            <p>LOADING SESSION INFORMATION</p>
            <script>
              setTimeout(() => {
                document.body.innerHTML = `
                  <form onsubmit="window.submitCount = (window.submitCount || 0) + 1; return false;">
                    <input name="j_username">
                    <input name="j_password" type="password">
                    <button type="submit">LOGIN</button>
                  </form>`;
              }, 350);
            </script>
            """
        )

        canvas.fill_primary_credentials(self.page, canvas.Credentials("gator123", "secret"))

        self.assertEqual(self.page.input_value('input[name="j_username"]'), "gator123")
        self.assertEqual(self.page.input_value('input[name="j_password"]'), "secret")
        self.assertEqual(self.page.evaluate("window.submitCount"), 1)

    def test_primary_form_without_controls_is_rejected(self):
        self.page.set_content("<p>Not a UF login form</p>")
        with self.assertRaisesRegex(RuntimeError, "UF login form was not recognized"):
            canvas.fill_primary_credentials(self.page, canvas.Credentials("u", "p"))

    def test_duo_already_sent_text_does_not_click(self):
        self.page.set_content(
            """
            <p>Check for a Duo Push</p>
            <button onclick="window.pushCount = (window.pushCount || 0) + 1">Send Me a Push</button>
            """
        )
        self.assertTrue(canvas.request_duo_push(self.page))
        self.assertEqual(self.page.evaluate("window.pushCount || 0"), 0)

    def test_duo_push_button_clicks_once(self):
        self.page.set_content(
            """
            <button onclick="window.pushCount = (window.pushCount || 0) + 1">Send Me a Push</button>
            """
        )
        self.assertTrue(canvas.request_duo_push(self.page))
        self.assertEqual(self.page.evaluate("window.pushCount || 0"), 1)

    def test_duo_other_options_path_clicks_other_and_push_once(self):
        self.page.set_content(
            """
            <button id="other" onclick="
              window.otherCount = (window.otherCount || 0) + 1;
              this.remove();
              const push = document.createElement('button');
              push.textContent = 'Duo Push';
              push.onclick = () => window.pushCount = (window.pushCount || 0) + 1;
              document.body.append(push);
            ">Other options</button>
            """
        )
        self.assertTrue(canvas.request_duo_push(self.page))
        self.assertEqual(self.page.evaluate("window.otherCount || 0"), 1)
        self.assertEqual(self.page.evaluate("window.pushCount || 0"), 1)

    def test_duo_other_options_waits_for_delayed_push_control(self):
        self.page.set_content(
            """
            <button id="other" onclick="
              window.otherCount = (window.otherCount || 0) + 1;
              this.remove();
              setTimeout(() => {
                const push = document.createElement('button');
                push.textContent = 'Duo Push';
                push.onclick = () => window.pushCount = (window.pushCount || 0) + 1;
                document.body.append(push);
              }, 350);
            ">Other options</button>
            """
        )
        self.assertTrue(canvas.request_duo_push(self.page))
        self.assertEqual(self.page.evaluate("window.otherCount || 0"), 1)
        self.assertEqual(self.page.evaluate("window.pushCount || 0"), 1)

    def test_duo_missing_push_method_fails_without_click(self):
        self.page.set_content(
            """
            <p>Use a passcode or security key.</p>
            <button onclick="window.otherCount = (window.otherCount || 0) + 1">Other account option</button>
            """
        )
        self.assertFalse(canvas.request_duo_push(self.page))
        self.assertEqual(self.page.evaluate("window.otherCount || 0"), 0)

    def test_duo_child_frame_text_is_detected(self):
        self.page.set_content(
            "<iframe srcdoc=\"<p>We've sent a notification</p>\"></iframe>"
        )
        self.assertTrue(canvas.request_duo_push(self.page))


    def test_batch_fetch_course_assignments_retries_on_rate_limit_and_succeeds(self):
        attempts = []

        def handle_route(route):
            attempts.append(route.request.url)
            if len(attempts) == 1:
                route.fulfill(
                    status=429,
                    headers={"Content-Type": "application/json", "Retry-After": "0"},
                    body=json.dumps({"message": "Rate Limit Exceeded"}),
                )
            else:
                route.fulfill(
                    status=200,
                    headers={"Content-Type": "application/json"},
                    body=json.dumps([{"id": 101, "name": "Recovered Assignment"}]),
                )

        self.page.route("**/api/v1/courses/99/assignments*", handle_route)
        try:
            results = canvas.batch_fetch_course_assignments(self.page, [99])
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["ok"])
            self.assertEqual(len(results[0]["items"]), 1)
            self.assertEqual(results[0]["items"][0]["name"], "Recovered Assignment")
            self.assertEqual(len(attempts), 2)
        finally:
            self.page.unroute("**/api/v1/courses/99/assignments*", handle_route)

    def test_batch_fetch_course_assignments_retries_on_403_rate_limit_message(self):
        attempts = []

        def handle_route(route):
            attempts.append(route.request.url)
            if len(attempts) == 1:
                route.fulfill(
                    status=403,
                    headers={"Content-Type": "application/json"},
                    body=json.dumps({"status": "unauthorized", "errors": [{"message": "Rate Limit Exceeded"}]}),
                )
            else:
                route.fulfill(
                    status=200,
                    headers={"Content-Type": "application/json"},
                    body=json.dumps([{"id": 102, "name": "Rate Limit Handled"}]),
                )

        self.page.route("**/api/v1/courses/88/assignments*", handle_route)
        try:
            results = canvas.batch_fetch_course_assignments(self.page, [88])
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["ok"])
            self.assertEqual(results[0]["items"][0]["name"], "Rate Limit Handled")
            self.assertEqual(len(attempts), 2)
        finally:
            self.page.unroute("**/api/v1/courses/88/assignments*", handle_route)


class HeadlessLaunchTests(unittest.TestCase):
    def test_launch_context_always_requests_headless(self):
        calls = []

        class FakeContext:
            pages = []

        class FakeChromium:
            def launch_persistent_context(self, **kwargs):
                calls.append(kwargs)
                return FakeContext()

        class FakePlaywright:
            chromium = FakeChromium()

        with tempfile.TemporaryDirectory() as profile:
            with mock.patch.object(canvas, "PROFILE_DIR", Path(profile)):
                context = canvas.launch_context(FakePlaywright())

        self.assertIsInstance(context, FakeContext)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["headless"])

    def test_session_check_distinguishes_unauthorized_and_network_failure(self):
        class FakePage:
            def __init__(self, result):
                self.result = result

            def evaluate(self, script, url):
                return self.result

        self.assertFalse(
            canvas.is_logged_in(
                FakePage({"ok": False, "status": 401, "text": "unauthorized"})
            )
        )
        with self.assertRaisesRegex(RuntimeError, "session check failed"):
            canvas.is_logged_in(
                FakePage({"ok": False, "status": 0, "text": "timed out"})
            )


if __name__ == "__main__":
    unittest.main()
