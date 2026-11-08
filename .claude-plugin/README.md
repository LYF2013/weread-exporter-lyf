# 插件入口

本目录保留已有独立导出入口和 MCP 适配，不包含个人配置、凭据或全局安装操作。

## 文件

- `skill.py`：接收 JSON 参数，调用项目导出命令；工作目录固定为仓库根目录。
- `mcp_server.py`：通过标准输入输出提供 `export_weread_book` 工具。
- `plugin.json`：沿用原插件标识与作者信息，不改变项目来源署名。
- 根目录 `skill.md`：导出步骤与参数说明。

## 独立调用

先在仓库根目录执行 `python -m pip install -e .`。然后运行：

```bash
python .claude-plugin/skill.py --skill-input '{"book_id":"<book_id>","formats":["epub","pdf"],"force_login":true,"login_timeout":120}'
```

Windows 终端按其 JSON 引号规则传参。无参数执行 `python .claude-plugin/skill.py` 可查看帮助。

## MCP 适配

在支持该适配器的客户端中指定 Python 解释器和本机仓库的 `.claude-plugin/mcp_server.py` 绝对路径。启动命令为 `python .claude-plugin/mcp_server.py`。客户端配置由使用者自行维护，本仓库不自动写入或提交 `.mcp.json`。

两个入口均支持 `book_id`、`formats`、`headless`、`force_login`、`load_timeout`、`load_interval`。独立入口还支持 `login_timeout`。默认强制登录；首次扫码不要使用无头模式。`load_interval` 默认６０秒，章节间隔实际６０～１５０秒，章内翻页为３０～６０秒。

入口参数和子进程路径有离线测试覆盖；未声称所有客户端均已实测。真实书籍导出和客户端连接验证状态见根目录 `ROADMAP.md`。
