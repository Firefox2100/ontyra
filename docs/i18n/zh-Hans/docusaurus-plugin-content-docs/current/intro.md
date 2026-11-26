---
slug: /
sidebar_position: 1
title: Ontyra 智能体记忆
hide_title: true
hide_table_of_contents: true
pagination_next: null
pagination_prev: null
---

<div className="ontyra-home">
  <header className="home-hero">
    <div className="home-hero-copy">
      <h1>重要的信息，不该随着会话结束而消失。</h1>
      <p>Ontyra 为 AI 智能体保存结构清晰、有据可查且由你掌控的长期记忆。它只调取当前工作需要的内容，不让无关信息打扰你。</p>
      <div className="home-actions">
        <a className="home-button home-button-primary" href="./quick-start">开始使用 Ontyra</a>
        <a className="home-button home-button-secondary" href="./concepts/overview">了解记忆模型</a>
      </div>
    </div>
    <p className="home-hero-note">适合编程、研究、写作、行政事务，以及其他由智能体协助完成的日常工作。</p>
  </header>

  <section className="home-memory-path" aria-labelledby="memory-path-title">
    <div className="home-section-heading">
      <h2 id="memory-path-title">从当下的重要信息，到日后恰当的提醒</h2>
      <p>Ontyra 会记录信息如何保存、由谁审核，以及何时再次使用。整个过程都有记录可查。</p>
    </div>
    <div className="home-path-track">
      <article className="home-path-stage">
        <span className="home-path-marker" aria-hidden="true"></span><p className="home-path-label">发现</p>
        <h3>发现值得保留的信息</h3><p>智能体发现一项值得长期保存的偏好、决策、流程、事实或任务。</p>
        <a href="./concepts/overview#memory-kinds">查看记忆的种类<span className="home-link-arrow" aria-hidden="true"></span></a>
      </article>
      <article className="home-path-stage">
        <span className="home-path-marker" aria-hidden="true"></span><p className="home-path-label">提议</p>
        <h3>整理成结构化记录</h3><p>它会记录具体内容、适用范围和相关证据。</p>
        <a href="./guides/evidence">证据如何运作<span className="home-link-arrow" aria-hidden="true"></span></a>
      </article>
      <article className="home-path-stage home-path-stage-review">
        <span className="home-path-marker" aria-hidden="true"></span><p className="home-path-label">审核</p>
        <h3>由你决定保留什么</h3><p>策略可以直接提交可信的变更，也可以留待你批准、修改或拒绝。</p>
        <a href="./guides/review">审核与策略<span className="home-link-arrow" aria-hidden="true"></span></a>
      </article>
      <article className="home-path-stage home-path-stage-recall">
        <span className="home-path-marker" aria-hidden="true"></span><p className="home-path-label">调取</p>
        <h3>在需要时重新出现</h3><p>相关记忆会在能派上用场时出现，无关内容则不会打扰当前工作。</p>
        <a href="./concepts/overview#recall">了解系统如何选择记忆<span className="home-link-arrow" aria-hidden="true"></span></a>
      </article>
    </div>
  </section>

  <section className="home-control" aria-labelledby="control-title">
    <div className="home-control-intro">
      <h2 id="control-title">每条记忆都有据可查。</h2>
      <p>Ontyra 不只是搜索对话记录或向量。每条记忆都保留含义、适用范围、来源和变更历史。</p>
    </div>
    <div className="home-control-ledger">
      <div className="home-ledger-row"><span>有类型</span><strong>事实不同于规则、决策、流程和任务。</strong></div>
      <div className="home-ledger-row"><span>有适用范围</span><strong>地点、概念、条件和生命周期决定一条记忆何时适用。</strong></div>
      <div className="home-ledger-row"><span>可追溯</span><strong>证据、修订记录、审核结果和调取理由都可随时查看。</strong></div>
    </div>
  </section>

  <section className="home-start" aria-labelledby="start-title">
    <div className="home-start-copy">
      <h2 id="start-title">按照指南完成安装。</h2>
      <p>先运行 Ontyra，再连接一个受支持的智能体客户端。</p>
      <a className="home-text-link" href="./quick-start">查看快速开始指南<span className="home-link-arrow" aria-hidden="true"></span></a>
    </div>
    <nav className="home-agent-links" aria-label="受支持的智能体客户端">
      <a href="./agents/claude-code"><span>Claude Code</span><small>插件、钩子与 MCP 工具</small></a>
      <a href="./agents/codex"><span>Codex</span><small>插件、记忆调取钩子与 MCP</small></a>
      <a href="./agents/dsh"><span>DSH</span><small>原生工具与会话记忆</small></a>
      <a href="./agents/other"><span>REST 与 MCP 客户端</span><small>接入兼容的智能体</small></a>
    </nav>
  </section>

  <aside className="home-experimental">
    <div><strong>Ontyra 仍处于实验阶段。</strong><p>数据库结构可能变化。没有安全防护时，只应绑定回环地址；如需对外提供服务，请启用多用户认证并正确配置边缘网关。</p></div>
    <a href="./guides/authentication">了解安全部署要求<span className="home-link-arrow" aria-hidden="true"></span></a>
  </aside>
</div>
