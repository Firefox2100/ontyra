# Model-driven evaluation

The benchmark (`../benchmark_test`) measures what the daemon returns. This suite measures the
other half: **what a language model does with the interfaces we give it**. It calls a chat model
through LangChain (`langchain-openai`, any OpenAI-compatible server) and scores what comes back.
Nothing here runs without `BENCH_LIVE=1`; the case data and every scorer are tested offline with a
scripted model (`uv run pytest tests/evaluation_test`: 5 live tests skip, the rest run).

| area | question | file |
|---|---|---|
| tool selection | does the model pick the right MCP tool, with valid arguments, from `lite`, `full`, and `full` without descriptions? | `test_tools.py` |
| writing | from a conversation, does it write the right memory (kind, scope, one per statement), and nothing when nothing durable was said? Runs against the real server, so rejections come back like in production | `test_write.py` |
| presentation | does it act on what recall delivers, rendered `compact`, `rich`, or not at all? Answers are checked with patterns (the rule obeyed, the stale value absent) | `test_presentation.py` |
| maintenance decisions | shown a task card (built by the daemon's own card functions), does it merge only true duplicates, keep what cannot go stale, defer when it cannot know? | `test_decisions.py` |
| agent decides to recall | the hook injects recall for the literal message; the model sees it and may call `recall` itself, as the skill asks. Scored on both packs merged, plus how often it recalls on messages that need history and on those that do not | `test_agent_recall.py` |
| end-to-end scenarios | the same user task in four arms: no memory, injected context (what the hooks do), the lite tools with the shipped skill, and both. Graded by patterns on the answer and by which tools were called; scenarios cover replaced values, retracted errors, rules, a standing preference, tasks that need no memory, and writing. The result keeps whole transcripts so graders can be fixed and runs re-scored offline (`regrade`) | `test_scenarios.py` |
| agent-written queries | when the model writes the `recall` query from the conversation, how does recall compare with the heuristics of the dialogue suite? | `test_agent_recall.py` |

## Run it

```
BENCH_LIVE=1 uv run pytest tests/evaluation_test/test_tools.py -s          # one area
BENCH_LIVE=1 BENCH_ONLY=t-remember uv run pytest tests/evaluation_test -s  # cases by id substring
```

Environment (all `BENCH_*`, see `framework/config.py`): `BENCH_LLM_URL` (default
`http://127.0.0.1:8081/v1`, the owner's LocalAI), `BENCH_LLM_MODEL` (default
`qwen3.6-35b-a3b-uncensored-1m`), `BENCH_LLM_API_KEY`, `BENCH_LLM_TEMPERATURE` (0),
`BENCH_LLM_MAX_TOKENS`, `BENCH_LLM_THINKING` (off: Qwen3 style reasoning is slow and noisy; sent as `reasoning_effort: "none"`, which LocalAI honours, plus the `enable_thinking` kwarg),
`BENCH_ARMS` (scenario arms, default all four), `BENCH_REPEAT` (samples per case; useful only above temperature 0), `BENCH_LABEL`, `BENCH_ONLY`,
`BENCH_RESULTS_DIR`. The world-based areas use the benchmark's embedder (`BENCH_EMBED_*`, hash by
default) over the same `commons` world.

Results go to `results/<area>-<label>-<model>.json` (git-ignored, never overwritten): per case the
variant, pass/fail, the problems, the calls and the model's answer, plus a summary per variant
and tag. Read the failures, not the totals: a failing case names the tool, the argument or the
pattern that went wrong.

## What each area scores

- **Tools**: the first call must be the expected tool (or one of `accept`); no `forbid`den tool is
  ever called (an agent must not approve its own proposal); the arguments validate against the
  tool's JSON schema and contain the strings the case names. Cases whose expected tool is not in
  the tool set are skipped for it (lite has no review tools). Cases with no expected tool are the
  negative half: pleasantries, mechanical edits, transient remarks must not trigger `remember`.
- **Writing**: one `remember` that commits, the right `kind`, the number of memories the
  statements call for, the facts in the proposal, no `unscoped_directive` warning, not held. A
  proposal rejected by the daemon and then corrected counts as a pass and is reported as a retry.
  Whether the proposal cites a quote is reported, not scored; a quote the daemon cannot find
  rejects the proposal, so a wrong quote shows up as a retry.
- **Presentation**: each scenario's `must`/`avoid` patterns over the answer. The `none` variant
  is the control: if it passes as often as `compact`, the scenario does not need the memory.
  Context tokens per variant show what `rich` costs over `compact`.
- **Decisions**: the decision must be one of the card's choices and one the case accepts; `distinct`
  and `keep` need a note. The pairs' cosine is fixed at 0.86 so the model cannot lean on it.
- **Agent-written queries**: the same dialogues and scoring as `benchmark_test/test_dialogue.py`.

## Adding cases

Cases are JSON under `data/cases/<area>/`. `test_data.py` checks that tool names exist in the
generated MCP contract, that decision cards build, and that every presentation scenario's recall
(hash embedder) really returns the memory it relies on, so a scenario cannot silently measure
recall instead of presentation. A presentation case has a `query` (what recall is asked, chosen
so the memory is found) and a `task` (what the user asks the model).

## Open points this suite surfaces

- **Evidence.** Settled: `remember` evidence is a literal `quote`, matched by the daemon against
  the episodes the harness hook logged (no id for the agent to know). The write cases log each
  conversation line as an episode, as the hook does. Measured: how often the model cites at all.
- **Tool profiles.** `lite` against `full` against `full-bare` says whether the 17 extra tools
  crowd out the right choice and how much the descriptions are worth.

## Comparing models (the tier matrix)

Point `BENCH_LLM_URL` / `BENCH_LLM_MODEL` at each model in turn and run the same areas with a label
(`BENCH_LABEL=qwen36`); the result files carry the model name, so `results/scenarios-*.json` can be
compared run against run. Any OpenAI-compatible server works: LocalAI for the local models, or a
LiteLLM proxy in front of a hosted one (the API key stays in the proxy's configuration; only the
model name is needed here). A hosted model bills per call: the scenario suite makes up to about
100 runs of several model calls each, so run one arm first (`BENCH_ARMS=none,both`).

## Knowledge retrieval (ingested documents)

`test_knowledge.py` asks `consult` the questions in `data/cases/knowledge/*.json` (lookups,
numeric filters, multi-hop, and "not stated" questions about one document, each with the page the
answer is on) and scores whether the returned entries contain what the answer needs (`hit@1/3/8`).
No model is called. It needs a daemon that has the document ingested:
`BENCH_KNOWLEDGE_URL=http://127.0.0.1:8787 BENCH_KNOWLEDGE_TOKEN=... uv run pytest tests/evaluation_test/test_knowledge.py -s`.
Absent-fact questions are for an answering model and are not scored here. First corpus: the
ESP32-S3-WROOM-1 datasheet (plan 28, A7/A8).
