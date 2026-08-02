# AI 圆桌辩论 (AI Roundtable)

*[English](README.md) · [项目主页](https://qzxp27.github.io/ai-roundtable/)*

一个本地自建的辩论聊天室：**Claude、Gemini、ChatGPT 和 DeepSeek 各自扮演指定
角色，围绕一个话题互相辩论**，由你实时主持。前三家通过官方的 Claude Code、
Antigravity (Gemini) 和 Codex (OpenAI) 命令行工具运行，因此这些发言除了你已有
的订阅之外不产生任何额外费用。DeepSeek 是唯一的例外——它没有提供订阅制命令行
工具，所以走 API，价格约为前沿模型的 1%。

![status](https://img.shields.io/badge/status-personal%20project-blue)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![providers](https://img.shields.io/badge/providers-4-brightgreen)
![license](https://img.shields.io/badge/license-AGPL--3.0-green)

## 为什么要做这个

向一个模型提问，你只会得到一个视角。这个项目同时启动多个模型，每个锁定在不同
的立场上，强制它们针对彼此最有力的论点正面回应——每轮结束后给出中立总结，最后
给出综合结论。适合用来压力测试一个决定、从你自己想不到的角度审视问题，或者单纯
看几个前沿模型吵架。

每轮结束时系统会判定共识程度（`unanimous` 一致 / `leaning` 倾向明显 /
`split` 两派对立 / `contested` 仍有争议），并指出**唯一那个一旦解决就能了结分歧
的关键问题**；辩论结束时给出一个明确的建议和置信度。目标是让你结束时需要权衡的
东西**变少**，而不是变多。

> **这一点很关键。** 三个模型输出三大段文字再加一段四平八稳的总结，只会让你要
> 判断的东西更多。所以本项目强制系统"表态"：必须收敛到一个结论、一个关键分歧点、
> 一个置信度。

## 架构

```
浏览器 (WebSocket)  <->  FastAPI 服务  <->  DebateEngine  <->  claude -p / agy -p / codex exec / DeepSeek API
      static/index.html      server.py        engine.py            clients.py
                                   |
                             pdf_export.py  ->  可下载的 PDF 记录
```

- **`clients.py`** — 对 `claude`、`agy`（Antigravity/Gemini）、`codex`
  (OpenAI) 命令行工具无头模式的异步封装，外加一个给 DeepSeek 用的轻量 HTTP
  客户端。命令行工具用各自已登录的身份；DeepSeek 读取密钥文件或
  `$DEEPSEEK_API_KEY`。文件顶部的 `PROVIDERS` 表是唯一的注册表——新增一个供应商
  只需在那里加一行，再写一个 `call_*` 函数。账号面板的连接状态检查也在这里。
- **`engine.py`** — 辩论状态机：轮次、发言顺序、记录拼装、每轮总结、主持人插话、
  最终结论、会话自动保存，以及导出 Markdown。
- **`pdf_export.py`** — 把当前或历史会话渲染成排版好的 PDF。
- **`server.py`** — FastAPI 应用，通过 WebSocket 向浏览器推送辩论事件，另有 PDF
  导出和参考资料上传的接口。
- **`static/index.html`** — 单文件原生 JS 前端，无需构建。

## 功能

- **多模型、多角色辩论** — 可任意混搭 Claude、Gemini、OpenAI、DeepSeek 的参与者，
  每位都可单独设置模型档位和角色设定。
- **实时主持** — 随时插话；你的发言会在下一位模型发言之前插入记录，因此所有参与者
  都能看到并作出回应。
- **每轮总结** — 每轮结束由 Opus 撰写：共识点、仍存的分歧、新出现的洞见、待解决的
  问题，然后暂停等你决定。
- **强制收敛** — 每轮标注共识程度（一致／倾向／对立／争议），并点明那个关键分歧；
  收尾时先给出一句明确建议加置信度，而不是把两边观点重述一遍。
- **联网搜索** — 参与者可在辩论中搜索网络，论据不局限于训练数据。
- **参考资料（PDF）** — 开始辩论时可上传一个或多个 PDF，正文会被提取并注入到每位
  参与者的上下文中作为共享资料。
- **随时开始新辩论** — 中途开新局会先确认，然后放弃当前这局（记录仍会保存并标记为
  `abandoned`），不必被迫等它收尾。
- **预设阵容** — 保存／复用参与者阵容与角色设定；内置"正方 vs 反方"和三方辩论两套
  预设。
- **历史会话** — 每场辩论自动保存为 JSON 到 `transcripts/`，点击任意历史记录即可
  回看。
- **导出** — 一键把 Markdown 记录写入 Obsidian，或下载排版好的 PDF（标题、分隔线、
  粗体、中文支持）。导出内容始终跟随你当前查看的会话，无论是进行中还是历史的。
- **明暗主题 + 中英文界面切换**，刷新后都会记住。
- **账号面板** — 一眼看清每个供应商的连接状态，并给出登录所需的确切终端命令和
  重新检查按钮。
- **分层的模型成本** — 辩论发言默认用便宜、快的模型；总结和最终结论用 Opus，因为
  综合判断的质量在这里最重要。只有 DeepSeek 的发言产生实际花费，而且只有前沿模型
  价格的约 1%。

## 环境要求

- **macOS。** 应用本身在任何能跑 Python 的系统上都能运行，但有两个功能依赖 macOS
  特有的路径，在其他系统上会静默降级——见[平台说明](#平台说明)。
- Python 3.11+
- 至少配置两个供应商（只需配置你实际想用的那些——如果某个供应商没连上，只有那一位
  参与者的发言会变成一条错误提示，整场辩论不会中断）：
  - **Claude**：[Claude Code CLI](https://claude.com/claude-code)，需要 Claude
    Pro/Max 订阅。
  - **Gemini**：Antigravity CLI（`brew install antigravity-cli`），需要带 Gemini
    订阅的 Google 账号。（Google 已于 2026 年 6 月停用旧版 Gemini CLI 的个人登录，
    `agy` 是其继任者，提供 gemini-3.x 系列模型。）
  - **OpenAI**：Codex CLI（`brew install --cask codex`）。用 ChatGPT 账号登录即可，
    免费档也能用，但本项目每位参与者每次交换都会调用一次，免费额度会比较紧张。
    也可以用 API 密钥，但那是按量计费，不走订阅额度。
  - **DeepSeek**：没有命令行工具。在
    [platform.deepseek.com](https://platform.deepseek.com) 申请密钥，然后二选一：
    写入 `~/.config/ai-roundtable/deepseek.key`（权限 600），或设置环境变量
    `$DEEPSEEK_API_KEY`（环境变量优先级更高）。

    **日常使用建议用文件方式。** 密钥在每次调用时读取，所以账号面板能立刻识别，
    无需重启服务；而在你自己终端里执行 `export` 是**无法**影响一个已经在运行的
    进程的——那样"重新检查状态"会一直是红的。环境变量适合无人值守／定时任务场景。

    这是唯一按量计费的供应商，但 `deepseek-v4-flash` 的价格是每百万 token 输入
    $0.14 / 输出 $0.28，跑完一整场辩论也就几分钱的零头。特别值得加入的原因在于
    它**不是**西方前沿实验室的模型——当它和 Claude、Gemini 都同意时，这个共识的
    含金量更高。

三个命令行工具各自通过自己的登录流程认证，在终端里跑一次即可。DeepSeek 只需要一个
密钥文件。除此之外不需要 `.env` 文件，也不需要任何计费配置。

## 运行

```bash
git clone https://github.com/QZXP27/ai-roundtable.git
cd ai-roundtable
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./.venv/bin/python server.py
# 打开 http://localhost:8500
```

首次打开后，查看侧边栏的**账号**面板。每个供应商都有一个状态点（绿色 = 已连接，
黄色 = 已安装但未登录，红色 = PATH 中找不到命令行工具；DeepSeek 只会显示绿或黄，
因为缺密钥并不等于缺程序）。点任意供应商的**连接**按钮会显示需要在终端执行的确切
命令——复制、执行，然后点**重新检查状态**。

> 各供应商的登录都是交互式终端界面，无法通过浏览器代理，所以要在你自己的终端里跑。

## 使用流程

1. 连接你想用的供应商（一次即可，各命令行工具会缓存自己的登录状态）。
2. 设置话题，添加参与者（名字 + 供应商 + 模型 + 角色设定），选择每轮交换次数。
   可选：上传参考 PDF。
3. **开始辩论** — 参与者轮流发言；每位都能看到到目前为止的完整记录，包括你的插话。
4. 每轮结束：Opus 撰写的总结，然后暂停等你决定。
5. 随时在输入框里以主持人身份插话；然后选择**继续本轮**或**收尾**。
6. 收尾会产出权衡所有立场后的最终结论。**导出到 Obsidian** 会把 Markdown 写入
   `~/Obsidian/Ai Chat Room/`；**导出 PDF** 则下载排版好的记录。

## 平台说明

有两个功能依赖 macOS 的具体环境，在其他平台上会优雅降级：

- **PDF 导出字体** — `pdf_export.py` 从 `/System/Library/Fonts` 加载 Arial 和
  STHeiti。如果这些字体不存在，会退回到内置的仅拉丁字母字体，导出仍然可用，但
  **中文将无法正常显示**。把字体常量指向任意本地 TTF/TTC 文件即可解决。
- **参考资料提取** — PDF 上传依赖 `pdftotext`（poppler 提供）。如果 PATH 中没有，
  上传会返回错误提示，辩论会在没有参考资料的情况下正常进行。

Obsidian 导出目录为 `~/Obsidian/Ai Chat Room/`；如果你的库在别处，修改 `server.py`
里的 `OBSIDIAN_DIR`。

## 项目结构

```
clients.py         Claude/Gemini/OpenAI 的命令行封装、DeepSeek 的 HTTP 客户端、PROVIDERS 注册表
engine.py          辩论状态机、提示词、记录、自动保存、Markdown 导出
pdf_export.py      当前或历史会话的 PDF 排版渲染
server.py          FastAPI 应用 + WebSocket 事件循环、预设、会话、上传/导出接口
static/index.html  前端（原生 JS/CSS、国际化、主题——无需构建）
personas.default.json  内置起步预设（纳入版本管理）
personas.local.json    你自己保存的预设（已 gitignore，保存时自动创建）
prompts.local.json     可选的提示词覆盖（已 gitignore）
requirements.txt   fastapi, uvicorn, websockets, fpdf2, python-multipart, httpx
transcripts/       自动保存的辩论会话（已 gitignore）
```

## 自定义（不必 fork）

两个可选文件，均已 gitignore，因此你的配置既不会被误提交，也不会在
`git pull` 时被覆盖：

- **`personas.local.json`** — 你在界面里保存的角色阵容都会写进这里。
  纳入版本管理的 `personas.default.json` 只是内置起步预设；同名的本地预设
  会覆盖它。
- **`prompts.local.json`** — 可选。用 `turn`、`summary`、`verdict` 三个键
  覆盖内置的提示词模板。每个模板通过 `.format()` 填入与默认版本相同的字段
  （参见 `engine.py` 中的 `_*_prompt` 方法）。模板写错时会自动回退到内置
  版本，不会让辩论中途崩溃。

## 许可证

[AGPL-3.0](LICENSE)。用大白话说：

| | |
|---|---|
| ✅ 可以 | 自由使用、修改、自己部署，也可以商用 |
| ⚠️ 但是 | 任何衍生版本都必须**公开源码**并保留原作者署名 |
| ⚠️ 注意 | 「网络条款」（AGPL 第 13 条）：即使你只是把改过的版本**做成网站给别人用**、并没有分发代码，也必须向使用者提供源码 |

选 AGPL 而不是 MIT，是希望大家能自由地用它、改它、在它基础上做更多东西，
但不希望有人把它闭源拿走当成自己的产品。**欢迎 fork、欢迎 PR、欢迎改成你
需要的样子**，只要保持开源就好。

> 说明：2026-08-02 之前的版本以 MIT 发布。对于已经拿到那份快照的人，该授权
> 无法撤回；AGPL 从当前版本起生效。
