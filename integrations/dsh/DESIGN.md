# Ontyra × DeepSeek Harness（DSH）原生插件整合

状态：设计已定稿，进入实现。本文件只在本地保存，不加入版本控制。

## 1. 目标与约束

把 Ontyra 的长期记忆接进 DeepSeek Harness，行为与 `integrations/claude-code`、
`integrations/codex` 一致：会话开始给 briefing、每条人类消息做 recall、记录 episode、可选的
capture 提醒、可选的完整工具面、随插件发布的技能。

约束（用户已确认）：

- **原生插件**，不通过 MCP 配置行暴露工具；用户不需要在 DSH 里手工配置 MCP。
- **不做任何 ontyra 服务端改动**：现有 REST 已覆盖全部能力。
- 设置页面**必须存在**：基础配置（Ontyra 地址、凭据）与行为配置分开。
- 凭据**不写进 Cordis patch**：只保存引用，值进 `$DSH_HOME/.credentials.yaml`。
- `capture`、`subagents` 两项**默认关闭**，放进 DSH 配置供用户打开；`episodes` **默认开启**（记忆证据 `quote` 要在 episode 账本里匹配，与 Claude/Codex 插件一致）。
- 以后更强的整合（例如由插件托管 daemon、记忆面板）继续以原生方式加进这个插件。

## 2. 为什么是 bundle + 客户端卡

DSH 的 Claude/Codex 桥接包（`@deepseek-ai/dsh-hooks-claude-code`、`dsh-hooks-codex`）**没有随
桌面版发布**（`MODULE_NOT_FOUND`），所以 `hooks.json` 无法复用。DSH 的等价缝隙是插件事件：

| Claude/Codex | DSH 缝隙 | 依据 |
|---|---|---|
| `SessionStart`（briefing，resume/compact 重投） | `agent/pre-step` + 私有 session projection | `dsh-time-context/src/index.ts:158-242` 是官方模板 |
| `UserPromptSubmit`（recall） | 同一个 `agent/pre-step`，过滤 `source.kind === 'user'` 的消息 | `PreStepDecision.messages` 即本步完整批次 |
| `Stop`（episode、capture 的 block） | `agent/turn-stopping` + `agent.steer()` | “runs before an otherwise completed turn closes and can steer to keep it open” |
| MCP lite profile 的工具 | `ctx.tools.register(definition)` | MCP 客户端本身也走这条路（`dsh-mcp-client/src/tools.ts:177`） |
| 插件自带的 SKILL.md | `ctx.skills.register(...)` | `dsh-skill` 注册表 |
| hook 里 `pwd` / `git rev-parse` | `ctx.workingDirectory.get(session)` | 做过 realpath，可靠 |

设置页的落点：`plugins.item` 被官方设置页占位，bundle 自己的配置槽是
`plugins.bundle.config`（key = 包名）与 `plugins.row.config`（key = `<包名>#<row>`）。
第三方注册没有闸门（`dsh-client-modules/src/index.ts:905` 只看 `dsh.client.platform`），
但表单**不会**自动生成，必须自带客户端半。

## 3. 包结构

```
integrations/dsh/
  package.json          # dsh.bundle.patch + dsh.client{platform:'web'} + exports{'.','./client'}
  cordis.patch.yml      # 插入 Host 插件 entry，id = "ontyra"（即设置命名空间）
  lib/index.js          # Host 插件（ESM，手写，无构建）
  lib/client.js         # 设置卡（lazy-CJS 工厂 bundle，手写，无构建）
  lib/tools.js          # 8 个原生工具：描述/参数/渲染手写，路由由契约测试钉住
  lib/{context,ontyra,settings,inject,inject-core,skill}.js
  skills/ontyra-memory/SKILL.md # DSH 版技能（工具名带 ontyra_ 前缀，语言随 DSH 走）
  test/*.test.js        # node --test：契约、注入、卡片、技能
  README.md
  DESIGN.md             # 本文件
```

两条「无构建」的事实：

