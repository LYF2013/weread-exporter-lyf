# 项目约定

## 目录与提交范围

- `weread_exporter/` 放导出器源码，保持现有模块划分；`tests/test_*.py` 放离线回归测试。
- `.claude-plugin/` 放现有插件入口、协议适配和使用说明；`skill.md` 放导出操作指引。
- `README.md` 记录安装和使用方式，`ROADMAP.md` 记录进度、验证依据与待办。
- `cache/` 放登录态与章节缓存，`output/` 放导出文件，均不得提交或因排查问题清空。
- 上游对照副本、截图、网页快照、日志和临时登录测试不进入版本库；新增持久目录前先补充约定。
- 原始工作区保留不动；本分支以目标仓库 `LYF2013/weread-exporter-lyf` 的 `main` 为基线，不合并无共同祖先的旧仓库历史。

## 修改纪律

- 只修改当前任务相关内容，不顺手重构；删除、回滚、修改远端、push 均须用户明确授权。
- Cookie、密钥、token、个人阅读内容不进入源码、文档或日志；测试仅使用明确的虚构凭据。
- 离线测试不读取真实账号凭据；真实扫码与书籍翻章由用户配合验证。
- 优先使用标准库和现有依赖，不为验证安装新依赖。
- 默认精确暂存文件，检查 staged diff 后提交；提交信息使用中文和明确的 `fix:`／`docs:` 等前缀，不添加协作者署名。

## 验证与交付

- 定向验证：`python -m unittest tests.test_webpage tests.test_webproxy tests.test_lifecycle tests.test_export -q`。
- 全量离线验证：`python -m unittest discover -s tests -q`。
- 现有函数式测试：`python -c "from tests.test_utils import test_wr_hash; test_wr_hash()"`。
- 语法验证：`python -m compileall -q weread_exporter tests .claude-plugin`。
- Windows 导出模块测试使用已有 `patch_windows()` 加载仓库附带的 DLL；不修改系统环境配置。
- Node.js 可用时执行实际 hook 脚本的独立 JavaScript 环境测试；不可用时必须明确报告跳过。
- 完成后同步 `ROADMAP.md`，区分当前分支离线验证、迁移前用户实际导出反馈与未验证场景。
