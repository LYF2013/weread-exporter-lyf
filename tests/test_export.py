import asyncio
import io
import json
import runpy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, mock_open, patch

from weread_exporter import __main__ as cli


class PluginMigrationTests(unittest.IsolatedAsyncioTestCase):
    def load_plugin(self, name):
        root = Path(__file__).resolve().parents[1]
        with patch.object(sys, "path", sys.path.copy()):
            return runpy.run_path(str(root / ".claude-plugin" / name))

    async def test_standalone_entry_uses_repository_root(self):
        plugin = self.load_plugin("skill.py")
        root = Path(__file__).resolve().parents[1]
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run, \
             patch("os.path.exists", return_value=False), \
             patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(plugin["skill_main"]({"book_id": "test-book", "formats": ["epub"]}), 0)
        self.assertEqual(Path(run.call_args.kwargs["cwd"]), root)
        self.assertIn("--force-login", run.call_args.args[0])

    async def test_mcp_declares_current_interval_default(self):
        plugin = self.load_plugin("mcp_server.py")
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            await plugin["handle_message"]({"method": "tools/list", "id": 1})
        schema = json.loads(output.getvalue())["result"]["tools"][0]["inputSchema"]
        self.assertEqual(schema["properties"]["load_interval"]["default"], 60)

    async def test_mcp_entry_forwards_interval_from_repository_root(self):
        plugin = self.load_plugin("mcp_server.py")
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"test", b"")))
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process) as spawn, \
             patch("os.path.exists", return_value=False):
            result = await plugin["export_weread_book"]({"book_id": "test-book", "load_interval": 60})
        self.assertIn("Export completed!", result)
        self.assertEqual(Path(spawn.call_args.kwargs["cwd"]), Path(__file__).resolve().parents[1])
        self.assertEqual(spawn.call_args.args[-2:], ("--load-interval", "60"))


class ExportTimingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform == "win32":
            cli.patch_windows()
        from weread_exporter import export

        cls.export = export

    async def test_default_interval_and_random_boundaries(self):
        for offset in (0, 45, 90):
            with self.subTest(offset=offset):
                exporter = self.export.WeReadExporter.__new__(self.export.WeReadExporter)
                exporter._chapter_dir = "test-chapters"
                exporter._cover_image_path = "test-cover.jpg"
                exporter._load_meta_data = AsyncMock(return_value={"chapters": [
                    {"id": "test-chapter", "title": "Test Chapter", "words": 10},
                ]})
                exporter._make_chapter_path = Mock(return_value="test-chapter.md")
                exporter._page = SimpleNamespace(
                    goto_chapter=AsyncMock(), get_markdown=AsyncMock(return_value="Test content"),
                )
                with patch.object(self.export.os.path, "isdir", return_value=True), \
                     patch.object(self.export.os.path, "isfile", side_effect=[True, False]), \
                     patch.object(self.export.random, "uniform", return_value=offset) as uniform, \
                     patch.object(self.export.asyncio, "sleep", new_callable=AsyncMock) as sleep, \
                     patch("builtins.open", mock_open()):
                    await exporter.export_markdown()
                uniform.assert_called_once_with(0, 90)
                sleep.assert_awaited_once_with(60 + offset)

    async def test_cli_help_describes_interval(self):
        with patch.object(sys, "argv", ["weread-exporter", "--help"]), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            with self.assertRaises(SystemExit) as captured:
                await cli.async_main()
        self.assertEqual(captured.exception.code, 0)
        self.assertIn("0~90s (default: 60)", output.getvalue())

    async def test_cli_default_interval_is_60(self):
        def check_default(parser):
            self.assertEqual(parser.get_default("load_interval"), 60)
            raise SystemExit(0)

        with patch.object(cli.argparse.ArgumentParser, "parse_args", autospec=True,
                          side_effect=check_default):
            with self.assertRaises(SystemExit):
                await cli.async_main()

    async def test_cancelled_chapter_is_not_swallowed_or_retried(self):
        exporter = self.export.WeReadExporter.__new__(self.export.WeReadExporter)
        exporter._chapter_dir = "test-chapters"
        exporter._cover_image_path = "test-cover.jpg"
        exporter._load_meta_data = AsyncMock(return_value={"chapters": [
            {"id": "test-chapter", "title": "Test Chapter", "words": 10},
        ]})
        exporter._make_chapter_path = Mock(return_value="test-chapter.md")
        exporter._page = SimpleNamespace(goto_chapter=AsyncMock(side_effect=asyncio.CancelledError()))
        with patch.object(self.export.os.path, "isdir", return_value=True), \
             patch.object(self.export.os.path, "isfile", side_effect=[True, False]):
            with self.assertRaises(asyncio.CancelledError):
                await exporter.export_markdown()
        exporter._page.goto_chapter.assert_awaited_once()
