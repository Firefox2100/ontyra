---
title: DSH
---

DSH 插件通过 REST 将 DeepSeek Harness 接入 Ontyra。它会在会话开始和上下文压缩后加入简报，在每一步开始前调取相关记忆，保存会话记录（episode），并注册原生的 `ontyra_*` 工具。如果 Ontyra 服务不可用，插件会跳过当前步骤，不影响对话继续。

## 前提条件

先启动 Ontyra 服务。插件默认连接 `http://127.0.0.1:8787`。

## 安装

通过 DSH 的插件管理器，从一份检出的代码安装该捆绑包：

```text
plugin_manager install_bundle target=/path/to/ontyra/integrations/dsh
```

安装包会添加一个名为 `ontyra` 的 Loader 条目。安装完成或 `package.json` 发生变化后，请重启当前配置档（profile）。如果只是 Host 端代码发生变化，可以先禁用再启用安装包来重新加载。

打开 **Settings -> Ontyra memory** 进行配置。常用设置如下：

| 设置 | 默认值 | 用途 |
|---|---|---|
| `baseUrl` | `http://127.0.0.1:8787` | Ontyra REST 端点 |
| `tokenRef` | `ONTYRA_TOKEN` | 存放 bearer 令牌的 DSH 凭据 |
| `briefing` | `true` | 注入会话简报 |
| `recall` | `true` | 在每一步的第一条提示之前调取相关记忆 |
| `toolsProfile` | `lite` | 使用 `full` 可增加本体工具 |
| `episodes` | `true` | 记录提示与回答，用作引文证据 |
| `capture` | `true` | 提示模型记录值得长期保留的结论 |
| `subagents` | `false` | 也在子智能体会话中注入和记录 |

令牌保存在 DSH 的凭据库中，不会写入 Cordis patch。连接仅监听回环地址的开放服务时，可以不设置令牌。

## 模型会收到什么

注入的记忆会作为一条来源为 `ontyra` 的 user 角色消息，并放在 `<ontyra-context trust="reference-only">` 中。这层标记告诉模型，存储的记忆只供参考，不是新的用户指令。插件只追加上下文，因此 DSH 仍可复用提示缓存。

`lite` 工具档包括记忆调取、检查、简报、结构化提问、读取、提案、反馈、待处理提案和修改。`full` 档还提供概念与本体变更工具。所有写入仍需经过 Ontyra 的校验和审核策略。

## 检查安装

在仓库根目录运行：

```bash
node integrations/dsh/scripts/doctor.mjs
```

doctor 会检查 Ontyra 的健康状态、一次空查询、DSH 已加载的模块图，以及浏览器端设置模块。如果 GUI 或 Ontyra 使用了其他地址，请通过 `--gui` 和 `--daemon` 指定。

运行包级别的测试：

```bash
cd integrations/dsh
node --test test/*.test.js
```

插件自己的 [README](https://github.com/Firefox2100/ontyra/tree/main/integrations/dsh) 有完整的设置、故障排查说明和包结构。
