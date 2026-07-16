#!/usr/bin/env bash
# Check 2 of the contract freshness gate (T7): WARN (never fail) when a consumer
# is pinned behind the latest published contract tag. Surfaces "a newer contract
# exists" without blocking unrelated work — bumping the pin stays a human/agent
# action (T7 out-of-scope: no auto-bump).
#
# Usage: warn-if-behind.sh <pin-file> <consumer-label>
#   <pin-file>       file whose first non-comment line is the pinned tag
#                    (e.g. app/internal/contract/CONTRACT_TAG -> "timeline-entry/v2.1")
#   <consumer-label> human name for the warning (e.g. "Go app", "dashboard")
#
# Requires the phantom-contracts fetch to be authenticated already: the caller
# runs `git config --global url.<https+token>.insteadOf git@github.com:` so the
# ls-remote below reads the private repo. If auth is missing this exits 0 with a
# warning — this check must never turn CI red.
set -uo pipefail

PIN_FILE="${1:?pin file required}"
LABEL="${2:?consumer label required}"
REMOTE="git@github.com:neverprepared/phantom-contracts.git"
PREFIX="timeline-entry/"

# First non-blank, non-comment line is the pinned tag.
PINNED="$(grep -vE '^\s*(#|$)' "$PIN_FILE" | head -n1 | tr -d '[:space:]')"
if [ -z "$PINNED" ]; then
  echo "::warning title=Freshness check skipped::could not read a pinned tag from ${PIN_FILE}"
  exit 0
fi

# List published timeline-entry tags, strip the peeled ^{} refs, keep vMAJOR.MINOR.
TAGS="$(git ls-remote --tags "$REMOTE" "refs/tags/${PREFIX}v*" 2>/dev/null \
  | sed -E 's#.*refs/tags/##; /\^\{\}$/d')"
if [ -z "$TAGS" ]; then
  echo "::warning title=Freshness check skipped::could not list phantom-contracts tags (auth or network). ${LABEL} pin=${PINNED} left unverified."
  exit 0
fi

# Highest version wins. `sort -V` orders v2.1 < v2.10 < v3.0 correctly.
LATEST="$(printf '%s\n' "$TAGS" | sort -V | tail -n1)"

if [ "$PINNED" = "$LATEST" ]; then
  echo "${LABEL} is pinned to the latest contract tag (${PINNED})."
  exit 0
fi

# If the pin sorts at-or-above latest (e.g. pin is ahead pre-publish), don't warn.
HIGHEST="$(printf '%s\n%s\n' "$PINNED" "$LATEST" | sort -V | tail -n1)"
if [ "$HIGHEST" = "$PINNED" ]; then
  echo "${LABEL} pin (${PINNED}) is at or ahead of the latest published tag (${LATEST}) — nothing to warn."
  exit 0
fi

echo "::warning title=Contract pin behind latest::${LABEL} is pinned to ${PINNED}, but ${LATEST} is published. Re-pin to ${LATEST} and regenerate bindings to adopt the newer contract (see T7 / DECISIONS.md D2)."
exit 0
