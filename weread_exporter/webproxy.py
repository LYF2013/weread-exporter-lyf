"""
Web Proxy
"""

import asyncio
import base64
import logging
import re
import sys
import traceback
from typing import Dict, List, Optional, Tuple, Union, Any, Callable, Awaitable, cast  # pyright: ignore[reportDeprecated]

import pyppeteer
from multidict import CIMultiDict

from . import utils

if sys.version_info >= (3, 8):
    from typing import TYPE_CHECKING
else:
    from typing_extensions import TYPE_CHECKING


class EnumResourceType(object):
    """资源类型"""

    Document: str = "Document"
    Stylesheet: str = "Stylesheet"
    Image: str = "Image"
    Media: str = "Media"
    Font: str = "Font"
    Script: str = "Script"
    TextTrack: str = "TextTrack"
    XHR: str = "XHR"
    Fetch: str = "Fetch"
    Prefetch: str = "Prefetch"
    EventSource: str = "EventSource"
    WebSocket: str = "WebSocket"
    Manifest: str = "Manifest"
    SignedExchange: str = "SignedExchange"
    Ping: str = "Ping"
    CSPViolationReport: str = "CSPViolationReport"
    Preflight: str = "Preflight"
    FedCM: str = "FedCM"
    Other: str = "Other"


class EnumProxyStage(object):
    """代理阶段"""

    Request: str = "request"
    Response: str = "response"


class EnumProxyType(object):
    """代理类型"""

    Block: int = 1
    Continue: int = 2
    Mock: int = 3


class ProxyRule(object):
    """代理规则基类

    用于定义请求拦截规则和响应处理逻辑
    """

    def __init__(
        self,
        url_pattern: str,
        callback: Callable[
            [Union["WebRequest", "WebResponse"]],  # pyright: ignore[reportDeprecated]
            Union[Dict[str, Any], Awaitable[Dict[str, Any]], None],  # pyright: ignore[reportDeprecated]
        ],
        resource_type: Optional[str] = None,  # pyright: ignore[reportDeprecated]
        stage: Optional[str] = None,  # pyright: ignore[reportDeprecated]
        methods: Optional[List[str]] = None,  # pyright: ignore[reportDeprecated]
    ) -> None:
        """初始化代理规则

        Args:
            url_pattern: URL 匹配模式，支持通配符 *
            callback: 回调函数
            resource_type: 资源类型，如 'Document', 'Script', 'Stylesheet', 'Image', 'XHR', 'Fetch', 'WebSocket' 等
            stage: 代理阶段
            methods: HTTP 方法列表，如 ['GET', 'POST']，None 表示匹配所有方法
        """
        self._url_pattern = url_pattern
        self._callback = callback
        self._resource_type = resource_type
        self._stage = stage or EnumProxyStage.Request
        self._methods = methods or []

    @property
    def stage(self) -> str:
        """拦截阶段"""
        return self._stage

    def matches(self, request: Any) -> bool:  # pyright: ignore[reportUnknownParameterType]
        """检查请求是否匹配此规则

        Args:
            request: WebRequest 对象（用于匹配）

        Returns:
            bool: 是否匹配
        """
        # WebRequest 对象
        url: str = request.url
        method: str = request.method

        # 检查 URL 模式
        if not self._match_url(url):
            return False

        # 检查 HTTP 方法
        if self._methods and method not in self._methods:
            return False

        return True

    def _match_url(self, url: str) -> bool:
        """匹配 URL 模式

        支持 * 通配符，如 '*/web/book/read*' 或 '*/weread.qq.com/*'

        Args:
            url: 实际的 URL

        Returns:
            bool: 是否匹配
        """
        pattern = self._url_pattern.replace("*", ".*")
        # 确保模式匹配完整字符串
        if not pattern.startswith(".*"):
            pattern = "^.*" + pattern
        if not pattern.endswith(".*"):
            pattern = pattern + ".*$"

        return re.match(pattern, url) is not None

    async def handle_request(self, request: "WebRequest") -> Optional[Dict[str, Any]]:  # pyright: ignore[reportDeprecated]
        """处理请求，返回响应

        调用 callback 函数并返回其结果。支持同步和异步 callback。

        Args:
            request (WebRequest): WebRequest 请求对象

        Returns:
            dict: 包含响应信息的字典，格式为 {
                'status': int,
                'headers': dict,
                'body': bytes
            }
            或者 None 表示不处理此请求（继续原请求）
        """
        # 调用 callback 函数
        try:
            result = self._callback(request)

            # 如果 callback 是协程，等待其完成
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as ex:
            logging.warning(
                "[%s] Handle request [%s]%s failed: \n%s"
                % (
                    self.__class__.__name__,
                    request.method,
                    request.url,
                    traceback.format_exc(),
                )
            )
            return {"type": EnumProxyType.Continue}
        return result

    async def handle_response(
        self, response: "WebResponse"
    ) -> Optional[Dict[str, Any]]:  # pyright: ignore[reportDeprecated]
        """处理响应

        调用 callback 函数处理响应。支持同步和异步 callback。

        Args:
            response (WebResponse): WebResponse 响应对象

        Returns:
            dict: 包含自定义响应信息的字典，格式为 {
                'status': int,
                'headers': dict,
                'body': bytes
            }
            或者 None 表示不修改响应
        """
        try:
            result = self._callback(response)

            # 如果 callback 是协程，等待其完成
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as ex:
            logging.warning(
                "[%s] Handle response for %s failed: \n%s"
                % (self.__class__.__name__, response.url, traceback.format_exc())
            )
            return None

        return result

    def get_fetch_pattern(self) -> Dict[str, Any]:  # pyright: ignore[reportDeprecated]
        """获取用于 CDP Fetch.enable 的拦截模式

        Returns:
            dict: CDP Fetch 拦截模式
        """
        pattern: Dict[str, Any] = {  # pyright: ignore[reportDeprecated]
            "urlPattern": self._url_pattern,
            "requestStage": "Request",
        }
        if self._resource_type:
            pattern["resourceType"] = self._resource_type
        return pattern