- Host 半：`plugin_manager` 只要求入口是成品（`lib/index.js` 即成品），安装时不运行任何构建脚本。
- 客户端半：DSH 支持手写 plain JS 工厂 bundle（官方模板
  `dsh-agent-preset/skills/dsh-doc/templates/decoration/client.js`），Host 通过
  `/plugins/??<pkg>/client.js` 提供，dev 时 `/plugins/events` SSE 自动换版本。

## 4. Host 插件

### 4.1 配置（Config，schemastery，全部 `.volatile()`）

命名空间 = Loader entry id = `ontyra`。

| 字段 | 默认 | 含义 |
|---|---|---|
| `baseUrl` | `http://127.0.0.1:8787` | daemon 地址 |
| `tokenRef` | `ONTYRA_TOKEN` | `.role('credential-ref')`，只存引用 |
| `harness` | `dsh` | 上报的 harness 标识 |
| `briefing` | `true` | 会话开始/压缩后注入 briefing |
| `recall` | `true` | 每条人类消息做 recall |
| `budgetTokens` | `1200` | 传给 recall/briefing |
| `timeoutMs` | `1500` | 单次 HTTP 超时，失败静默 |
| `tools` | `true` | 注册原生工具 |
| `toolsProfile` | `lite` | `lite` 6 个；`full` 再加 2 个 |
| `episodes` | `true` | 记录 episode |
| `capture` | `false` | capture 提醒 |
| `captureEvery` | `10` | 每 N 轮提醒一次 |
| `subagents` | `false` | 子 agent 是否也注入 |

### 4.2 原生工具

`ontyra_recall`、`ontyra_check`、`ontyra_briefing`、`ontyra_ask`、`ontyra_get`、`ontyra_remember`、
`ontyra_concepts`、`ontyra_propose_ontology_change`（后两个仅 `full`）。

- 名称、描述、参数与路由写死在 `lib/tools.js`，由 `test/contract.test.js` 对着 MCP 契约逐条断言
  （见 §6）；不生成代码，也不引入构建步骤。
- 执行：`client.call({ method, path, body }, signal)` 打 REST；`context` 由 `lib/context.js` 从会话的
  cwd/工程文件观测得到，模型显式传入时覆盖观测值（裸字符串 signal 会被包成 `{concept}`）。
- `output.render`：recall/briefing 返回 daemon 的 `rendered` 文本，其余 JSON 文本。
- 超时：`ToolDefinition.timeoutMs`（注册时固定）+ `exec.signal`（每次调用实际生效）。

### 4.3 注入（`agent/pre-step`，prepend）

```
await next()
decision.kind === 'reject' || signal.aborted  → 原样返回
subagents=false 且当前 agent 是子 agent        → 原样返回
需要 briefing（首次 / compaction/end 之后）    → POST /v1/briefing
否则存在 source.kind === 'user' 的新 prompt    → POST /v1/recall
silent 或 rendered 为空                        → 不注入
注入文本用 <ontyra-context trust="reference-only"> 包装
```

注入永远是持久 user-role 消息 + 自定义 `MessageSource`
（`{ kind: 'ontyra', form: 'snapshot', sections: [{ name, text }] }`），append-only、KV 友好，
**不新造事件类型**；「本 session 已投递」记在 `ctx.sessionProjections.register({ key: 'ontyra', ... })`
的私有状态里，`compaction/end` 时置位重投。

去重记账必须按会话分开：`turn` 是每会话编号，briefing 周期号也带会话 id，所以监听器里的
`recalledTurn: Map<sessionId, turn>` 与 `attempted: Map<sessionId, Set<cycle>>` 都以
`String(session.id)` 为键，并在 `agent/disposed` 时清掉。用一个插件级「上一个 turn」会让
并发会话里第二个会话的 turn 1 被静默跳过——这是本轮自查抓到并修掉的 bug，回归用例在
`test/inject.test.js`（`each session recalls its own turn, even when the numbers collide`）。
子 agent 门控在**两个**入口都要有：`agent/pre-step` 与 `agent/turn-stopping`，否则子 agent
的转录仍会写进 episode 账本。

