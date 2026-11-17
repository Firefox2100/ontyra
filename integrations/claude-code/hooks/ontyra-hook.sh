#!/usr/bin/env bash
# Agent harness hook (Claude Code, Codex): ask the Ontyra daemon what applies and hand it to the model as context.
#   SessionStart      -> POST /v1/briefing (redelivers after a resume or compaction)
#   UserPromptSubmit  -> POST /v1/recall   (the prompt is the query)
#   UserPromptSubmit and Stop also log the prompt / the assistant's last message as episodes
#   (the lossless interaction log that proposals cite as evidence).
#   Stop, when ONTYRA_CAPTURE=1: every ONTYRA_CAPTURE_EVERY prompts (default 10) asks the agent
#   once to propose memories for anything durable it learned (`remember` tool).
# Fails open: if the daemon is down or slow the session continues without memory.
# Needs curl and jq. Env: ONTYRA_URL (default http://127.0.0.1:8787), ONTYRA_BUDGET_TOKENS,
# ONTYRA_CAPTURE, ONTYRA_CAPTURE_EVERY, ONTYRA_LOG_EPISODES (default 1), ONTYRA_HARNESS (default
# claude-code) and ONTYRA_ACTOR (default: the harness name) say who is talking.
set -u
input=$(cat)
url="${ONTYRA_URL:-http://127.0.0.1:8787}"
event=$(jq -r '.hook_event_name // empty' <<<"$input") || exit 0
cwd=$(jq -r '.cwd // empty' <<<"$input")
session=$(jq -r '.session_id // empty' <<<"$input")
source_kind=$(jq -r '.source // "startup"' <<<"$input")
host=$(hostname 2>/dev/null || echo localhost)
harness="${ONTYRA_HARNESS:-claude-code}"
actor="${ONTYRA_ACTOR:-${harness%%-*}}"
state="${TMPDIR:-/tmp}/ontyra-$harness-${session:-none}"

post() { # post PATH JSON: fire at the daemon, print the reply
  curl -sS --max-time 8 -X POST "$url/v1/$1" -H 'content-type: application/json' \
    -H "X-Ontyra-Actor: $actor" -H "X-Ontyra-Harness: $harness" -d "$2"
}

episode() { # episode ROLE TEXT
  [ "${ONTYRA_LOG_EPISODES:-1}" = 1 ] || return 0
  [ -n "$2" ] || return 0
  post episodes "$(jq -n --arg s "$session" --arg r "$1" --arg c "$2" --arg h "$harness" \
    '{session: $s, role: $r, content: $c, meta: {harness: $h}}')" >/dev/null 2>&1 || true
}

# Where we are: the working directory and the git root (anchors; the daemon knows the containment)
root=$(git -C "${cwd:-.}" rev-parse --show-toplevel 2>/dev/null || true)
uris=$(jq -n --arg cwd "$cwd" --arg root "$root" --arg host "$host" \
  '[$cwd, $root] | map(select(. != "")) | unique | map("file://\($host)\(.)")')

# What is here: OS, shell, and the languages/tools the project files show. Labels the daemon
# does not know are ignored quietly (they come back in `unresolved`), so over-reporting is safe.
signals='[]'
add_signal() { signals=$(jq -c --arg c "$1" --arg v "${2:-}" \
  '. + [if $v == "" then {concept: $c} else {concept: $c, value: $v} end]' <<<"$signals"); }
case "$(uname -s 2>/dev/null)" in
  Linux) add_signal linux ;; Darwin) add_signal macos ;; esac
[ -n "${SHELL:-}" ] && add_signal "$(basename "$SHELL")"
dir="${root:-$cwd}"
if [ -n "$dir" ] && [ -d "$dir" ]; then
  [ -e "$dir/pyproject.toml" ] || [ -e "$dir/requirements.txt" ] && add_signal python
  [ -e "$dir/package.json" ] && add_signal nodejs
  [ -e "$dir/Cargo.toml" ] && add_signal rust
  [ -e "$dir/go.mod" ] && add_signal go
  [ -e "$dir/Dockerfile" ] || [ -e "$dir/compose.yaml" ] && add_signal docker
  [ -d "$dir/.git" ] && add_signal git
fi

case "$event" in
  Stop)
    episode assistant "$(jq -r '.last_assistant_message // empty' <<<"$input")"
    [ "${ONTYRA_CAPTURE:-0}" = 1 ] || exit 0
    # `stop_hook_active` is true when this very hook already kept the agent going: never loop
    [ "$(jq -r '.stop_hook_active // false' <<<"$input")" = "true" ] && exit 0
    n=$(( $(cat "$state.prompts" 2>/dev/null || echo 0) ))
    last=$(( $(cat "$state.captured" 2>/dev/null || echo 0) ))
    [ $(( n - last )) -ge "${ONTYRA_CAPTURE_EVERY:-10}" ] || exit 0
    echo "$n" >"$state.captured"
    jq -n '{decision: "block", reason: "Memory check (Ontyra): if in this conversation you learned something durable and non-obvious about the user, the project or a decision that was made (a preference, a rule, a fact, a decision with its reason), record it now with the `remember` tool (a proposal; quote the evidence). Do not record anything transient or already stored. If there is nothing, just say \"nothing to record\" in one line and stop."}'
    exit 0
    ;;
  SessionStart)
    path=briefing
    redeliver=false
    [ "$source_kind" = "startup" ] || redeliver=true
    query=null
    ;;
  UserPromptSubmit)
    path=recall
    redeliver=false
    query=$(jq '.prompt // null' <<<"$input")
    echo $(( $(cat "$state.prompts" 2>/dev/null || echo 0) + 1 )) >"$state.prompts"
    episode user "$(jq -r '.prompt // empty' <<<"$input")"
    ;;
  *) exit 0 ;;
esac

body=$(jq -n \
  --argjson uris "$uris" --argjson signals "$signals" --arg session "$session" \
  --argjson query "$query" --argjson redeliver "$redeliver" \
  --argjson budget "${ONTYRA_BUDGET_TOKENS:-null}" \
  '{context: {uris: $uris, signals: $signals, session: $session},
    query: $query, redeliver: $redeliver, budget_tokens: $budget}') || exit 0

reply=$(post "$path" "$body") || exit 0

silent=$(jq -r 'if .silent == false then "false" else "true" end' <<<"$reply" 2>/dev/null) || exit 0
rendered=$(jq -r '.rendered // ""' <<<"$reply" 2>/dev/null) || exit 0
[ "$silent" = "true" ] || [ -z "$rendered" ] && exit 0

jq -n --arg event "$event" --arg text "$rendered" \
  '{hookSpecificOutput: {hookEventName: $event, additionalContext: $text}}'
