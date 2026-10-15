# v1 / v2 并存

根目录是远端 v2 (`d5bf80d2ed1629f8fa602058093f8d41bb0c7ace`)；`v1/` 是本地
`57b0ef300c255a21817185e356e9f5b1f5e367a1` 的完整已提交源码快照，包含此前全部 6 个提交。
不是只保留 83 个残余文件：异动中心、成长阶梯、龙虎榜、板型研究、日志截图导入等所依赖的
Python 服务、前端组件、启动脚本、测试和依赖声明也都在这里。

| 项目 | v2（默认） | v1（独立保留） |
|---|---|---|
| 源码 | `desktop/`、`orchestrator/` | `legacy/v1/frontend/`、`legacy/v1/server.py` |
| 启动 | `scripts/start.cmd` | `powershell -ExecutionPolicy Bypass -File scripts/start-v1.ps1 -Python <Python路径>` |
| 端口 | 页面 5940，API 8770 | 页面与 API 8910 |
| Node 依赖 | v2 各自的 `node_modules` | v1 各自的 `node_modules` |
| Python | v2 安装环境（本机验证使用 `.local/venv-v2`） | `legacy/v1/.venv` |
| 数据 | 根目录 `.local/` | 包装启动器默认 `legacy/v1/.local/data/` |

v1 启动器首次运行会调用 `scripts/setup-v1.ps1`，按 v1 自己的依赖声明安装环境并构建页面。`-Python` 指向 Python 3.11+
可执行文件（建议 3.12），不与 v2 的 Python 包混用。`-NoBrowser` 可禁止自动打开浏览器。
Linux/macOS 可在 `legacy/v1` 内运行原有 `scripts/setup` / `scripts/start`；先将
`ASTOCK_DATA_HOME` 和 `VR_DATA_DIR` 设为该目录下独立的绝对数据路径。

旧版功能从 v1 地址访问，尚未改写接入 v2 导航。两套服务禁止相互导入源码、共享虚拟环境或
共用可写的数据目录。已有用户数据未搬动；需要导入时使用 v2「接入 AI → 从旧版导入」的预览流程。

`v1/` 内保留原版文档和配置，它们的相对路径以 `legacy/v1/` 为根，而非仓库根。
旧截图、Docker 配置与旧 CI 文件只是版本快照；根目录的 v2 启动与 CI 不调用它们。

## 完整性验证

在仓库根运行 `node scripts/verify-coexistence.mjs`，逐文件验证 v1 的 Git blob 哈希，
并检查两套入口和依赖没有交叉引用。安全分支 `codex/pre-remote-restructure-20261015`
也指向完整原版。快照不包含未跟踪的 `.env`、用户数据、依赖或构建产物。
