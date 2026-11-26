---
slug: /
sidebar_position: 1
title: Ontyra agent memory
hide_title: true
hide_table_of_contents: true
pagination_next: null
pagination_prev: null
---

<div className="ontyra-home">
  <header className="home-hero">
    <div className="home-hero-copy">
      <h1>Useful memory should survive the session.</h1>
      <p>Ontyra gives your AI agent a durable memory that stays structured, grounded in evidence, and under your control. It recalls what applies to the work at hand and leaves the rest alone.</p>
      <div className="home-actions">
        <a className="home-button home-button-primary" href="./quick-start">Start with Ontyra</a>
        <a className="home-button home-button-secondary" href="./concepts/overview">Understand the memory model</a>
      </div>
    </div>
    <p className="home-hero-note">For coding, research, writing, administration, and the everyday work you already share with an agent.</p>
  </header>

  <section className="home-memory-path" aria-labelledby="memory-path-title">
    <div className="home-section-heading">
      <h2 id="memory-path-title">From a useful moment to memory that returns</h2>
      <p>Ontyra keeps the path visible. Every stage has a clear owner and a record you can inspect.</p>
    </div>
    <div className="home-path-track">
      <article className="home-path-stage">
        <span className="home-path-marker" aria-hidden="true"></span><p className="home-path-label">Notice</p>
        <h3>A useful moment</h3><p>Your agent notices a preference, decision, procedure, fact, or task worth keeping.</p>
        <a href="./concepts/overview#memory-kinds">See the memory kinds<span className="home-link-arrow" aria-hidden="true"></span></a>
      </article>
      <article className="home-path-stage">
        <span className="home-path-marker" aria-hidden="true"></span><p className="home-path-label">Propose</p>
        <h3>A structured record</h3><p>It records what happened, where the memory applies, and the evidence behind it.</p>
        <a href="./guides/evidence">How evidence works<span className="home-link-arrow" aria-hidden="true"></span></a>
      </article>
      <article className="home-path-stage home-path-stage-review">
        <span className="home-path-marker" aria-hidden="true"></span><p className="home-path-label">Review</p>
        <h3>You decide what lasts</h3><p>Policy commits trusted changes or holds them for you to approve, amend, or reject.</p>
        <a href="./guides/review">Review and policy<span className="home-link-arrow" aria-hidden="true"></span></a>
      </article>
      <article className="home-path-stage home-path-stage-recall">
        <span className="home-path-marker" aria-hidden="true"></span><p className="home-path-label">Recall</p>
        <h3>Returned in context</h3><p>The right memory comes back when it helps. Irrelevant memory stays quiet.</p>
        <a href="./concepts/overview#recall">See how recall decides<span className="home-link-arrow" aria-hidden="true"></span></a>
      </article>
    </div>
  </section>

  <section className="home-control" aria-labelledby="control-title">
    <div className="home-control-intro">
      <h2 id="control-title">Memory without the black box.</h2>
      <p>Ontyra is not a transcript search box or a pile of embeddings. It keeps meaning, scope, provenance, and change history close to every memory.</p>
    </div>
    <div className="home-control-ledger">
      <div className="home-ledger-row"><span>Typed</span><strong>Facts stay different from rules, decisions, procedures, and tasks.</strong></div>
      <div className="home-ledger-row"><span>Applicable</span><strong>Places, concepts, conditions, and lifecycle determine when memory belongs.</strong></div>
      <div className="home-ledger-row"><span>Inspectible</span><strong>Evidence, revisions, review decisions, and recall reasons remain available.</strong></div>
    </div>
  </section>

  <section className="home-start" aria-labelledby="start-title">
    <div className="home-start-copy">
      <h2 id="start-title">Start with the guided setup.</h2>
      <p>Run Ontyra first, then connect one of the supported agent clients.</p>
      <a className="home-text-link" href="./quick-start">Open the quick start<span className="home-link-arrow" aria-hidden="true"></span></a>
    </div>
    <nav className="home-agent-links" aria-label="Supported agent clients">
      <a href="./agents/claude-code"><span>Claude Code</span><small>Plugin, hooks, and MCP tools</small></a>
      <a href="./agents/codex"><span>Codex</span><small>Plugin, recall hooks, and MCP</small></a>
      <a href="./agents/dsh"><span>DSH</span><small>Native tools and session memory</small></a>
      <a href="./agents/other"><span>REST and MCP clients</span><small>Connect a compatible agent</small></a>
    </nav>
  </section>

  <aside className="home-experimental">
    <div><strong>Ontyra is an experiment.</strong><p>The schema may change. Open deployments belong on loopback; public use needs multi-user authentication and a correctly configured edge.</p></div>
    <a href="./guides/authentication">Read the deployment boundary<span className="home-link-arrow" aria-hidden="true"></span></a>
  </aside>
</div>
