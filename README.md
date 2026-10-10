# SupplySync · 赛题二 B 可运行决策工作台

这是一个独立的 Streamlit Web Demo，位于桌面 `赛题二B_前端Demo`。它使用固定、校验过的合成供应链数据与队友**本次改版**的预测/补货/仿真源码，演示「历史需求 → 30 天预测 → 首日补货建议 → 双策略仿真 → Agent 工具分析」闭环。没有导入旧 `experiment_report.md`、`HANDOFF.md` 或旧 `agent.py`，页面指标均在当前版本重算；不宣称企业真实收益。

## 启动与功能

本机双击 `启动Demo.cmd`，或在已安装 Python 3.11+ 的环境中执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe bootstrap.py streamlit
```

默认访问 `http://127.0.0.1:8501`，保持启动窗口开启。端口冲突可运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_demo.ps1 -Port 8502`。Dockerfile 支持后续部署到能持续运行 Python 服务的主机；**纯静态托管（例如 Cloudflare Pages 静态站）不能运行此计算型 Streamlit Demo**。本项目尚未公开部署，不应把本机地址称为公网网址。

四个页面标签：

1. **网络与库存**：15 节点、14 线路、初始库存/仓容和运输约束。选择节点会同步其余页面。
2. **需求与预测**：历史需求、促销标记、最近 28 天历史与未来 30 天预测；用历史末 28 天单次留出验证，与重复训练期最后一周同星期需求的朴素基线比较 WAPE/MAE。预测只读取历史和已知日历。显示 S1/S2 第 1 天的**请求下单量**、库存位置、仓容、起订量和整批约束；不冒充实际发运或到货。
3. **仿真对比**：先选六个场景之一，再点「运行 30 天双策略对照」。两策略共享初始库存、外部日需求和成本；逐日检查库存、终端需求=销售+失销、每条边的订单/发货/欠单守恒、成本分项。展示总成本、缺货率、周转次数、成本分项、节点逐日现货、失销和牛鞭系数。协同策略在某些场景可能更差，页面如实显示。切换场景后旧结果不会冒充新场景结果。
4. **Agent 与证据**：离线「证据模式」实际调用只读的库存、预测、补货和仿真工具，输出可展开/下载的参数与结果，明确不是大模型。可选 DeepSeek 模式使用官方兼容的 Chat Completions function calling；仅在服务端配置 `DEEPSEEK_API_KEY` 后启用。未联网/未配置时仍可演示证据模式；**尚未完成真实 DeepSeek 端到端密钥测试**。

旧的输入资料静态预览已移除。完整功能请打开 Streamlit 工作台；直接双击项目文件不能运行预测、仿真或 Agent。

页面侧栏和首屏使用用户提供的数联科技 Logo 图片 `assets/shulian-tech-logo.jpg`；SupplySync 仍是参赛作品名称，页面不宣称自己是数联科技官方产品。移交或公开发布时请一并包含该图片，并确认赛事展示范围内的标识使用授权。

## 数据和计算边界

`data/` 是原合成输入的展示副本，没有 `future_demand.csv`。`engine_input/` 是计算输入固定副本，其中的 `future_demand.csv` **只由仿真器实现逐日实际需求**；预测和补货策略接口不接收它。项目不是对外公开的盲测平台；如果未来公开源码仓库，不应把其中合成未来真值误认为保密测试集。

`engine_manifest.json` 固定了本次改版 8 个计算输入和 6 个计算源码文件的 SHA-256；启动时逐一校验，7 个共享输入还需与展示副本逐字节同哈希。输入展示快照 ID 为 `e697ed76be457e7f`，计算引擎签名为 `483aeef75502f1e0`。任何源码或输入变更，都必须先重新复核算法/结果，再更新签名；直接改清单绕过检查不是验收。

仿真固定 30 天、**warmup_days=0**。旧版中可选 `warmup_days>0` 尚有时钟错误，因此本 UI 不开放。S1 是分散观察下游订单策略；S2 共享子树终端预测的汇总值，不是全局最优规划，也没有隐私数学保证。周转次数采用「窗口终端实际销量 ÷ 全系统日终平均现货」；牛鞭按各层每日订单总量方差与终端需求方差之比。合成场景的单次对照不等于真实部署效果或统计显著结论。

校验只覆盖当前固定网络/场景和实际记录到的状态，不代表所有潜在扰动都得到证明。第 1 天建议单只是事前请求量；页面没有把日志未记录的逐日在途量、分配比例或反事实收益伪装成结果。

## Agent 密钥与安全

可在启动进程的环境变量中设置 `DEEPSEEK_API_KEY`，也可放在不提交的 `.streamlit/secrets.toml`：

```toml
DEEPSEEK_API_KEY = "请在本机填入真实密钥"
```

**不要**把密钥发给队友、写进源码、README、Git 仓库或聊天。DeepSeek 模式只允许四种只读工具，限制节点/场景为当前选择，最多 8 次调用、4 轮，每次网络请求超时 30 秒；若没有成功的工具调用，会拒绝无证据回答。模型文字仍可能出错，展示时以原始工具结果和仿真指标为准。当前未验证实际 API 的账户、可用性和费用。

官方技术文档：[DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)；[工具调用指南](https://api-docs.deepseek.com/guides/tool_calls/)。

## 项目结构与交接

```text
app.py                      Streamlit 入口
demo/provider.py            展示输入读取与复验
demo/engine.py              固定引擎校验、预测、首日计划、双策略仿真与审计
demo/agent_service.py       业务工具与可选模型调用
demo/views.py               四页交互与证据展示
demo/charts.py              网络与历史图
engine_input/               固定计算数据（含仅供模拟器使用的合成未来真值）
vendor/supply_chain/        本次改版的原始计算源码副本，未修改
engine_manifest.json        计算版本哈希与展示快照关联
data/ + vendor/supply_data/ 展示输入及数据生成/校验源码副本
reports/validation_report.json  合成输入独立复验报告
tests/                     输入、引擎和 Agent 服务回归检查
Dockerfile                  Python 容器部署模板
```

本机使用 `bootstrap.py` 自动接入项目 `.runtime/packages`；交接时不必发送 `.runtime` 和 `.venv`，对方安装 `requirements.txt` 即可。运行全部自动检查：

```powershell
python bootstrap.py test
```

在当前受限 Windows 沙箱中，Streamlit `AppTest` 会卡在 asyncio socketpair 初始化，故该单项 UI 自动化明确跳过；其余自动检查覆盖六场景双策略逐日审计、版本篡改拦截与 Agent 工具调用模拟。**尚需在实际浏览器进行完整交互和移动端视觉验收**。旧 `docs/接入合同.md` 与 `docs/仿真结果接口_最小对接规格.md` 属于前期草案，不是此版本已实现 API 的说明；以本 README 和当前源码为准。

## 后续上线

部署需要可持续运行 Python/Streamlit 的容器或虚拟机、 HTTPS、服务端密钥管理及运行资源。不能把本地 `127.0.0.1` 当作已公开的完整站点。公网部署前还应增加访问控制、速率限制、部署时文件权限核查和现场浏览器验收；不要直接暴露模型密钥或计算输入下载接口。
