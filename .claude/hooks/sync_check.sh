#!/bin/sh
# Claude Code UserPromptSubmit hook: tells the agent about teammates' work before it edits anything.
# Anything printed here is added to the agent's context. It prints nothing when there is no news.
# It only fetches and reads; it never changes your branch or files.

git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# Fetch at most once every 2 minutes, so prompts stay quick.
fetch_head=$(git rev-parse --git-path FETCH_HEAD)
if [ -z "$(find "$fetch_head" -mmin -2 2>/dev/null)" ]; then
    git fetch --quiet origin 2>/dev/null
fi

git rev-parse --verify --quiet origin/main >/dev/null || exit 0
current=$(git rev-parse --abbrev-ref HEAD)

behind=$(git rev-list --count HEAD..origin/main)
if [ "$behind" -gt 0 ]; then
    echo "SYNC CHECK: origin/main has $behind commit(s) that $current does not. Read them before editing these files:"
    git log --oneline --max-count=8 HEAD..origin/main
    git diff --name-only "HEAD...origin/main" | head -n 20
fi

# Teammates' unmerged branches changed in the last 2 days, with the files they touch.
cutoff=$(( $(date +%s) - 172800 ))
git for-each-ref --format='%(refname:short) %(committerdate:unix)' refs/remotes/origin |
while read -r branch stamp; do
    case "$branch" in origin/HEAD|origin|origin/main|"origin/$current") continue ;; esac
    [ "$stamp" -ge "$cutoff" ] || continue
    [ "$(git rev-list --count "origin/main..$branch")" -gt 0 ] || continue
    echo "SYNC CHECK: unmerged work on $branch touches: $(git diff --name-only "origin/main...$branch" | head -n 12 | tr '\n' ' ')"
done
exit 0