`context` 组装：

- `uris`：`file://` + 工作目录 + git 根（存在 `.git` 时）。
- `signals`：`process.platform`；工程文件探测
  `pyproject.toml`/`requirements.txt`→python、`package.json`→nodejs、`Cargo.toml`→rust、
  `go.mod`→go、`compose.yaml`→docker；存在 `.git`→git。
- `session`：`agent.session.id`（ledger 去重与更正通知依赖它）。

### 4.4 episode 与 capture

- `assistant/message` 是持久事件，episodes 打开时发 `POST /v1/episodes`
  （`{ session, role, content, meta: { harness } }`），受 `episodes` 开关控制。
- `agent/turn-stopping`：满足 `capture`、`captureEvery` 且本回合未提醒过时
  `agent.steer({ content: [{ type: 'text', text: 'Memory check (Ontyra): …' }],
  source: { kind: 'ontyra-capture' } })`，语义等同 Claude 的 `decision: "block"`。

### 4.5 凭据

- 配置里只有 `tokenRef`（`role('credential-ref')`）；Host 用 `ctx.credentials.resolve(tokenRef)` 取用。
- **不要**用 `role('secret')` 存 token：它只做线上脱敏，写入仍然进 Cordis patch
  （`dsh-settings/src/index.ts:391`），正是要避免的。
- 值由设置卡通过 `ctx.remote.credentials.set(ref, value)` 写入 `$DSH_HOME/.credentials.yaml`；
  客户端插件与官方设置页共用 owner peer，`remoteAccess` 对 owner 直接放行
  （`dsh-api-remote-access/src/index.ts:202`）。读只给 `describe()`，永不回传值。
- daemon 未开鉴权时 token 留空，不发 `Authorization`。

### 4.6 失败模式

daemon 不可达、超时、非 2xx、JSON 意外：注入静默跳过（一条 debug 日志），工具返回错误文本
（`isError`），会话本身绝不被阻塞。

### 4.7 技能

`skills/ontyra-memory/SKILL.md` 是 DSH 专用改编版（工具名带 `ontyra_*` 前缀，正文讲何时写、怎么定
锚点、何时 check）。Host 侧在 `apply` 里 `ctx.inject(['skills'], …)` 可选取得 `ctx.skills`，用
`parseSkill()` 拆出 frontmatter 的 `name`/`description` 与正文，`ctx.effect(() => ctx.skills.register({
name, description, content }))`；部署没有技能服务、或文件读不到时静默跳过。`test/skill.test.js`
反向校验：技能里出现的每个 `ontyra_*` 都必须是本插件真的注册了的工具。

## 5. 客户端设置卡

- 注册 `settings.section`（普通设置页的一节，与官方 provider 设置同列），`id` = 包名；仅在 Host
  真的 serve `ontyra` 命名空间时用 `whileServed` 显示。
- **连接**：`baseUrl` 文本框；token 密码框（只写，显示已设置/未设置，保存走
  `ctx.remote.credentials.set`，清空走 `unset`）；`tokenRef` 是引用名，值从不进 Cordis patch。
- **行为**：`briefing`、`recall`、`tools`、`episodes`、`capture`、`subagents` 复选框 +
  `captureEvery`、`budgetTokens`、`timeoutMs` 数字框 + `toolsProfile` 下拉（`lite` / `full`，
  选项文字随界面语言；非法值在 `coerce()` 里被拒，不会写进 patch）。
- 卡片文案随界面语言切换：`ctx.locale.register` 注册 en/zh/ja/es/fr/de/ru/pt 八套字典，
  键集必须与 `en` 完全相同（`test/client.test.js` 断言），缺键回落键名而非留白。
