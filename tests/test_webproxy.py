import asyncio
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from multidict import CIMultiDict

from weread_exporter import webproxy
from weread_exporter.webpage import WeReadWebPage


class WebProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_request_uses_live_url_scoped_cookies(self):
        page = WeReadWebPage.__new__(WeReadWebPage)
        page._cookie = {"wr_skey": "test-stale-key"}
        page._page = SimpleNamespace(cookies=AsyncMock(return_value=[
            {"name": "wr_vid", "value": "test-user"},
            {"name": "wr_skey", "value": "test-live-key"},
            {"name": "wr_useHorizonReader", "value": "1"},
        ]))
        request = webproxy.WebRequest(
            "https://weread.qq.com/web/reader/test-book", "GET",
            {"Cookie": "wr_skey=test-stale-key", "Accept": "text/html"}, None,
        )

        rule = webproxy.ProxyRule("*/web/reader/*", page.on_document_request)
        result = await rule.handle_request(request)

        page._page.cookies.assert_awaited_once_with(request.url)
        self.assertEqual(result["headers"]["cookie"],
                         "wr_vid=test-user; wr_skey=test-live-key; wr_useHorizonReader=0")
        self.assertNotIn("Cookie", result["headers"])
        self.assertEqual(result["headers"]["accept"], "text/html")

    async def test_anonymous_document_does_not_reuse_stale_cookie(self):
        page = WeReadWebPage.__new__(WeReadWebPage)
        page._page = SimpleNamespace(cookies=AsyncMock(return_value=[]))
        request = webproxy.WebRequest(
            "https://weread.qq.com/web/reader/test-book", "GET",
            {"cookie": "wr_skey=test-expired-key"}, None,
        )

        rule = webproxy.ProxyRule("*/web/reader/*", page.on_document_request)
        result = await rule.handle_request(request)

        self.assertEqual(result["headers"]["cookie"], "wr_useHorizonReader=0")

    async def test_document_proxy_preserves_login_cookies_end_to_end(self):
        client = SimpleNamespace(send=AsyncMock())
        browser_page = SimpleNamespace(
            _client=client,
            cookies=AsyncMock(return_value=[
                {"name": "wr_skey", "value": "test-live-key"},
            ]),
        )
        page = WeReadWebPage.__new__(WeReadWebPage)
        page._page = browser_page
        def preserve_response(response):
            return {"status": response.status, "headers": response.headers, "body": response.body}

        rules = [
            webproxy.ProxyRule("*/web/reader/*", page.on_document_request),
            webproxy.ProxyRule("*/web/reader/*", preserve_response,
                               stage=webproxy.EnumProxyStage.Response),
        ]
        proxy = webproxy.WebProxy(browser_page, rules)
        headers = CIMultiDict([
            ("Content-Type", "text/html"),
            ("Set-Cookie", "wr_skey=test-renewed-key; Path=/; Secure"),
            ("Set-Cookie", "wr_rt=test-renewed-refresh; Path=/; Secure"),
        ])
        with patch.object(webproxy.utils, "fetch", new_callable=AsyncMock) as fetch:
            fetch.return_value = (200, headers, b"<html><head></head><body>test</body></html>")
            await proxy._handle_fetch_event("test-request", {"request": {
                "url": "https://weread.qq.com/web/reader/test-book",
                "method": "GET", "headers": {"Accept": "text/html"},
            }})

        self.assertEqual(fetch.call_args.kwargs["headers"]["cookie"],
                         "wr_skey=test-live-key; wr_useHorizonReader=0")
        method, params = client.send.call_args.args
        self.assertEqual(method, "Fetch.fulfillRequest")
        self.assertEqual([
            entry["value"] for entry in params["responseHeaders"]
            if entry["name"].lower() == "set-cookie"
        ], headers.getall("Set-Cookie"))
        self.assertEqual(b"<html><head></head><body>test</body></html>", base64.b64decode(params["body"]))

    async def test_response_keeps_repeated_set_cookie_headers(self):
        request = webproxy.WebRequest("https://weread.qq.com/", "GET", {}, None)
        response = webproxy.WebResponse(request, 200, CIMultiDict([
            ("Set-Cookie", "wr_skey=test-key"),
            ("set-cookie", "wr_rt=test-refresh"),
        ]), b"test")

        entries = webproxy.WebProxy._to_header_entries(response.headers)

        self.assertEqual(entries, [
            {"name": "set-cookie", "value": "wr_skey=test-key"},
            {"name": "set-cookie", "value": "wr_rt=test-refresh"},
        ])

    async def test_next_chapter_reads_cookies_again(self):
        page = WeReadWebPage.__new__(WeReadWebPage)
        page._page = SimpleNamespace(cookies=AsyncMock(side_effect=[
            [{"name": "wr_skey", "value": "test-first-key"}],
            [{"name": "wr_skey", "value": "test-second-key"}],
        ]))
        for chapter, expected in (("first", "test-first-key"), ("second", "test-second-key")):
            request = webproxy.WebRequest(
                "https://weread.qq.com/web/reader/" + chapter, "GET", {}, None,
            )
            await page.on_document_request(request)
            self.assertEqual(request.headers["cookie"],
                             "wr_skey=%s; wr_useHorizonReader=0" % expected)
        self.assertEqual(page._page.cookies.await_count, 2)

    async def test_real_http_transport_preserves_multiple_set_cookie_headers(self):
        received = []

        async def respond(reader, writer):
            received.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Set-Cookie: wr_skey=test-new-key; Path=/\r\n"
                b"Set-Cookie: wr_rt=test-new-refresh; Path=/\r\n"
                b"Content-Length: 4\r\nConnection: close\r\n\r\ntest"
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(respond, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            request = webproxy.WebRequest(
                "http://127.0.0.1:%d/" % port, "GET",
                {"Cookie": "wr_skey=test-current-key"}, None,
            )
            proxy = webproxy.WebProxy(SimpleNamespace(_client=None), [])
            response = await proxy.get_http_response(request)

        self.assertIsNotNone(response)
        self.assertEqual(response.body, b"test")
        self.assertEqual(response.headers.getall("Set-Cookie"), [
            "wr_skey=test-new-key; Path=/", "wr_rt=test-new-refresh; Path=/",
        ])
        self.assertIn(b"cookie: wr_skey=test-current-key", received[0].lower())
