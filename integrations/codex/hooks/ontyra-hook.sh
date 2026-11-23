#!/usr/bin/env bash
# Agent harness hook (Claude Code, Codex): ask the Ontyra daemon what applies and hand it to the model as context.
#   SessionStart      -> POST /v1/briefing (redelivers after a resume or compaction)
#   UserPromptSubmit  -> POST /v1/recall   (the prompt is the query)
#   UserPromptSubmit and Stop also log the prompt / the assistant's last message as episodes
#   (the lossless interaction log that proposals cite as evidence).
#   UserPromptSubmit also adds a one-line reminder to propose what is durable (`remember` tool) when
#   the prompt reads like a rule, preference, correction or decision, and every ONTYRA_CAPTURE_EVERY
#   prompts (default 5), also for a `feedback` verdict on a recalled memory (the recall trace id
#   is added to the injected text). It rides on the context already being injected: no extra turn
#   or model call, and the agent just does nothing when there is nothing to report.
# Fails open: if the daemon is down or slow the session continues without memory.
# Needs curl and jq. Env: ONTYRA_URL (default http://127.0.0.1:8787), ONTYRA_BUDGET_TOKENS,
# ONTYRA_TOKEN, ONTYRA_PROFILE (compact|rich, default compact: no scores or anchors in the text), ONTYRA_CAPTURE (default 1; 0 turns the reminder off), ONTYRA_CAPTURE_EVERY, ONTYRA_LOG_EPISODES (default 1), ONTYRA_HARNESS (default
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

# ONTYRA_TOKEN: the credential when the daemon requires one (a single-user token, or the token
# of an account's credential). Unset: no Authorization header is sent.
auth=()
[ -n "${ONTYRA_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $ONTYRA_TOKEN")

post() { # post PATH JSON: fire at the daemon, print the reply
  curl -sS --max-time 8 -X POST "$url/v1/$1" -H 'content-type: application/json' "${auth[@]}" \
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
    exit 0
    ;;
  SessionStart)
    path=briefing
    # Only a compaction or /clear loses what was delivered; a resumed conversation still holds it,
    # so the daemon's session ledger sends just what is new or changed.
    redeliver=false
    case "$source_kind" in compact | clear) redeliver=true ;; esac
    query=null
    ;;
  UserPromptSubmit)
    path=recall
    redeliver=false
    query=$(jq '.prompt // null' <<<"$input")
    echo $(( $(cat "$state.prompts" 2>/dev/null || echo 0) + 1 )) >"$state.prompts"
    prompt_text=$(jq -r '.prompt // empty' <<<"$input")
    episode user "$prompt_text"
    # Remind the agent to record what was settled: on wording that states a rule, preference,
    # correction or decision, and otherwise every ONTYRA_CAPTURE_EVERY prompts.
    if [ "${ONTYRA_CAPTURE:-1}" != 0 ]; then
      n=$(cat "$state.prompts")
      last=$(( $(cat "$state.captured" 2>/dev/null || echo 0) ))
      cue='(from now on|going forward|always|never|remember|i prefer|prefer to|we decided|decided to|instead of|stop (doing|using)|no, (use|do|don.t))'
      if grep -qiE "$cue" <<<"$prompt_text" || [ $(( n - last )) -ge "${ONTYRA_CAPTURE_EVERY:-5}" ]; then
        echo "$n" >"$state.captured"
        capture_note="Ontyra: if this conversation has settled something durable that is not stored yet (a preference, rule, correction, or a decision with its reason), record it with \`remember\` as part of this turn. Likewise report with \`feedback\` (the memory, the query, the recall trace) a recalled memory that clearly helped, or that was wrong, outdated or beside the point. Otherwise do nothing and do not mention it."
      fi
    fi
    ;;
  *) exit 0 ;;
esac

body=$(jq -n \
  --argjson uris "$uris" --argjson signals "$signals" --arg session "$session" \
  --argjson query "$query" --argjson redeliver "$redeliver" \
  --argjson budget "${ONTYRA_BUDGET_TOKENS:-null}" --arg profile "${ONTYRA_PROFILE:-compact}" \
  '{context: {uris: $uris, signals: $signals, session: $session},
    query: $query, redeliver: $redeliver, budget_tokens: $budget, profile: $profile}') || exit 0

reply=$(post "$path" "$body") || exit 0

silent=$(jq -r 'if .silent == false then "false" else "true" end' <<<"$reply" 2>/dev/null) || silent=true
rendered=$(jq -r '.rendered // ""' <<<"$reply" 2>/dev/null) || rendered=""
[ "$silent" = "true" ] && rendered=""
# The recall's id goes with its memories, so a verdict on one can cite it (`feedback` trace)
if [ "$event" = UserPromptSubmit ] && [ -n "$rendered" ]; then
  trace=$(jq -r '.trace // empty' <<<"$reply" 2>/dev/null) || trace=""
  [ -n "$trace" ] && rendered="$rendered"$'\n'"(recall trace: $trace)"
fi

# At the start of a session: say when a reviewer sent proposals back with comments.
note=""
if [ "$event" = SessionStart ]; then
  waiting=$(curl -sS --max-time 4 "$url/v1/proposals?limit=50" "${auth[@]}" \
    -H "X-Ontyra-Actor: $actor" -H "X-Ontyra-Harness: $harness" 2>/dev/null) || waiting='[]'
  note=$(jq -r '[.[]? | select(.status == "changes_requested")] | if length == 0 then "" else
    "Ontyra: \(length) proposal(s) need changes before review (" + (map(.proposal // .id) | join(", ")) +
    "). Call pending_proposals to read the comments, then amend them." end' <<<"$waiting" 2>/dev/null) || note=""
fi
text="$rendered"
[ -n "$note" ] && text="${text:+$text$'\n\n'}$note"
[ -n "${capture_note:-}" ] && text="${text:+$text$'\n\n'}$capture_note"
[ -n "$text" ] || exit 0

jq -n --arg event "$event" --arg text "$text" \
  '{hookSpecificOutput: {hookEventName: $event, additionalContext: $text}}'