- 表单句柄自取：section 槽的 owner props 只有 `{ close }`。**读**走 `ctx.configForms.describe()`
  的镜像（`getSnapshot().view.namespaces` 里找 `ns === 'ontyra'` 的 `value`），**写**仍走
  `ctx.configForms.get('ontyra')` 的 `set`/`unset`。
  理由：共享 `ConfigForm` 只有在 Host 序列化的 schema 能被 `rehydrate` 且 `validate` 通过后才进入
  `ready`，否则永远停在 `loading`（`config-form.ts:181-220`），旧实现在此状态下渲染空白；镜像携带
  同一份脱敏 JSON 但没有这道关卡，且写入只依赖 `revision`（decode 失败前就已拿到）。
- 订阅镜像的 `subscribe` 并 `setState`（`getSnapshot()` 每次返回新对象，不能直接交给
  `useSyncExternalStore`；写入成功后镜像自行折叠 Host 的答复）。
- 三种非 ready 状态都渲染可见文本：`loading`（附 `error`）、`unavailable`（附 Host 实际 serve 的
  命名空间列表）、只读；卡片外面再包一层错误边界，渲染失败显示一行原因而不是留白。
- 手写 JS + `require('react')`，不依赖 `@deepseek-ai` 的客户端包（其构建预设不随包发布）。
- 插件行本身也本地化：`locale/<language>.json` 的 `meta.title` / `meta.description` 由 Host 的
  `readPluginMeta()` 读取，**必须经 `exports` 暴露 `./locale/*.json`** 才能解析到；缺 `en.json`
  时标题回落包名，所以英文那份不可省。`test/meta.test.js` 钉住八个文件、非空字段与 exports 形态。

## 6. 契约一致性

`lib/tools.js` 是手写的（不生成代码），但工具集合、描述、必填参数、**接受的参数名**与**路由**由
`test/contract.test.js` 与 `integrations/contract/mcp-tools-lite.json` 逐条比对：名称顺序、full
档多出的两个、每条描述逐字相等、每个工具的必填参数、每个工具声明的参数都必须是 daemon 契约里有的
参数（不许自造），以及每个工具 `request()` 落到的 `method + path`（`ROUTES` 表，对应
`src/ontyra/transports/rest/routers/` 的真实前缀）。改路由或改描述时，契约测试失败即提醒同步
`export_contract.py`。技能文件由 `test/skill.test.js` 校验工具名不漂移。

为什么不生成：生成只能给到参数 schema，而每个工具还需要一张「DSH 参数 → REST 请求体」的映射
（`request()`）和一段渲染（`render()`），这两样是手写的领域知识。生成的收益只是描述文本，用一条
逐字断言就能拿到同样的保证，还少一个构建步骤。

这条路由断言是补上来的：最初 `ontyra_ask`/`ontyra_get` 写成 `/v1/explore/ask` 与
`/v1/explore/memories/{id}/detail`，而 daemon 的 explore 路由**没有** `/explore` 前缀，真机
逐工具烟测立刻 404。教训：契约 JSON 只描述参数，不描述路径，路径必须有独立的断言或烟测。

`tests/unit_test/test_architecture.py` 已保证 integrations 不 import ontyra（本插件为纯 JS，天然
满足）。JS 侧纯函数用内置 `node --test`，不新增 npm 依赖。

## 7. 里程碑

| # | 内容 | 状态 |
|---|---|---|
| M1 | 包骨架、8 个原生工具 | 完成：真机 8 个工具逐个调通；`ontyra_remember` 落库 `committed`，`ontyra_recall` 取回真实记忆，`ontyra_propose_ontology_change` 产出 `pending` 变更 |
| M2 | projection + pre-step 注入 | 完成：真机收到 `<ontyra-context>` briefing；锚点不匹配时静默 abstain |
| M3 | episodes 与 capture | 代码完成 + 单测；capture 默认关，开启后 `agent/steer` 续命 |
| M4 | 设置卡 + 凭据 | 完成：真机确认三个分区与 14 个控件、保存与令牌写入均可用；卡片改读 `describe` 镜像后才稳定渲染 |
| M5 | 收口 | 完成：技能注册已在真机挂上（本会话技能目录出现 `ontyra-memory`）；DESIGN/README/AGENTS 同步；JS 侧 35 个用例全绿 |
| M6 | 打磨（用户 m02724） | 完成：`toolsProfile` 改下拉、八语言卡片文案、`locale/` 元数据（八份 `meta.title`/`meta.description`，经 exports 解析实测通过） |

