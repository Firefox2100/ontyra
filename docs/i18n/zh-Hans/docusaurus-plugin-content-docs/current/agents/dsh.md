---
title: DSH
---

DSH 插件通过 REST 把 DeepSeek Harness 连接到 Ontyra。它在会话开始时和压缩之后注入简报，在每一步之前召回记忆，记录会话记录（episode），并注册原生的 `ontyra_*` 工具。如果守护进程不可用，插件不会干预这一轮。

## 前提条件

先启动一个 Ontyra 守护进程。插件的默认地址是 `http://127.0.0.1:8787`。

## 安装

通过 DSH 的插件管理器，从一份检出的代码安装该捆绑包：

```text
plugin_manager install_bundle target=/path/to/ontyra/integrations/dsh
```

该捆绑包会添加一个名为 `ontyra` 的 Loader 条目。安装之后，或 `package.json` 变化之后，请重启配置档（profile）。先禁用再启用该捆绑包，可重新加载普通的 Host 端变更。

打开 **Settings -> Ontyra memory** 进行配置。常用设置如下：

| 设置 | 默认值 | 用途 |
|---|---|---|
| `baseUrl` | `http://127.0.0.1:8787` | Ontyra REST 端点 |
| `tokenRef` | `ONTYRA_TOKEN` | 存放 bearer 令牌的 DSH 凭据 |
| `briefing` | `true` | 注入会话简报 |
| `recall` | `true` | 在每一步的第一条提示之前召回 |
| `toolsProfile` | `lite` | 使用 `full` 可增加本体工具 |
| `episodes` | `true` | 记录提示与回答，用作引文证据 |
| `capture` | `true` | 提示模型记录值得长期保留的结论 |
| `subagents` | `false` | 也在子智能体会话中注入和记录 |

令牌存放在 DSH 的凭据库里，而不是 Cordis patch 中。对于开放的回环守护进程，没有令牌也是合法的。

## 模型会收到什么

注入的记忆是一条来源为 `ontyra` 的 user 角色消息，包在 `<ontyra-context trust="reference-only">` 之中。这层包装表明：存储的记忆是参考数据，而不是新的用户指令。注入只做追加，因此 DSH 可以保留它的提示缓存。

lite 工具档包括召回、检查、简报、结构化提问、读取、提案、反馈、待处理提案和修改。full 档增加概念与本体变更工具。每一次写入仍然要经过 Ontyra 的校验和审核策略。

## 检查安装

在仓库根目录运行：

```bash
node integrations/dsh/scripts/doctor.mjs
```

doctor 会检查守护进程的健康状况、一次空召回、DSH 已加载的模块图，以及浏览器端的设置模块。任何一个服务运行在不同地址时，使用 `--gui` 和 `--daemon`。

运行包级别的测试：

```bash
cd integrations/dsh
node --test test/*.test.js
```

插件自己的 [README](https://github.com/Firefox2100/ontyra/tree/main/integrations/dsh) 有完整的设置、故障排查说明和包结构。