class WebRequest:
    """Web请求封装类

    封装HTTP请求的基本信息，包括URL、方法、请求头和请求体。
    用于在代理处理中传递请求信息。
    """

    def __init__(
        self, url: str, method: str, headers: Dict[str, str], body: Optional[bytes]  # pyright: ignore[reportDeprecated]
    ) -> None:
        """初始化 WebRequest

        Args:
            url (str): 请求的URL地址
            method (str): HTTP请求方法（GET、POST等）
            headers (dict): 请求头字典
            body (bytes): 请求体内容
        """
        self._url: str = url
        self._method: str = method
        self._headers: Dict[str, str] = {key.lower(): value for key, value in headers.items()}
        self._body: bytes | None = body

    @property
    def url(self) -> str:
        """获取请求URL"""
        return self._url

    @property
    def method(self) -> str:
        """获取HTTP请求方法"""
        return self._method

    @property
    def headers(self) -> Dict[str, str]:  # pyright: ignore[reportDeprecated]
        """获取请求头字典"""
        return self._headers

    @property
    def body(self) -> Optional[bytes]:  # pyright: ignore[reportDeprecated]
        """获取请求体内容"""
        return self._body

    @property
    def content(self) -> str:
        if self._body:
            return self._body.decode("utf-8")
        return ""


class WebResponse:
    """Web请求响应类"""

    def __init__(
        self, request: WebRequest, status: int, headers: Dict[str, str], body: bytes  # pyright: ignore[reportDeprecated]
    ) -> None:
        """初始化

        Args:
            request: WebRequest 对象
            status (int): HTTP 状态码
            headers (dict): 响应头字典
            body (bytes): 响应体内容
        """
        self._request: WebRequest = request
        self._status: int = status
        self._headers = CIMultiDict((key.lower(), value) for key, value in headers.items())
        self._body: bytes = body

    @property
    def url(self) -> str:
        """获取响应对应的URL"""
        return self._request.url

    @property
    def status(self) -> int:
        """HTTP状态码"""
        return self._status

    @property
    def ok(self) -> bool:
        """判断响应是否成功

        Returns:
            bool: 如果状态码在200-299范围内返回True，否则返回False
        """
        return self._status == 0 or 200 <= self._status <= 299

    @property
    def headers(self) -> CIMultiDict[str]:
        """获取响应头字典

        所有header名称都是小写的。
        """
        return self._headers

    @property
    def body(self) -> bytes:
        return self._body

    @property
    def content(self) -> str:
        if self._body:
            return self._body.decode("utf-8")
        return ""

    @property
    def request(self) -> WebRequest:
        """获取关联的 Request 对象"""
        return self._request