## 8. 安装与验证

```
plugin_manager action=install_bundle target=D:\GitHub\ontyra\integrations\dsh
```

`lib/*.js` 的改动用 `set_bundle enabled=false` → `true` 重载即可；`package.json`（manifest，例如
新增 `dsh.client`）的改动需要重启桌面进程。

客户端半是否真的进了模块图，可以在运行中的 Host 上直接确认，不必靠目视：

```
# SSE 图：data: {"type":"graph","graph":{"entries":[{"id":"dsh-ontyra-memory","url":"plugins/??dsh-ontyra-memory/client.js&rev=…"}]}}
GET http://127.0.0.1:<gui-port>/plugins/events      (Accept: text/event-stream，读一帧后断开)
# 取到 url 再抓产物，应当 200 且正文是本文件的 browser half
GET http://127.0.0.1:<gui-port>/plugins/??dsh-ontyra-memory/client.js&rev=…
```

2026-11-18 实测：图里有 `dsh-ontyra-memory`（94 个条目中的第三方之一），产物 200、
`text/javascript`、41266 字节（磁盘 CRLF → 服务端 LF），正文含 `settings.section`、`toolsProfile`
的下拉与八套字典。

以上探针已固化为只读脚本 `node integrations/dsh/scripts/doctor.mjs`（`--gui` / `--daemon` 可改，
默认 `http://127.0.0.1:49180` 与 `http://127.0.0.1:8787`，失败退出码非 0）：它依次查 daemon
`/health`、一次空 recall、模块图里有没有本包、以及产物能否取到并含 `settings.section`。SSE 的第一帧
是注释帧（`:connected`），解析时必须跳过没有 `data:` 的帧——脚本第一版就在这里失败过。

## 9. 待确认

- 设置卡已真机确认（`settings.section` 三组、14 个控件、保存与令牌写入可用）。卡片的三种非
  ready 状态（`loading` / `unavailable` / 只读）都渲染可见文本，渲染异常由错误边界显示为
  `Ontyra settings failed to render: …`，所以「空白」不应再出现。
- 元数据（用户 m02724）：八份 `locale/<lang>.json` 已写入并用 `createRequire` 从 profile 侧按
  `<pkg>/locale/<lang>.json` 逐份解析通过（这正是 Host `readPluginMeta()` 的取法）；图标留给用户，
  `package.json` 尚未声明 `icon`——一旦写上，`test/meta.test.js` 会检查文件存在且 ≤256 KiB。
- 子 agent 判定已实现为 `isSubagent(header)`（`origin === 'subagent'` 或 `delegationDepth > 0`），
  命中且 `subagents` 未开时**两个入口**（`agent/pre-step` 与 `agent/turn-stopping`）都直接返回：
  不注入、不记 episode、不提醒 capture。已经过真机验证（一次只读探测子 agent 反馈
  `ontyra-context: no`、`capture: no`）。
- recall 的触发条件不是「第几步」，而是「这一步的批次里出现了人类 prompt」（`source.kind ===
  'user'`），并按 `turn` 去重，因此排队 prompt 与 step 1 之外的人类输入都能触发一次；仍需在真机
  观察一轮新 prompt 的注入。
- 重载时 `agent/pre-step` 的注入是否会串到已存在的会话（观察中；`attempted` 键含 compaction
  周期，重启后会重问一次，由 daemon 的 session ledger 兜底）。
- `ontyra_propose_ontology_change` 已真机调用：`define` 产出 `ontology_change:01a1eeadd901f5201e3b01`、
  `status: pending`。拒绝要 `{reason}` 且**只允许人类**（`only a human can reject an ontology change`），
  所以冒烟留下的 pending 变更需要用户自己在设置页/控制台处理，而不是插件代劳。
