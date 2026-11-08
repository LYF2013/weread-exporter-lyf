import asyncio
import logging
import signal
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from weread_exporter import __main__ as cli, utils


class BrowserLifecycleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform == "win32":
            cli.patch_windows()
        from weread_exporter import export

        cls.export = export

    def setUp(self):
        self.page = SimpleNamespace(
            _cookie={"wr_vid": "test-user"}, check_valid=AsyncMock(return_value=True),
            launch=AsyncMock(), close=AsyncMock(),
        )
        self.exporter = SimpleNamespace(
            export_markdown=AsyncMock(), pre_process_markdown=AsyncMock(),
            get_book_title=AsyncMock(return_value="Test Book"),
        )

    async def run_cli(self):
        with patch.object(sys, "argv", ["weread-exporter", "-b", "test-book", "-o", "md"]), \
             patch.object(cli.webpage, "WeReadWebPage", return_value=self.page), \
             patch.object(self.export, "WeReadExporter", return_value=self.exporter), \
             patch.object(cli.os.path, "isdir", return_value=True), \
             patch.object(cli.logging, "exception"), \
             patch.object(cli.asyncio, "sleep", new_callable=AsyncMock):
            return await cli.async_main()

    async def test_cancelled_export_closes_browser(self):
        self.exporter.export_markdown.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_cli()
        self.page.close.assert_awaited_once()

    async def test_unexpected_export_error_closes_browser(self):
        self.exporter.export_markdown.side_effect = ValueError("Test unexpected error")
        with self.assertRaises(ValueError):
            await self.run_cli()
        self.page.close.assert_awaited_once()

    async def test_user_break_during_launch_closes_browser(self):
        self.page.launch.side_effect = utils.BreakExportingError()
        self.assertEqual(await self.run_cli(), -1)
        self.page.close.assert_awaited_once()

    async def test_launch_failures_close_browser_and_stop_after_three_attempts(self):
        self.page.launch.side_effect = [RuntimeError("Test launch failure")] * 3 + [ValueError("Unbounded retry")]
        with self.assertRaises(RuntimeError):
            await self.run_cli()
        self.assertEqual(self.page.launch.await_count, 3)
        self.assertEqual(self.page.close.await_count, 3)

    async def test_chapter_failures_stop_after_three_browser_attempts(self):
        self.exporter.export_markdown.side_effect = [utils.LoadChapterFailedError("Test missing hook")] * 3 + [ValueError("Unbounded retry")]
        with self.assertRaises(utils.LoadChapterFailedError):
            await self.run_cli()
        self.assertEqual(self.page.close.await_count, 3)

    async def test_successful_retry_closes_each_browser(self):
        self.exporter.export_markdown.side_effect = [utils.LoadChapterFailedError("Test missing hook"), None]
        self.assertEqual(await self.run_cli(), 0)
        self.assertEqual(self.page.launch.await_count, 2)
        self.assertEqual(self.page.close.await_count, 2)
        self.exporter.pre_process_markdown.assert_awaited_once()


class MainShutdownTests(unittest.TestCase):
    def test_signal_cancels_task_then_runs_async_cleanup_and_restores_handlers(self):
        handlers = {}
        cleaned_up = []

        def register(signum, handler):
            previous = handlers.get(signum, signal.SIG_DFL)
            handlers[signum] = handler
            return previous

        async def run():
            try:
                handlers[signal.SIGINT](signal.SIGINT, None)
                await asyncio.sleep(0)
            finally:
                handlers[signal.SIGINT](signal.SIGINT, None)
                await asyncio.sleep(0)
                cleaned_up.append(True)

        previous_level = logging.root.level
        self.addCleanup(logging.root.setLevel, previous_level)
        with patch.object(cli, "async_main", side_effect=run), \
             patch.object(cli, "patch_windows"), \
             patch.object(cli, "patch_generateRequestHash"), \
             patch.object(cli.utils, "check_cairo_installed"), \
             patch.object(signal, "signal", side_effect=register), \
             patch.object(signal, "getsignal", return_value=signal.SIG_DFL):
            self.assertEqual(cli.main(), 130)
        self.assertEqual(cleaned_up, [True])
        self.assertEqual(handlers[signal.SIGINT], signal.SIG_DFL)
