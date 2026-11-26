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
      <h1>有用的记忆，应当比一次会话活得更久。</h1>
      <p>Ontyra 为你的 AI 智能体提供持久的记忆：结构清晰、有证据支撑、由你掌控。它只召回与当前工作相关的内容，其余保持沉默。</p>
      <div className="home-actions">
        <a className="home-button home-button-primary" href="./quick-start">开始使用 Ontyra</a>
        <a className="home-button home-button-secondary" href="./concepts/overview">了解记忆模型</a>
      </div>
    </div>
    <p className="home-hero-note">适用于编程、研究、写作、行政事务，以及你已经与智能体共同完成的日常工作。</p>
  </header>

  <section className="home-memory-path" aria-labelledby="memory-path-title">
    <div className="home-section-heading">
      <h2 id="memory-path-title">从一个有用的瞬间，到会再次出现的记忆</h2>
      <p>Ontyra 让整条路径清晰可见。每个阶段都有明确的负责方，以及可供检查的记录。</p>
    </div>
    <div className="home-path-track">
      <article className="home-path-stage">
        <span className="home-path-marker" aria-hidden="true"></span><p className="home-path-label">发现</p>
        <h3>一个有用的瞬间</h3><p>智能体注意到值得保留的偏好、决策、流程、事实或任务。</p>
        <a href="./concepts/overview#memory-kinds">查看记忆的种类<span className="home-link-arrow" aria-hidden="true"></span></a>
      </article>
      <article className="home-path-stage">
        <span className="home-path-marker" aria-hidden="true"></span><p className="home-path-label">提议</p>
        <h3>一条结构化的记录</h3><p>它记下发生了什么、这条记忆适用于哪里，以及背后的证据。</p>
        <a href="./guides/evidence">证据如何运作<span className="home-link-arrow" aria-hidden="true"></span></a>
      </article>
      <article className="home-path-stage home-path-stage-review">
        <span className="home-path-marker" aria-hidden="true"></span><p className="home-path-label">审核</p>
        <h3>由你决定留下什么</h3><p>策略会直接提交可信的变更，或将其搁置，等你批准、修改或拒绝。</p>
        <a href="./guides/review">审核与策略<span className="home-link-arrow" aria-hidden="true"></span></a>
      </article>
      <article className="home-path-stage home-path-stage-recall">
        <span className="home-path-marker" aria-hidden="true"></span><p className="home-path-label">召回</p>
        <h3>在合适的上下文中返回</h3><p>合适的记忆在有帮助时回来，无关的记忆保持安静。</p>
        <a href="./concepts/overview#recall">了解召回如何判断<span className="home-link-arrow" aria-hidden="true"></span></a>
      </article>
    </div>
  </section>

  <section className="home-control" aria-labelledby="control-title">
    <div className="home-control-intro">
      <h2 id="control-title">记忆，而不是黑箱。</h2>
      <p>Ontyra 不是对话记录的搜索框，也不是一堆向量。每条记忆都紧贴着它的含义、作用范围、出处和变更历史。</p>
    </div>
    <div className="home-control-ledger">
      <div className="home-ledger-row"><span>有类型</span><strong>事实不同于规则、决策、流程和任务。</strong></div>
      <div className="home-ledger-row"><span>有适用范围</span><strong>地点、概念、条件和生命周期决定一条记忆何时适用。</strong></div>
      <div className="home-ledger-row"><span>可检查</span><strong>证据、修订、审核决定和召回理由都可随时查看。</strong></div>
    </div>
  </section>

  <section className="home-start" aria-labelledby="start-title">
    <div className="home-start-copy">
      <h2 id="start-title">从引导式安装开始。</h2>
      <p>先运行 Ontyra，再连接一个受支持的智能体客户端。</p>
      <a className="home-text-link" href="./quick-start">打开快速开始<span className="home-link-arrow" aria-hidden="true"></span></a>
    </div>
    <nav className="home-agent-links" aria-label="受支持的智能体客户端">
      <a href="./agents/claude-code"><span>Claude Code</span><small>插件、钩子与 MCP 工具</small></a>
      <a href="./agents/codex"><span>Codex</span><small>插件、召回钩子与 MCP</small></a>
      <a href="./agents/dsh"><span>DSH</span><small>原生工具与会话记忆</small></a>
      <a href="./agents/other"><span>REST 与 MCP 客户端</span><small>接入兼容的智能体</small></a>
    </nav>
  </section>

  <aside className="home-experimental">
    <div><strong>Ontyra 是一个实验项目。</strong><p>数据库模式可能变化。未加保护的部署应仅绑定回环地址；对外使用需要多用户认证和正确配置的边缘网关。</p></div>
    <a href="./guides/authentication">阅读部署边界<span className="home-link-arrow" aria-hidden="true"></span></a>
  </aside>
</div>
