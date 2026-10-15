<p align="center"><b>简体中文</b> | <a href="README_en.md">English</a></p>

<h1 align="center">Vibe AStock</h1>

<p align="center">
  <b>基于 OpenAI Codex Harness 的 A 股短线复盘与跟踪工作台</b><br>
  盘面观察 · 证据复盘 · 多空辩论 · 历史回测 · 本地网页
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.12-3776AB.svg?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/react-19-61DAFB.svg?logo=react&logoColor=white" alt="React">
  <a href="https://github.com/simonlin1212/vibe-astock/releases/tag/v1.1.3"><img src="https://img.shields.io/badge/version-v1.1.3-orange.svg" alt="v1.1.3"></a>
</p>

---

**看机会 · Open to Opportunities｜深圳 · 香港 · 远程**

我是 Simon，专注于 AI Agent 与实用工具开发，目前在看深圳、香港或远程的机会，欢迎联系：[simonlin0423@gmail.com](mailto:simonlin0423@gmail.com)

I'm Simon, building AI agents and practical tools. Currently open to opportunities in Shenzhen, Hong Kong, or remote — feel free to reach out: [simonlin0423@gmail.com](mailto:simonlin0423@gmail.com)

---

<p align="center">
  <a href="#产品定位">产品定位</a> · <a href="#七个工作模块">功能</a> ·
  <a href="#快速开始">快速开始</a> · <a href="#接入-ai">接入 AI</a> ·
  <a href="#数据与隐私">数据与隐私</a> · <a href="#验证范围">验证范围</a> ·
  <a href="CHANGELOG.md">更新日志</a>
</p>

**V1.1.3：修复 v1.1.2 的回归 —— 自托管网关启动时打印的 `http://0.0.0.0:<端口>` 重新可用；端口写错时的提示直接指向端口。**

