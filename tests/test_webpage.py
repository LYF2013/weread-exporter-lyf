import asyncio
import io
import functools
import subprocess
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from multidict import CIMultiDict

from weread_exporter import utils, webpage


class WebPageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.page = webpage.WeReadWebPage.__new__(webpage.WeReadWebPage)
        self.page._cookie = {"wr_vid": "test-user", "wr_skey": "test-key"}
        self.page._cookie_path = None
        self.page._launcher = None
        self.page._save_cookie = Mock()
        self.page._page = SimpleNamespace(
            evaluate=AsyncMock(return_value="https://example.invalid/avatar.png"),
            cookies=AsyncMock(return_value=[
                {"name": "wr_vid", "value": "test-user"},
                {"name": "wr_skey", "value": "test-key"},
            ]),
            setCookie=AsyncMock(),
            waitForFunction=AsyncMock(),
            evaluateOnNewDocument=AsyncMock(),
        )

    async def test_missing_avatar_is_not_logged_in(self):
        for avatar in (None, "", "https://example.invalid/Default.svg?v=1"):
            with self.subTest(avatar=avatar):
                self.page._page.evaluate.return_value = avatar
                with patch.object(webpage.time, "time", side_effect=[0, 0, 31]), \
                     patch.object(webpage.asyncio, "sleep", new_callable=AsyncMock):
                    with self.assertRaisesRegex(RuntimeError, "avatar"):
                        await self.page.wait_for_avatar()

    async def test_avatar_alone_cannot_validate_session(self):
        with patch.object(self.page, "get_user_info", new_callable=AsyncMock) as get_user:
            get_user.side_effect = utils.InvalidUserError("Session expired")
            with self.assertRaises(utils.InvalidUserError):
                await self.page.wait_for_avatar()
        self.page._save_cookie.assert_not_called()

    async def test_wait_for_avatar_validates_server_and_saves(self):
        with patch.object(self.page, "get_user_info", new_callable=AsyncMock) as get_user:
            get_user.return_value = {"name": "Test User"}
            await self.page.wait_for_avatar()
        get_user.assert_awaited_once()
        self.page._save_cookie.assert_called_once()

    async def test_missing_session_cookie_is_rejected(self):
        self.page._page.cookies.return_value = [{"name": "wr_vid", "value": "test-user"}]
        with self.assertRaises(utils.InvalidUserError):
            await self.page.wait_for_avatar()
        self.page._save_cookie.assert_not_called()

    async def test_failed_refresh_is_not_reported_as_success(self):
        with patch.object(utils, "fetch", new_callable=AsyncMock) as fetch:
            fetch.side_effect = [
                b'{"errCode": -2012}',
                (200, CIMultiDict(), b"test"),
                b'{"errCode": -2012}',
            ]
            with self.assertRaises(utils.InvalidUserError):
                await self.page.get_user_info()
        self.page._save_cookie.assert_not_called()

    async def test_refresh_rejection_with_different_code_is_checked(self):
        with patch.object(utils, "fetch", new_callable=AsyncMock) as fetch:
            fetch.side_effect = [
                b'{"errCode": -2012}',
                (200, CIMultiDict(), b"test"),
                b'{"errCode": -2010}',
            ]
            with self.assertRaises(utils.InvalidUserError):
                await self.page.get_user_info()

    async def test_refreshed_session_is_synced_to_browser(self):
        with patch.object(utils, "fetch", new_callable=AsyncMock) as fetch:
            fetch.side_effect = [
                b'{"errCode": -2012}',
                (200, CIMultiDict([
                    ("Set-Cookie", "wr_skey=test-renewed-key; Path=/"),
                    ("Set-Cookie", "wr_rt=test-renewed-refresh; Path=/"),
                ]), b"test"),
                b'{"name": "Test User"}',
            ]
            await self.page.wait_for_avatar()
        injected = {
            call.args[0]["name"]: call.args[0]["value"]
            for call in self.page._page.setCookie.call_args_list
        }
        self.assertEqual(injected["wr_skey"], "test-renewed-key")
        self.assertEqual(injected["wr_rt"], "test-renewed-refresh")
        self.page._save_cookie.assert_called_once()

    async def test_cookie_values_are_not_logged(self):
        with self.assertLogs(level="INFO") as captured:
            await self.page._inject_cookie()
        self.assertNotIn("test-key", "\n".join(captured.output))
        self.assertNotIn("test-user", "\n".join(captured.output))

    async def test_invalid_user_error_does_not_expose_cookie(self):
        self.page._cookie = {"wr_skey": "test-private-key"}
        with self.assertRaises(utils.InvalidUserError) as captured:
            await self.page.get_user_info()
        self.assertNotIn("test-private-key", str(captured.exception))

    async def test_login_waits_for_server_validation(self):
        self.page._page.waitForSelector = AsyncMock()
        self.page._page.click = AsyncMock()
        self.page._page.evaluate.side_effect = ["登录", True, True]
        with patch.object(self.page, "get_user_info", new_callable=AsyncMock) as get_user, \
             patch.object(webpage.asyncio, "sleep", new_callable=AsyncMock), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            get_user.side_effect = [
                utils.InvalidUserError("Test expired session"), {"name": "Test User"},
            ]
            self.assertTrue(await self.page.login())
        self.assertEqual(get_user.await_count, 2)
        self.page._save_cookie.assert_called_once()
        self.assertEqual(output.getvalue().count("Login successful!"), 1)

    async def test_no_login_control_does_not_retry_navigation(self):
        self.page._headless = False
        self.page._page.goto = AsyncMock()
        self.page.pre_load_page = AsyncMock()
        self.page._get_chapter_url = Mock(return_value="https://weread.qq.com/web/reader/test")
        self.page._check_next_page = AsyncMock(side_effect=utils.LoginRequiredError())
        self.page.login = AsyncMock(return_value=False)
        with self.assertRaisesRegex(utils.LoginRequiredError, "did not complete"):
            await self.page.goto_chapter("test-chapter")
        self.page._page.goto.assert_awaited_once()

    async def test_successful_relogin_reloads_same_chapter_once(self):
        self.page._headless = False
        self.page._page.goto = AsyncMock()
        self.page.pre_load_page = AsyncMock()
        self.page._get_chapter_url = Mock(return_value="https://weread.qq.com/web/reader/test")
        self.page._check_next_page = AsyncMock(side_effect=[utils.LoginRequiredError(), None])
        self.page.login = AsyncMock(return_value=True)
        await self.page.goto_chapter("test-chapter")
        self.assertEqual(self.page._page.goto.await_count, 2)
        self.assertEqual(self.page._page.goto.call_args_list[0],
                         self.page._page.goto.call_args_list[1])
        self.page.login.assert_awaited_once()

    async def test_login_cookie_read_does_not_log_refresh_values(self):
        with patch.object(utils, "fetch", new_callable=AsyncMock) as fetch:
            fetch.side_effect = [
                b'{"errCode": -2012}',
                (200, CIMultiDict([
                    ("Set-Cookie", "wr_skey=test-new-private-key;Path=/"),
                    ("Set-Cookie", "test-malformed-private-value"),
                ]), b"test"),
                b'{"name": "Test User"}',
            ]
            with self.assertLogs(level="INFO") as captured:
                await self.page.get_user_info()
        self.assertEqual(self.page._cookie["wr_skey"], "test-new-private-key")
        self.assertNotIn("test-new-private-key", "\n".join(captured.output))
        self.assertNotIn("test-malformed-private-value", "\n".join(captured.output))

    async def test_next_page_continues_until_next_chapter(self):
        self.page.wait_for_selector = AsyncMock()
        self.page.pre_load_page = AsyncMock()
        self.page._page.click = AsyncMock()
        self.page._page.evaluate.side_effect = [
            "下一页", None, "下一页", None, "下一章",
        ]
        with patch.object(webpage.random, "uniform", side_effect=[30, 60]) as uniform, \
             patch.object(webpage.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await self.page._check_next_page()
        self.assertEqual(self.page._page.click.await_count, 2)
        self.assertEqual([call.args for call in uniform.call_args_list], [(30, 60), (30, 60)])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [30, 1, 60, 1])
        self.assertEqual(sum(
            call.args[0] == r"canvasContextHandler.data.markdown += '\n\n';"
            for call in self.page._page.evaluate.call_args_list
        ), 2)

    async def test_next_page_waits_before_click_at_random_boundaries(self):
        for delay in (30, 45, 60):
            with self.subTest(delay=delay):
                self.page.wait_for_selector = AsyncMock()
                self.page.pre_load_page = AsyncMock()
                self.page._page.click = AsyncMock()
                self.page._page.evaluate.side_effect = ["下一页", None, "下一章"]
                events = Mock()
                events.attach_mock(self.page._page.click, "click")
                with patch.object(webpage.random, "uniform", return_value=delay) as uniform, \
                     patch.object(webpage.asyncio, "sleep", new_callable=AsyncMock) as sleep:
                    events.attach_mock(sleep, "sleep")
                    await self.page._check_next_page()
                uniform.assert_called_once_with(30, 60)
                self.assertEqual([
                    (call[0], call.args) for call in events.mock_calls
                ], [
                    ("sleep", (delay,)),
                    ("click", ("button.readerFooter_button",)),
                    ("sleep", (1,)),
                ])

    async def test_next_chapter_does_not_click_next_page(self):
        self.page.wait_for_selector = AsyncMock()
        self.page._page.click = AsyncMock()
        self.page._page.evaluate.return_value = "下一章"
        with patch.object(webpage.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await self.page._check_next_page()
        self.page._page.click.assert_not_awaited()
        sleep.assert_not_awaited()

    async def test_close_reinstalls_hook_and_proxy_for_new_browser(self):
        self.page._proxy_installed = False
        self.page._hook_installed = False
        self.page._browser = SimpleNamespace(close=AsyncMock())
        first_page = self.page._page
        with patch.object(webpage.webproxy, "WebProxy") as proxy:
            proxy.return_value.setup_interception = AsyncMock()
            await self.page.pre_load_page()
            await self.page.pre_load_page()
            await self.page.close()
            self.assertFalse(self.page._proxy_installed)
            self.assertFalse(self.page._hook_installed)
            self.page._page = SimpleNamespace(evaluateOnNewDocument=AsyncMock())
            await self.page.pre_load_page()
        self.assertEqual(proxy.return_value.setup_interception.await_count, 2)
        first_page.evaluateOnNewDocument.assert_awaited_once()
        self.page._page.evaluateOnNewDocument.assert_awaited_once()

    async def test_failed_proxy_install_can_retry_without_duplicate_hook(self):
        self.page._proxy_installed = False
        self.page._hook_installed = False
        with patch.object(webpage.webproxy, "WebProxy") as proxy:
            proxy.return_value.setup_interception = AsyncMock(side_effect=[RuntimeError("Test failure"), None])
            with self.assertRaises(RuntimeError):
                await self.page.pre_load_page()
            self.assertFalse(self.page._proxy_installed)
            await self.page.pre_load_page()
        self.assertTrue(self.page._proxy_installed)
        self.page._page.evaluateOnNewDocument.assert_awaited_once()

    async def test_close_resets_state_even_when_browser_close_fails(self):
        self.page._proxy_installed = True
        self.page._hook_installed = True
        self.page._browser = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("Test close failure")))
        with self.assertRaises(RuntimeError):
            await self.page.close()
        self.assertIsNone(self.page._browser)
        self.assertIsNone(self.page._page)
        self.assertFalse(self.page._proxy_installed)
        self.assertFalse(self.page._hook_installed)
        await self.page.close()

    async def test_missing_hook_fails_before_page_delay_or_click(self):
        self.page.wait_for_selector = AsyncMock()
        self.page._page.evaluate.return_value = "下一页"
        self.page._page.click = AsyncMock()
        self.page._page.waitForFunction.side_effect = webpage.pyppeteer.errors.TimeoutError("Test missing hook")
        with patch.object(webpage.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaisesRegex(utils.LoadChapterFailedError, "hook"):
                await self.page._check_next_page()
        sleep.assert_not_awaited()
        self.page._page.click.assert_not_awaited()

    async def test_hook_is_registered_before_site_scripts_without_external_url(self):
        self.page._proxy_installed = False
        self.page._hook_installed = False
        with patch.object(webpage.webproxy, "WebProxy") as proxy:
            proxy.return_value.setup_interception = AsyncMock()
            await self.page.pre_load_page()
        script = self.page._page.evaluateOnNewDocument.call_args.args[0]
        self.assertIn("HTMLCanvasElement.prototype.getContext", script)
        self.assertIn("window.canvasContextHandler = canvasContextHandler", script)
        self.assertIn("/web/reader/", script)
        self.assertNotIn("cdn.weread.qq.com", script)

    async def test_registered_hook_captures_first_canvas_drawing_in_fresh_documents(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable for the JavaScript smoke test")
        self.page._proxy_installed = False
        self.page._hook_installed = False
        with patch.object(webpage.webproxy, "WebProxy") as proxy:
            proxy.return_value.setup_interception = AsyncMock()
            await self.page.pre_load_page()
        script = self.page._page.evaluateOnNewDocument.call_args.args[0]
        runner = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
for (const chapter of ['first', 'second', 'after-restart']) {
    const context = vm.createContext({});
    vm.runInContext(`
        var window = globalThis;
        var location = {hostname: 'weread.qq.com', pathname: '/web/reader/test'};
        var document = {getElementsByClassName: () => [], querySelector: () => null};
        class HTMLCanvasElement {
            getContext() { return {fillText() {}}; }
        }
    `, context);
    vm.runInContext('(' + source + ')()', context);
    vm.runInContext(`new HTMLCanvasElement().getContext('2d').fillText('${chapter}', 0, 0)`, context);
    assert.equal(vm.runInContext('canvasContextHandler.data.markdown', context), chapter);
}
const context = vm.createContext({location: {hostname: 'weread.qq.com', pathname: '/web/bookDetail/test'}});
vm.runInContext('(' + source + ')()', context);
assert.equal(vm.runInContext('typeof canvasContextHandler', context), 'undefined');
console.log('Fresh-document hook capture: OK');
"""
        result = await asyncio.get_running_loop().run_in_executor(None, functools.partial(
            subprocess.run, [node, "-e", runner], input=script, text=True,
            capture_output=True, timeout=15,
        ))
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_document_navigation_uses_browser_transport_not_python_fetch(self):
        self.page._proxy_installed = False
        self.page._hook_installed = False
        self.page._page._client = SimpleNamespace(send=AsyncMock())
        with patch.object(webpage.webproxy.WebProxy, "setup_interception", new_callable=AsyncMock):
            with patch.object(webpage.webproxy, "WebProxy", wraps=webpage.webproxy.WebProxy) as constructor:
                await self.page.pre_load_page()
            rules = constructor.call_args.args[1]
        proxy = webpage.webproxy.WebProxy(self.page._page, rules)
        with patch.object(utils, "fetch", new_callable=AsyncMock) as fetch:
            await proxy._handle_fetch_event("test-request", {"request": {
                "url": "https://weread.qq.com/web/reader/test", "method": "GET", "headers": {},
            }})
        fetch.assert_not_awaited()
        self.assertEqual(self.page._page._client.send.call_args.args[0], "Fetch.continueRequest")

    async def test_launcher_disables_library_signal_and_atexit_handlers(self):
        self.page._proxy_installed = True
        self.page._hook_installed = True
        self.page._home_url = "https://weread.qq.com/web/bookDetail/test"
        self.page._check_chrome = Mock(return_value="test-chrome")
        with patch.object(webpage.tempfile, "mkdtemp", return_value="test-profile"), \
             patch.object(webpage.pyppeteer.launcher, "Launcher") as launcher:
            launcher.return_value.launch = AsyncMock(side_effect=RuntimeError("Test startup failure"))
            with self.assertRaises(RuntimeError):
                await self.page.launch()
        for option in ("handleSIGINT", "handleSIGTERM", "handleSIGHUP", "autoClose"):
            self.assertFalse(launcher.call_args.kwargs[option])
        self.assertFalse(self.page._proxy_installed)
        self.assertFalse(self.page._hook_installed)

    async def test_partial_browser_launch_is_cleaned_up(self):
        self.page._browser = None
        self.page._launcher = SimpleNamespace(proc=Mock(poll=Mock(return_value=0)), killChrome=AsyncMock())
        launcher = self.page._launcher
        await self.page.close()
        launcher.killChrome.assert_awaited_once()
        self.assertIsNone(self.page._launcher)

    async def test_close_terminates_only_owned_process_when_connection_close_fails(self):
        process = Mock(poll=Mock(return_value=None))
        self.page._launcher = SimpleNamespace(proc=process)
        self.page._browser = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("Test connection closed")))
        with self.assertRaises(RuntimeError):
            await self.page.close()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(5)
        self.assertIsNone(self.page._browser)

    async def test_close_escalates_only_owned_process_after_termination_timeout(self):
        process = Mock(poll=Mock(return_value=None))
        process.wait.side_effect = [subprocess.TimeoutExpired("test-chrome", 5), 0]
        self.page._launcher = SimpleNamespace(proc=process)
        self.page._browser = SimpleNamespace(close=AsyncMock())
        await self.page.close()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