class WebProxy(object):
    """Web Proxy

    使用代理规则处理网络请求拦截和响应
    """

    def __init__(self, page: pyppeteer.page.Page, proxy_rules: List[ProxyRule]) -> None:  # pyright: ignore[reportDeprecated]
        """初始化 Web 代理

        Args:
            page: pyppeteer.page.Page 实例
            proxy_rules: ProxyRule 对象列表
        """
        self._page: pyppeteer.page.Page = page
        self._client: Any = page._client
        self._proxy_rules: List[ProxyRule] = proxy_rules  # pyright: ignore[reportDeprecated]

    async def setup_interception(self) -> None:
        """启用网络请求拦截

        Chrome 129+ 已移除 Network.setRequestInterception，
        改用 Fetch 域拦截。响应阶段的规则不注册 CDP pattern，
        统一通过带外请求（utils.fetch）获取响应后再改写并 fulfill。
        """
        logging.info("[%s] Setup interception" % self.__class__.__name__)
        patterns: List[Dict[str, Any]] = [
            rule.get_fetch_pattern()
            for rule in self._proxy_rules
            if rule.stage == EnumProxyStage.Request
        ]
        await asyncio.gather(
            self._client.send(
                "Network.setCacheDisabled",
                {"cacheDisabled": True},
            ),
            self._client.send(
                "Fetch.enable",
                {"patterns": patterns},
            ),
        )
        self._client.on("Fetch.requestPaused", self._on_request_paused)

    def _on_request_paused(self, event: Dict[str, Any]) -> None:  # pyright: ignore[reportAny]
        asyncio.ensure_future(self.handle_fetch_event(event))

    async def get_http_response(self, request: WebRequest) -> Optional[WebResponse]:  # pyright: ignore[reportDeprecated]
        """通过aiohttp获取http返回包，返回WebResponse对象

        Args:
            request (WebRequest): WebRequest 请求对象

        Returns:
            WebResponse 对象
        """
        url = request.url

        # 获取请求头并转换为字典
        headers: Dict[str, str] = {}  # pyright: ignore[reportDeprecated]
        for key, value in request.headers.items():
            headers[key] = value

        try:
            # 使用 utils.fetch 替代 aiohttp 直接调用
            result = await utils.fetch(
                url=url,
                method=request.method,
                headers=headers,
                respond_with_headers=True,
            )
            status, response_headers, body = cast(
                Tuple[int, Dict[str, str], bytes], result  # pyright: ignore[reportDeprecated]
            )

            # 返回 WebResponse 对象
            return WebResponse(
                request=request,
                status=status,
                headers=response_headers,
                body=body,
            )
        except Exception as ex:
            logging.warning(
                "[WebProxy] Get HTTP response failed for %s: %s" % (url, str(ex))
            )
            return None

    def get_rule(
        self, request: WebRequest
    ) -> Tuple[Optional[ProxyRule], Optional[ProxyRule]]:  # pyright: ignore[reportDeprecated]
        """获取与request匹配的规则

        Args:
            request: WebRequest 对象

        Returns:
            tuple: (request_rule, response_rule)
        """
        request_rule: Optional[ProxyRule] = None  # pyright: ignore[reportDeprecated]
        response_rule: Optional[ProxyRule] = None  # pyright: ignore[reportDeprecated]

        # 遍历所有规则，查找匹配的规则
        for rule in self._proxy_rules:
            if rule.matches(request):
                if rule.stage == EnumProxyStage.Request and request_rule is None:
                    request_rule = rule
                elif rule.stage == EnumProxyStage.Response and response_rule is None:
                    response_rule = rule

        return request_rule, response_rule

    async def handle_fetch_event(self, event: Dict[str, Any]) -> None:  # pyright: ignore[reportAny]
        """处理 Fetch.requestPaused 事件

        遍历所有代理规则，找到第一个匹配的规则并调用其 callback，
        然后通过 Fetch 域的 fulfillRequest / continueRequest / failRequest 放行。

        Args:
            event: CDP Fetch.requestPaused 事件参数
        """
        request_id: str = event["requestId"]
        try:
            await self._handle_fetch_event(request_id, event)
        except Exception:
            logging.warning(
                "[%s] Handle paused request failed: \n%s"
                % (self.__class__.__name__, traceback.format_exc())
            )
            await self._continue(request_id)

    async def _handle_fetch_event(
        self, request_id: str, event: Dict[str, Any]  # pyright: ignore[reportAny]
    ) -> None:
        if event.get("responseStatusCode") is not None:
            # 响应阶段的规则走带外取包，无需 CDP 级拦截，直接放行
            await self._send("Fetch.continueResponse", {"requestId": request_id})
            return

        req: Dict[str, Any] = event.get("request") or {}  # pyright: ignore[reportAny]
        post_data: Optional[str] = req.get("postData")  # pyright: ignore[reportAny]
        web_request = WebRequest(
            url=req.get("url", ""),
            method=req.get("method", "GET"),
            headers=dict(req.get("headers") or {}),
            body=post_data.encode("utf-8") if post_data else None,
        )
        logging.info(
            "[%s] Handle request %s %s"
            % (self.__class__.__name__, web_request.method, web_request.url)
        )
        request_rule, response_rule = self.get_rule(web_request)
        if not request_rule:
            await self._continue(request_id)
            return
        result = await request_rule.handle_request(web_request)
        if not isinstance(result, dict):
            logging.warning(
                "[WebProxy] Unexpected return type %s from callback, continuing request: %s"
                % (type(result), web_request.url)
            )
            await self._continue(request_id)
            return
        proxy_type: int = result.pop("type", 0)
        if proxy_type == EnumProxyType.Block:
            await self._send(
                "Fetch.failRequest",
                {"requestId": request_id, "errorReason": "BlockedByClient"},
            )
            return
        elif proxy_type == EnumProxyType.Mock:
            await self._fulfill(request_id, result)
            return
        elif not response_rule:
            await self._continue(request_id, headers=result.get("headers"))
            return

        response = await self.get_http_response(web_request)
        if not response:
            # 没有获取到回包
            await self._continue(request_id)
            return
        result = await response_rule.handle_response(response)
        if result:
            await self._fulfill(request_id, result)
        else:
            await self._continue(request_id)

    async def _send(self, method: str, params: Dict[str, Any]) -> None:  # pyright: ignore[reportAny]
        try:
            await self._client.send(method, params)
        except pyppeteer.errors.NetworkError:
            # 页面跳转/关闭时 requestId 可能已失效
            pass

    @staticmethod
    def _to_header_entries(headers: Dict[str, Any]) -> List[Dict[str, str]]:  # pyright: ignore[reportAny]
        return [
            {"name": name, "value": value} for name, value in (headers or {}).items()
        ]

    async def _continue(
        self,
        request_id: str,
        headers: Optional[Dict[str, Any]] = None,  # pyright: ignore[reportAny]
    ) -> None:
        params: Dict[str, Any] = {"requestId": request_id}  # pyright: ignore[reportAny]
        if headers:
            params["headers"] = self._to_header_entries(headers)
        await self._send("Fetch.continueRequest", params)

    async def _fulfill(self, request_id: str, result: Dict[str, Any]) -> None:  # pyright: ignore[reportAny]
        body: Any = result.get("body") or b""  # pyright: ignore[reportAny]
        if isinstance(body, str):
            body = body.encode("utf-8")
        params: Dict[str, Any] = {  # pyright: ignore[reportAny]
            "requestId": request_id,
            "responseCode": result.get("status", 200),
            "body": base64.b64encode(body).decode("ascii"),
        }
        response_headers: Optional[Dict[str, Any]] = result.get("headers")  # pyright: ignore[reportAny]
        if response_headers:
            params["responseHeaders"] = self._to_header_entries(response_headers)
        await self._send("Fetch.fulfillRequest", params)