统一 AI 接入、任务进度与证据核验，保留并完善短线复盘、盯盘、个股研究和历史回测。提供 Codex、Claude、WorkBuddy / CodeBuddy 订阅接入入口，以及 DeepSeek 等 API 配置；具体要求见[接入 AI](#接入-ai)。

---

## 产品定位

Vibe AStock 把 A 股短线研究中反复进行的工作放在一个本地网页里：查看盘面和昨日梯队，整理复盘证据，核对次日变化，再回看自己的记录与历史规则表现。

数据、程序计算、人工记录和 AI 解读分别呈现。行情与统计不依赖 AI 生成；AI 用于解释材料、对照不同观点和整理待验证条件。情绪分档是分析框架，不是客观事实，也不能由某一个指标直接推导交易结论。

通过本地网页启动器运行，在浏览器中查看行情、复盘、异动和任务进度。

### 界面预览

深色为默认主题，也保留浅色主题。侧栏按工作流程分组，二级栏目默认展开，可用小三角单独收起。

![深色网页复盘界面](assets/screenshots/09-web-review-dark-20260909.png)

截图摄于 2026-09-09，展示 2026-09-08 的公开盘面复盘和样本覆盖说明，不代表实时行情或收益案例。

## 七个工作模块

| 一级模块 | 二级栏目与主要内容 |
|---|---|
| 首页 | 聊天、分类功能入口、查看最近报告 |
| 盯盘 | 盘面数据（含自选行情）、实时动态、昨日梯队、盘中核验 |
| 复盘 | 复盘报告、首板分析、近5天热度、历史统计 |
| 资讯雷达 | 新闻聚合与事件概率，显示来源、时点和覆盖状态 |
| 个股研究 | 行情、估值、财报与研报资料；多空辩论 |
| 回测 | AI 整理历史规则、确认条件、程序计算和报告归档 |
| 我的股票 | 持仓自选、交易日志、研究记录 |

「接入 AI」位于侧栏底部。持仓自选在同一页上下展示；持仓来自交易日志，不再维护互相冲突的两份账。

### 盯盘：看今天，也看昨天留下的梯队

- **盘面数据**：指数、市场广度、资金与板块读数，以及同一自选池的行情。不同来源的市场范围、日期和单位要分别阅读。
- **实时动态**：从行情快照归集急拉急跌、封板开板等已发生事件，可按页面提供的范围和阈值筛选。快照轮询并非逐笔成交数据，不能保证捕获所有盘中变化。
- **昨日梯队**：固定前一交易日的涨停样本，可选全部、二板及以上、三板及以上。今天回落或行情缺失的成员仍保留，便于观察高位梯队的延续、断板和覆盖缺口。
- **盘中核验**：把复盘中的观察条件与当天有效快照对照。它回答“此前条件是否出现”，实时动态回答“刚才发生了什么”。历史场次随时能看，读取已有定稿记录与已保存快照；错过的历史盘中时点不能靠当前行情补造。

采集依赖本机服务运行和数据源可用。夜间、午休或过期行情不能冒充当场的有效盘中快照。

盘面数据中的限售解禁日历可查看未来或最近十个日历日，自选匹配在浏览器内完成。解禁数量使用本次解禁股数，占比使用总股本口径；接口失败会明确提示，不等同于没有解禁。数据源最多返回 500 条时会标明部分覆盖。

### 复盘：先确认日期、样本和证据

选择目标交易日后生成复盘，可以选择尚未生成报告的日期。页面区分所选日期、当前报告的交易日和生成时间；不会将已有报告的日期冒充新选择。支持进度、取消和刷新后继续查看任务状态；生成失败保留旧稿，同日重新生成保存版本，补做较早日期不会把最新报告倒退。

各路取数显示当前类别，单路等待上限为90秒，并受整场剩余时间约束；超时或取消会清理对应进程。AI生成耗时另计。

复盘围绕情绪、资金、题材、龙虎榜和龙头跟踪整理五个分项，再汇总依据与待验证条件。新报告可展开「查看依据」；数字展示与已支持的比较由程序处理，AI 解释受引用校验约束。旧稿保留原样，不因升级自动获得新的校验结论。

| 观察维度 | 看什么 | 阅读边界 |
|---|---|---|
| 市场广度 | 上涨、下跌、平盘及成交情况 | 不同统计范围不能混用分母 |
| 昨日强势股反馈 | 均值、中位数、翻红率、再次涨停率 | 是固定样本的反馈，不代表全市场收益 |
| 晋级与梯队 | 昨日不同板位今天的延续、断板和分布 | 缺行情要披露，单个读数不能决定情绪周期 |
| 亏钱效应与封板结构 | 回落、炸板、回封和封板质量 | 只解释来源实际覆盖的样本 |
| 题材与跨日比较 | 涨停原因、题材变化、历史观察条件 | 归因是待核对的解释，不是已证实因果 |

首板分析与近5天热度放在复盘内，提供当日结构与跨交易日背景。数据缺失、来源修订或结构变化会保留提示；已有原始归档便于追溯，但不保证外部来源永久可补取。

### 个股研究与多空辩论

个股研究集中展示行情、估值、财报和研报资料。多空辩论针对同一标的组织不同分析视角，分别检查积极解释、风险解释和反证，再汇总尚未解决的分歧；不强制选出赢家，不给参与倾向、买卖点位或仓位建议。

旧记录中的操作倾向与点位字段不再作为操作提示展示；旧正文没有自动重新校验，页面会标明旧记录边界。运行时长受来源、材料量和模型影响，不承诺固定几分钟完成。

### 历史统计与回测的区别

**复盘里的历史统计**按已有涨停样本观察次日表现、封板时间等分组差异。样本是事后整理的名单，存在选择偏差；样本统计不能当成实际可成交收益。

**独立的回测模块**用于检查明确的历史规则：输入条件 → AI 整理可执行范围 → 用户确认 → 确定性引擎计算 → 本地报告。AI 不替用户决定当前如何交易。报告按支持范围披露市场规则、数据区间、费用、成交与未成交原因；零成交也如实显示。没有数据或暂不支持的条件会说明限制，不用模拟结果伪装实测结果。

历史表现不能保证未来结果。港股旧报告若缺少当前市场规则字段，应按原条件重新运行，不能把旧结果当作新版规则已验收。

### 我的股票：保留自己的研究过程

持仓、已实现结果和费用以交易日志为基础；自选是手动维护的观察列表。行情缺失显示为不可用，汇总注明不完整，不能把缺行情算成资产归零。

交易日志支持结合市场环境、模式与执行记录进行分组自查，也可批量上传券商交易截图，经本机 RapidOCR 识别、人工校对后一次性入账。截图只在本机后端内存中处理，不发送给 AI 或第三方，也不保存原图；首次使用或升级后需重新运行安装步骤以补齐 OCR 依赖。账户风险按用户填写的条件计算；没有填写风险边界的持仓单列，已知部分不能冒充完整账户风险。研究记录支持保存、查找和导出。记录中的相关性不等于能够判定盈亏的因果。

## 快速开始

从 [v1.1.3 Release](https://github.com/simonlin1212/vibe-astock/releases/tag/v1.1.3) 获取源码并解压，在项目目录中按下面步骤启动。已有用户请先备份本地数据，更新源码后重新准备依赖。

环境要求：Python 3.10+（推荐 3.12）、Node.js 22+（含 npm）。首次准备环境需要联网下载依赖。

macOS 可双击 [启动 Vibe AStock.command](启动%20Vibe%20AStock.command)，按提示准备环境并打开本地浏览器。保留终端窗口，按 Control+C 停止服务。

macOS / Linux 也可在仓库根目录分步运行：

```bash
sh scripts/setup
sh scripts/doctor
sh scripts/start
```

Windows 10/11 可双击 [启动 Vibe AStock.cmd](启动%20Vibe%20AStock.cmd)，或在 PowerShell / CMD 的仓库根目录运行：

```powershell
py -3 -X utf8 scripts/manage.py setup
.venv\Scripts\python.exe -X utf8 scripts/manage.py doctor
.venv\Scripts\python.exe -X utf8 scripts/manage.py start
```

Windows 也在本机浏览器中使用；需要安装 Python（含 `py` 启动器）和 Node.js。保留启动窗口，按 Ctrl+C 停止服务；更换端口可在 `start` 后加 `--port 8911`。更新源码后重新运行上述 `setup` 和 `doctor`。

默认地址为 `http://127.0.0.1:8910`，只监听本机回环地址。端口被占用时可运行 `sh scripts/start --port 8911`。开发预览可使用其他端口；截图中的布局不依赖固定开发端口。

自行改为监听局域网地址或挂在反向代理后面时，需要把访问用的 Host 写进环境变量 `VIBE_ALLOW_HOSTS`（逗号分隔，可写在 `.env` 中），例如 `VIBE_ALLOW_HOSTS=192.168.1.10,astock.example.com`。未列出的 Host 会被全部接口拒绝并返回 403，页面表现为各处读取失败。`127.0.0.1`、`::1` 与 `localhost` 始终允许，不需要写入；其余回环地址（如 `127.0.0.2`）不在默认名单内。

先到「接入 AI」完成登录或填写自己的 API 配置，测试成功后保存。查看行情与已有记录不要求先生成 AI 报告；启动服务也不会自动生成复盘。连接测试和 AI 任务可能消耗所选服务的额度。

同页的「市场数据网络」统一控制腾讯、东财、新浪、巨潮、问财和研究数据的 HTTP 出口：推荐使用“自动”（WSL 直连失败后回退 Clash），也可固定直连或固定代理，并一键分别检测 WSL 直连和 Windows Clash。WSL NAT 网络下代理地址通常应填写 Windows 主机网关而非 `127.0.0.1`，同时需在 Clash 开启 Allow LAN。该设置只作用于市场数据请求，不修改进程级 `HTTP_PROXY` / `NO_PROXY`，不会改变 Codex、Claude 等 AI 连接。通达信和 Baostock 使用 TCP，不经过 HTTP 代理，失败时按各数据源既有降级策略处理。

源码更新前停止服务并备份下节列出的数据；保留本地修改，更新后重新运行 `sh scripts/setup` 与 `sh scripts/doctor`。日志位于 `.local/startup.log`，分享日志前检查个人信息。

本地后台提供 macOS、Linux 和 Windows 启动路径。Windows 原生安装、进程清理和网页重启的自动验收结果见[验证范围](#验证范围)；各供应商需使用自己的账户或密钥完成连接测试。

## 接入 AI

网页的 AI 入口共用「接入 AI」中测试保存的来源与模型。页面显示来源不等于当前账户仍能调用，应以连接测试和任务结果为准。

| 接入方式 | 当前实现与验证边界 |
|---|---|
| Codex 订阅 | 产品专用 ChatGPT 授权与隔离引擎；已有真实登录、连接及业务运行记录，模型可用性受账户额度限制 |
| Claude 订阅 | 本机官方 CLI 接入，已有真实复盘与辩论记录；需要自己的安装、登录和有效订阅，历史成功不代表当前账户状态 |
| WorkBuddy / CodeBuddy | 使用本机 CLI 登录；本次仅实测 macOS WorkBuddy 内置 CodeBuddy CLI 2.137.1 的连接、受控工具、普通对话与单日完整复盘流程 |
| OpenAI API | 使用用户自己的配置；不以订阅测试代替 API 实测，尚未完成全部兼容验收 |
| DeepSeek / MiMo | 提供模型与地址预设，支持自行修改；使用自己的密钥测试成功后保存 |
| CC Switch 本机路由 / 自托管网关 | 预设 `http://127.0.0.1:15721/v1`，密钥通常填 `PROXY_MANAGED`，模型按路由实际配置填写；本机回环、内网（RFC1918）、未指定地址 `0.0.0.0` 与 IPv6 唯一本地地址允许 http 任意端口，公网地址仍强制 HTTPS 标准端口。需路由支持流式 Responses 与工具调用 |
| 智谱 GLM / Kimi / 通义千问 | 提供阿里云百炼预设，需要填写工作空间地址和百炼密钥；须以实际连接测试为准 |
| 硅基流动 / MiniMax / OpenRouter / Groq / Together / 自定义 | 提供可编辑的地址与模型配置；预设不代表兼容性已验证，端点必须支持 Responses API 与工具调用 |

Agent 默认关闭：普通聊天不启用工具或联网。开启后，适用入口才会使用其允许的证据或公开数据工具；首页已有复盘证据、页面材料和显式允许的历史日线各有范围，不是开放任意联网与本机文件访问。生成复盘、辩论和回测仍需明确发起，不由开关自动执行。

所有网页 AI 来源均附加研究约束；普通聊天与报告路径对明确交易建议进行检查，证据回答另有引用和数字核对。这些检查能减少越界与错引，**不构成完整语义正确性保证**。产品要求不输出投资决策、买卖建议或仓位指令；历史规则中的买卖描述用于模拟，不是现实操作建议。

旧 `main.py` CLI 与可执行 Python Prompt 包保留供开发维护，参见 [PromptPack](duanxian/prompts.py)；其独立配置不是网页接入步骤，自定义代码也不属于官方网页约束保证。

## 数据与隐私

行情、涨停池、龙虎榜、板块、新闻与事件概率来自公开接口或用户配置的来源。问财密钥是可选补充，未配置不应被解释成整个复盘不可用；公开涨停原因渠道也可能不覆盖所有市场或历史日期。事件概率来自 Kalshi / Polymarket 等来源，显示采集时点、缓存年龄与部分覆盖状态；它不是事件已发生的证明，也不是产品给出的股票涨跌预测。

本地网页仍需要联网取数；使用远程 AI 时，问题、所选材料和必要上下文会发送给用户选择的供应商。不能把“本地保存”理解为“所有计算都离线”。

| 数据 | 默认位置与注意事项 |
|---|---|
| Agent 会话、任务、证据账本与产品登录 | `~/.vibe-astock-agent/`；可用 `ASTOCK_AGENT_HOME` 改变该根目录 |
| 复盘、版本、交易日志与原始归档 | `~/.duanxian-agents/`，包括 `reviews/`、`journal/`、`archive/` 等；可用 `ASTOCK_DATA_HOME` 指定绝对路径 |
| 连阳天梯本地日线与完整快照 | `ASTOCK_DATA_HOME/cache/growth_ladders.sqlite`；首次启动后台回补最近 20 个已收盘交易日，完整性未通过时页面只显示构建进度，不展示局部榜单 |
| 市场资料与盯盘缓存 | `~/.vibe-astock-agent/market-data/`；可通过 `VR_DATA_DIR` 指定 |
| 研报资料 | 默认在上述市场资料目录的 `myreports/`；`VR_REPORTS_DIR` 可单独指定 |
| 自选、研究记录、界面状态与 API 配置 | 当前浏览器站点的本地存储；更换浏览器、端口或清理站点数据会影响读取 |
| 开发日志与运行诊断 | 仓库 `.local/`，默认不进入版本控制 |

这些位置不是由一个开关统一迁移的：更改 `ASTOCK_AGENT_HOME` 不会自动搬走交易日志、市场资料或浏览器数据。备份应覆盖实际使用的目录及页面可导出的记录，并妥善保护其中的登录与密钥。不要将私人记录、API key 或登录文件提交到仓库；不要复制开发助手的登录文件来替代产品授权。

连阳天梯不再依赖问财额度生成名单：名单、层级、新高和断阳由本地全市场日线计算，问财只可作为行业标签增强。同步失败可在「复盘 → 更多天梯」点击刷新重试；服务会按 5、15、30 分钟退避继续补齐。

### 将业务数据放到其他磁盘

Windows PowerShell 中，在启动前设置（示例路径可自行调整）：

```powershell
$env:ASTOCK_DATA_HOME = 'D:\VibeAStock\reviews-and-journal'
$env:ASTOCK_AGENT_HOME = 'D:\VibeAStock\agent'
$env:VR_DATA_DIR = 'D:\VibeAStock\market-data'
python scripts/manage.py start
```

这些变量只影响从该终端启动的进程，需在每次启动前设置，或加入自己的启动脚本。macOS/Linux 可用 `export ASTOCK_DATA_HOME="/完整路径/业务数据"` 等对应变量。默认目录保持兼容；配置目录不会自动搬迁旧文件。迁移已有数据时，先停止服务、备份并将原目录内容复制到对应新目录，再设置变量启动核对；确认前保留原件。单独配置的 `VR_REPORTS_DIR` 也需同步调整。浏览器记录和仓库 `.local/` 运行日志不受这些变量影响。

## 架构与开发

使用官方 OpenAI Codex Harness 作为 Agent 底座，产品固定引擎版本，配套短线研究流程、受控证据工具、任务管理和引用校验。行情与回测计算由程序完成；AI 负责阅读材料、解释和追问。Claude 与 WorkBuddy / CodeBuddy 通过各自的本机 CLI 适配接入。

```text
公开数据 → 原始归档与口径整理 → 确定性统计 / 历史模拟
                              ↓
                     受控 AI 解读与证据工具
                              ↓
                     引用校验 → 报告版本与追问
人工交易日志与研究记录 → 本地台账和自查视图
```

| 目录 | 职责 |
|---|---|
| `frontend/` | React 网页、七模块导航、主题与交互 |
| `server.py`、`vr/` | 本地 HTTP 服务与市场数据页面接口 |
| `review_agent/`、`runtime/` | 统一模型接入、隔离引擎、任务、证据工具与研究约束 |
| `duanxian/` | 短线指标、复盘、核验、归档、交易日志与多空辩论 |
| `backtest/`、`research_data/` | 历史模拟引擎、配套取数与事件概率 |
| `scripts/`、`tests/`、`frontend/test/` | 初始化、体检、启动和回归验证 |

事件概率与回测所用代码的来源及许可见 [回测 NOTICE](backtest/NOTICE.md) 和 [取数 NOTICE](research_data/NOTICE.md)。

## 验证范围

v1.1.3 发布检查：macOS 后端三目录 **1287 项通过、4 项 Windows 专项跳过**，前端 **41 项通过**，类型检查与生产构建通过。回归包含 README、网页页脚、包配置和 API 的产品版本一致性检查。

2026-09-10 源码基线 `9d84e0d` 的 Windows Server 2025 原生验收通过 **34 项运行契约和87项业务回归**，以及全新环境准备、体检、捆绑引擎启动、两次真实 HTTP 网页/API 启动和强制退出后的重启。[查看 Windows 验收记录](https://github.com/simonlin1212/vibe-astock/actions/runs/34417311929)。

已有部分真实日期复盘、日期切换、页面问答和 A/H/US 历史回测与独立复算记录。上述结果不代表所有供应商、Windows 10/11 实体用户电脑、Linux 全流程或连续完整交易日均已验收；已使用 macOS WorkBuddy 内置 CodeBuddy CLI 2.137.1 完成一次单日复盘流程验证，并验证连接、受控工具、普通对话及取消/超时；独立安装的 CodeBuddy CLI、其他平台、全部模型及报告推断的正确性不在此次验证范围内。保留既有依赖弃用与构建体积提示。

在完成环境准备的仓库中可运行：

```bash
.venv/bin/python -m pytest tests backtest/tests research_data/tests -q
node --test frontend/test/*.test.ts
npm --prefix frontend run build
```

## 本 fork 的二开维护与会话接手说明

> 更新于 2026-09-28。本节记录当前本地 fork 的运行环境、已恢复能力、关键实现和接手顺序，供后续开发对话继续工作。它不是上游发布承诺；合并上游时仍以上游工程的正确性和新版功能可用性为第一优先级，再逐项恢复本节所列二开能力。

### 当前部署拓扑

- 仓库在 Windows 的 `D:\workspace\vibe-astock`，WSL 中对应 `/mnt/d/workspace/vibe-astock`。
- Python 服务运行在 WSL，Windows 浏览器访问 `http://localhost:8910`。排查问题时必须区分“Windows 浏览器能访问”与“WSL 服务能出站取数”，二者不是同一条网络路径。
- Windows 使用 Clash。市场数据统一由「接入 AI → 市场数据网络」控制，推荐保持“自动”：先尝试 WSL 直连，连接失败、被重置或受限后再尝试 Clash。不要在腾讯、东财、新浪、巨潮、问财等功能中各自增加代理逻辑。
- 当前环境曾检测到 `http://127.0.0.1:7890`，但 WSL NAT、镜像网络和 Clash 的 Allow LAN 设置变化后地址可能不同；新会话应以设置页的一键诊断结果为准，不能硬编码这个地址。
- HTTP 市场数据走 `net_utils.py` 的统一网络缝隙；通达信与 Baostock 使用 TCP，不经过 HTTP/Clash 代理。

### 本轮上游合并后的恢复顺序与状态

本 fork 曾在合并上游后丢失部分二开界面与数据链。恢复顺序固定如下，后续再次同步上游时也按此顺序回归：

1. **历史复盘核心**：红绿灯、梯队详情、次日跟踪。
2. **昨日强势股反馈**：排序、勾选、批量深挖、龙虎榜、导出、备注。
3. **定时复盘设置**：启停、执行时间、失败状态与重试。
4. **股票跳转与题材诊断**：统一股票链接、板块归属、概念成分与热门概念命中。

截至本节更新时间，上述界面已恢复。历史报告与深挖报告按版本保存，不会被新版算法自动改写；需要新版字段时应重新生成报告，不能把旧报告当作已重新验收。

本轮还完成了以下二开修复：

- 复盘历史场次重新展示红绿灯、梯队抽屉、同行业/概念成员、昨日强势股反馈和次日观察数据；失败来源保留明确提示，不用空值伪装正常。
- 个股弹窗直接复用个股研究页的 `StockConceptCard`，展示同一套“板块归属 · 概念”和“当下热门概念命中”，禁止另写一套概念判断算法。
- 多空辩论保存不可变历史版本，支持按标的查看多次报告和经确认后删除；“最近完成报告”只是快捷入口，不代表只保存一份。
- 深挖题材数据直接调用 `vr/astock.py` 中现有的 `concept_blocks()` 与 `hot_concepts()`；问财只做补充。东财 `slist` 使用相同算法在官方镜像间降级，单次连接重置不能被解释为“该股没有概念”。
- 问财密钥可在设置页保存、替换、清除。密钥写入 WSL 当前用户的 `~/.duanxian-agents/credentials.json`，文件权限为 `0600`；API 只返回是否配置和来源，绝不回显密钥。旧 `.env` 仍兼容，设置页的“清除并停用”可以显式覆盖旧 `.env` 值。
- 问财密钥会按最小范围传给需要它的复盘/深挖隔离 worker。此前“设置页显示已配置，但深挖子进程取不到”的问题已修复。
- WSL 服务没有继承 NVM PATH 时，会从 `~/.nvm/versions/node/*/bin/mcporter` 定位命令。当前机器尚未给 mcporter 配置名为 `exa` 的 MCP server，因此 Agent-Reach 全网资讯属于可选缺口；它不得阻断板块归属、热门概念或问财题材结果。
- 网络诊断现在检查真实使用的东财板块归属端点，而不是只检查一个无关的延时行情端点。新浪延迟可能因 DNS/TCP/TLS/CDN 抖动出现秒级变化，应对照直连与代理的多次结果判断，不能凭一次耗时认定代码故障。

### 接手时先做的检查

不要一上来重新合并、覆盖目录或重写算法。先保留当前脏工作区并确认运行事实：

```bash
cd /mnt/d/workspace/vibe-astock
git status --short
curl -sS http://127.0.0.1:8910/api/health
curl -sS http://127.0.0.1:8910/api/settings
curl -sS "http://127.0.0.1:8910/api/blocks?code=603248"
curl -sS "http://127.0.0.1:8910/api/hot-concepts?code=603248"
curl -sS -X POST http://127.0.0.1:8910/api/settings/market-network/diagnose
```

`/api/settings` 不得返回任何密钥明文。若接口正常但浏览器仍显示旧界面，先确认 `frontend/dist/` 已重新构建并强制刷新 Windows 浏览器；若浏览器能开页面但取数失败，再检查 WSL 内的诊断结果、服务日志和 Clash 路由。

验证深挖题材 worker 时可使用一个公开标的做只读检查：

```bash
timeout 70s .venv/bin/python -m review_agent.public_worker get_theme '["603248","锡华科技"]'
```

期望结果至少包含“板块归属 · 概念”和“当下热门概念命中”。配置了有效问财密钥时还应出现“问财补充”；Agent-Reach 不可用只能作为独立提示，不能把前述结果清空。

本轮修复的重点回归命令为：

```bash
.venv/bin/python -m pytest \
  tests/test_iwencai_settings_and_deepdive.py \
  tests/test_market_network.py \
  tests/test_market_data_fallbacks.py \
  tests/test_deepdive_alignment.py \
  tests/test_theme_reasons_gateway.py \
  tests/test_issue_regressions.py -q
npm run build --prefix frontend
git diff --check
```

2026-09-27 的最后一次相关回归为 **73 项通过**，前端生产构建通过；这只是本轮相关测试，不等同于仓库全量测试。构建仍有既有的大 chunk 提示，pytest 在当前挂载盘上可能因 `.pytest_cache` 权限产生警告，二者当时均未导致失败。

### 关键代码入口

| 入口 | 维护职责 |
|---|---|
| `net_utils.py`、`duanxian/app_settings.py` | 市场数据统一直连/代理策略、状态与诊断 |
| `duanxian/data_credentials.py` | 问财私有密钥落盘、停用覆盖与只返回状态 |
| `duanxian/fetchers.py`、`duanxian/theme_reasons.py` | `.env` 兼容、问财网关、缓存与熔断状态 |
| `review_agent/public_worker.py` | 隔离取数 worker 的最小环境和密钥传递边界 |
| `duanxian/deepdive/data.py` | 多空辩论使用的行情、K 线、龙虎榜和题材材料 |
| `vr/astock.py` | 个股研究与深挖共用的板块归属、热门概念等真实数据算法 |
| `frontend/src/components/StockConceptCard.tsx` | 个股研究与复盘个股弹窗共用的概念展示 |
| `frontend/src/pages/Settings.tsx`、`server.py` | 网络/问财设置界面及受鉴权、来源校验保护的 API |
| `review_agent/deepdive.py`、`review_agent/api.py`、`frontend/src/pages/DeepDive.tsx` | 多空辩论历史版本、读取与删除 |

### 后续修改约束

- 同步上游前先记录本地状态并备份用户数据；优先逐文件合并，不整目录覆盖 `vr/`、`duanxian/`、`review_agent/` 或前端页面。
- 不要为某个页面私建代理开关；所有 HTTP 市场数据必须复用统一网络层。AI 登录网络与市场数据网络保持隔离。
- 不要将数据源异常转换成零、空榜或正常状态。报告必须区分“真实为空”“接口失败”“历史日期不可补取”和“样本范围有限”。
- 不要复制“板块归属/热门概念”算法。个股研究、复盘弹窗和深挖报告应共享现有函数或组件。
- 不要在聊天、README、日志、测试夹具或 Git 中写入真实问财 key、AI key、OAuth token 或代理账号密码；需要更新问财 key 时使用设置页。
- 诊断或修复数据问题时先复现并写回归测试，再修改实现；真实接口验证只做必要的只读请求，并注明交易日、历史日和实时行情的口径差异。
- 工作区可能包含用户尚未提交的二开修改。接手者必须先查看差异并保留无关改动，禁止使用 `git reset --hard` 或整树覆盖来“恢复干净”。

## 更新日志

产品更新见 [CHANGELOG.md](CHANGELOG.md)。

## 免责声明

本工具用于数据整理、研究和历史复核，不提供投资决策或交易指令。公开数据、程序计算、人工记录与 AI 解读均可能出错；引用存在不代表推论正确，历史结果不代表未来表现。投资有风险，应独立核实资料并自行判断。

## License

Apache-2.0，详见 [LICENSE](LICENSE)。

**作者：** Simon 林 · X [@linsizhen](https://x.com/linsizhen) · 邮箱：[simonlin0423@gmail.com](mailto:simonlin0423@gmail.com)
