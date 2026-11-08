# 微信读书导出工具

## 实现原理

通过Hook Web页面中的Canvas函数，获取绘制到Canvas中的文本及样式等信息，转换成markdown格式，保存到本地文件，然后再转换成最终的epub或pdf格式，而mobi格式则是使用kindlegen工具从epub格式转换来的。

## INSTALL

仓库：`git@github.com:LYF2013/weread-exporter-lyf.git`。需要本机安装 Chrome／Chromium 和 Python 依赖。

```bash
$ pip3 install -e .
```

## USAGE

```bash
$ python -m weread_exporter -b $book_id -o epub -o pdf --force-login --login-timeout 120
```

> 获取书籍ID的方法：在页面`https://weread.qq.com/`搜索目标书籍，进入到书籍介绍页，URL格式为：`https://weread.qq.com/web/bookDetail/08232ac0720befa90825d88`，这里的`08232ac0720befa90825d88`就是书籍ID。

`-o` 参数可重复指定，支持 `md`、`epub`、`pdf`、`mobi`、`txt`。最终文件位于 `output/`，章节 Markdown 位于 `cache/<book_id>/chapters/`。MOBI 转换仅支持 Linux。

`epub`格式适合手机端访问，`pdf`格式适合电脑端访问，`mobi`格式适合kindle访问。

命令行还支持一个可选参数`--force-login`，默认为`False`，指定该参数时，会先进行登录操作。

### 登录与续跑

首次运行或会话失效时添加 `--force-login`，在工具打开的浏览器中扫码；此时不要添加 `--headless`。已有有效会话时可省略登录参数。保留 `cache/`，续跑会跳过已有章节，不会主动退出登录。登录有效不代表拥有付费章节的阅读权限。

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--login-timeout` | 60 | 扫码等待时间，单位秒 |
| `--load-timeout` | 60 | 章节加载超时，单位秒 |
| `--load-interval` | 60 | 章节间隔下限，实际为下限加随机０～９０秒，即默认６０～１５０秒 |

章内每次点击「下一页」前独立随机等待３０～６０秒，不受 `--load-interval` 影响。程序读完本章各页后再进入下一章。代码更新后需重新启动进程。

### 提取异常与退出

`canvasContextHandler is not defined` 表示提取脚本不可用，不是字体预加载警告。hook 在阅读页站点脚本运行前注入；章节导航由浏览器直接请求。翻页或读取正文前最多等待１０秒确认 hook 就绪，失败明确报错，不保存该章。

浏览器重启时重新安装 hook 和代理。每本书最多尝试３次浏览器会话，仍失败则退出并保留错误。按一次 `Ctrl+C` 后等待异步清理，只关闭本次启动的浏览器，不删除章节缓存。

遇到异常请保留章节名、时间和脱敏错误信息，不要分享 Cookie 文件、书籍全文或未脱敏的完整日志。

## 插件入口

保留 `.claude-plugin/skill.py` 独立入口与 `.claude-plugin/mcp_server.py` MCP 适配。使用方式见 `.claude-plugin/README.md`，导出指引见 `skill.md`。本仓库不携带个人 MCP 配置或自动安装脚本。

## 开发验证

```bash
python -m unittest discover -s tests -q
python -c "from tests.test_utils import test_wr_hash; test_wr_hash()"
python -m compileall -q weread_exporter tests .claude-plugin
```

测试不访问微信读书账号。Node.js 可用时会执行实际 hook 的 JavaScript 冒烟测试。开发约定见 `AGENTS.md`，验证进度和边界见 `ROADMAP.md`。

## 免责申明

本工具仅作技术研究之用，请勿用于商业或违法用途，由于使用该工具导致的侵权或其它问题，该本工具不承担任何责任！
