#!/bin/bash
set -euo pipefail

# Generate changelog and calculate the next version based on conventional commits.
# Usage: ./generate-changelog.sh [comparison_tag]
# Environment:
#   FORCE_PATCH=true optionally creates a patch release when content changed without a conventional commit
# Outputs:
#   - Markdown changelog to stdout
#   - skip_release=true|false, semantic_bump=true|false, bump_level, and version to stderr

FORCE_PATCH="${FORCE_PATCH:-false}"
CHANGELOG_APP_NAME="Twitter"
CHANGELOG_APP_NAME_LOWER=$(printf '%s' "$CHANGELOG_APP_NAME" | tr '[:upper:]' '[:lower:]')
PIKO_REPOSITORY="${PIKO_REPOSITORY:-https://github.com/crimera/piko.git}"
PIKO_BRANCH="${PIKO_BRANCH:-x-lite}"
PIKO_REPO_URL="${PIKO_REPO_URL:-https://github.com/crimera/piko}"
PATCHES_BUNDLE_FILE="patches-bundle.json"
PREVIOUS_TAG="${1:-$(git tag --merged HEAD --sort=-version:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n1 || echo '')}"

if [[ "$FORCE_PATCH" != "true" && "$FORCE_PATCH" != "false" ]]; then
    echo "FORCE_PATCH must be 'true' or 'false'" >&2
    exit 1
fi

PIKO_DIRECTORY=$(mktemp -d)
trap 'rm -rf "$PIKO_DIRECTORY"' EXIT

git clone --quiet --single-branch --branch "$PIKO_BRANCH" \
    "$PIKO_REPOSITORY" "$PIKO_DIRECTORY"

PREVIOUS_PIKO_COMMIT=""
if [ -f "$PATCHES_BUNDLE_FILE" ]; then
    PREVIOUS_PIKO_COMMIT=$(jq -r '.piko_commit // empty' "$PATCHES_BUNDLE_FILE")
fi

# Generate semantic changes from the same upstream branch that is built for the
# release.  The local repository only provides the previous release version.
if [ -n "$PREVIOUS_PIKO_COMMIT" ]; then
    if ! git -C "$PIKO_DIRECTORY" cat-file -e "${PREVIOUS_PIKO_COMMIT}^{commit}" 2>/dev/null; then
        echo "Previous Piko commit is not present in the cloned x-lite branch: ${PREVIOUS_PIKO_COMMIT}" >&2
        exit 1
    fi
    COMMIT_RANGE="${PREVIOUS_PIKO_COMMIT}..HEAD"
else
    COMMIT_RANGE="HEAD"
fi

piko_git() {
    git -C "$PIKO_DIRECTORY" "$@"
}

get_bump_level() {
    local commit_range="$1"
    local has_breaking=false
    local has_feat=false
    local has_patch=false

    while IFS= read -r line; do
        [ -z "$line" ] && continue

        local commit_hash
        local commit_msg
        local commit_body
        commit_hash=$(echo "$line" | cut -d'|' -f1)
        commit_msg=$(echo "$line" | cut -d'|' -f3-)

        if [[ "$commit_msg" =~ ^(ci|agent|chore|doc|Merge|Revert) ]]; then
            continue
        fi

        commit_body=$(piko_git log -1 --pretty=format:"%b" "$commit_hash" 2>/dev/null || echo "")
        if [[ "$commit_body" == *"BREAKING CHANGE:"* ]] || [[ "$commit_msg" == *"!:"* ]]; then
            has_breaking=true
        fi

        if echo "$commit_msg" | grep -qE '^(feat|fix|bump|update|ui|refactor|perf)(\([^)]+\))?!?:[[:space:]]+.+$'; then
            case "$(echo "$commit_msg" | sed -E 's/^(feat|fix|bump|update|ui|refactor|perf).*/\1/')" in
                feat)
                    has_feat=true
                    ;;
                fix|bump|update|ui|refactor|perf)
                    has_patch=true
                    ;;
            esac
        fi
    done < <(piko_git log --abbrev=7 --pretty=format:"%h|%H|%s%n" "$commit_range" 2>/dev/null || true)

    if [ "$has_breaking" = true ]; then
        echo "breaking"
        return
    fi

    if [ "$has_feat" = true ]; then
        echo "feat"
        return
    fi

    if [ "$has_patch" = true ]; then
        echo "patch"
        return
    fi

    echo "none"
}

features=()
fixes=()
updates=()
improvements=()

HAS_BREAKING=false
HAS_FEAT=false
HAS_PATCH=false

while IFS= read -r line; do
    [ -z "$line" ] && continue

    commit_hash=$(echo "$line" | cut -d'|' -f1)
    commit_hash_full=$(echo "$line" | cut -d'|' -f2)
    commit_msg=$(echo "$line" | cut -d'|' -f3-)

    if [[ "$commit_msg" =~ ^(ci|agent|chore|doc|Merge|Revert) ]]; then
        continue
    fi

    commit_body=$(piko_git log -1 --pretty=format:"%b" "$commit_hash" 2>/dev/null || echo "")
    if [[ "$commit_body" == *"BREAKING CHANGE:"* ]] || [[ "$commit_msg" == *"!:"* ]]; then
        HAS_BREAKING=true
    fi

    if echo "$commit_msg" | grep -qE '^(feat|fix|bump|update|ui|refactor|perf)(\([^)]+\))?!?:[[:space:]]+.+$'; then
        type=$(echo "$commit_msg" | sed -E 's/^(feat|fix|bump|update|ui|refactor|perf).*/\1/')
        scope=$(echo "$commit_msg" | sed -nE 's/^[^(:]+\(([^)]+)\)!?:.*/\1/p')
        desc=$(echo "$commit_msg" | sed -E 's/^[^(:]+(\([^)]+\))?!?:[[:space:]]+//')

        changelog_scope="$CHANGELOG_APP_NAME"
        if [ -n "$scope" ]; then
            normalized_scope=$(printf '%s' "$scope" | tr '[:upper:]' '[:lower:]')
            if [ "$normalized_scope" != "$CHANGELOG_APP_NAME_LOWER" ] &&
               [[ "$normalized_scope" != "$CHANGELOG_APP_NAME_LOWER - "* ]]; then
                changelog_scope="$CHANGELOG_APP_NAME - $scope"
            fi
        fi

        commit_link="([${commit_hash}](${PIKO_REPO_URL}/commit/${commit_hash_full}))"

        entry="* **${changelog_scope}:** ${desc}"
        if [ -n "$commit_link" ]; then
            entry+=" ${commit_link}"
        fi

        case "$type" in
            feat)
                HAS_FEAT=true
                features+=("$entry")
                ;;
            fix)
                HAS_PATCH=true
                fixes+=("$entry")
                ;;
            bump)
                HAS_PATCH=true
                updates+=("$entry")
                ;;
            update)
                HAS_PATCH=true
                updates+=("$entry")
                ;;
            ui)
                HAS_PATCH=true
                improvements+=("$entry")
                ;;
            refactor|perf)
                HAS_PATCH=true
                improvements+=("$entry")
                ;;
        esac
    fi
done < <(piko_git log --abbrev=7 --pretty=format:"%h|%H|%s%n" "$COMMIT_RANGE" 2>/dev/null || true)

BASE_BUMP_LEVEL=$(get_bump_level "$COMMIT_RANGE")
PIKO_BUMP_LEVEL="$BASE_BUMP_LEVEL"

if [ "$FORCE_PATCH" = true ] && [ "$BASE_BUMP_LEVEL" = "none" ]; then
    BASE_BUMP_LEVEL="patch"
fi

if [ -n "$PREVIOUS_TAG" ]; then
    PREV_VERSION="${PREVIOUS_TAG#v}"

    if [[ "$PREV_VERSION" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
        MAJOR="${BASH_REMATCH[1]}"
        MINOR="${BASH_REMATCH[2]}"
        PATCH="${BASH_REMATCH[3]}"
    else
        MAJOR=0
        MINOR=0
        PATCH=0
    fi
else
    MAJOR=0
    MINOR=0
    PATCH=0
fi

HAS_VERSION_BUMP=false
if [ "$HAS_BREAKING" = true ] || [ "$HAS_FEAT" = true ] || [ "$HAS_PATCH" = true ] || \
   { [ "$FORCE_PATCH" = true ] && [ "$PIKO_BUMP_LEVEL" = "none" ]; }; then
    HAS_VERSION_BUMP=true
fi

case "$BASE_BUMP_LEVEL" in
    breaking)
        MAJOR=$((MAJOR + 1))
        MINOR=0
        PATCH=0
        ;;
    feat)
        MINOR=$((MINOR + 1))
        PATCH=0
        ;;
    patch)
        PATCH=$((PATCH + 1))
        ;;
esac

COMPARISON_VERSION="${PREVIOUS_TAG#v}"
if [[ "$COMPARISON_VERSION" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    COMPARISON_MAJOR="${BASH_REMATCH[1]}"
    COMPARISON_MINOR="${BASH_REMATCH[2]}"
    COMPARISON_PATCH="${BASH_REMATCH[3]}"

    if [ "$COMPARISON_MAJOR" -gt "$MAJOR" ] || \
       { [ "$COMPARISON_MAJOR" -eq "$MAJOR" ] && [ "$COMPARISON_MINOR" -gt "$MINOR" ]; } || \
       { [ "$COMPARISON_MAJOR" -eq "$MAJOR" ] && [ "$COMPARISON_MINOR" -eq "$MINOR" ] && [ "$COMPARISON_PATCH" -gt "$PATCH" ]; }; then
        MAJOR="$COMPARISON_MAJOR"
        MINOR="$COMPARISON_MINOR"
        PATCH="$COMPARISON_PATCH"
    fi
fi

BASE_VERSION="${MAJOR}.${MINOR}.${PATCH}"
NEW_VERSION="$BASE_VERSION"

echo "skip_release=$([ "$HAS_VERSION_BUMP" = true ] && echo 'false' || echo 'true')" >&2
echo "semantic_bump=$([ "$PIKO_BUMP_LEVEL" != "none" ] && echo 'true' || echo 'false')" >&2
echo "bump_level=$BASE_BUMP_LEVEL" >&2
echo "v${NEW_VERSION}" >&2

output_section() {
    local title="$1"
    shift
    local arr=("$@")

    if [ ${#arr[@]} -eq 0 ]; then
        return
    fi

    echo "### ${title}"
    printf "%s\n" "${arr[@]}"
    echo ""
}

if [ ${#fixes[@]} -gt 0 ]; then
    output_section "🐛 Bug Fixes" "${fixes[@]}"
fi

if [ ${#features[@]} -gt 0 ]; then
    output_section "✨ New Features" "${features[@]}"
fi

if [ ${#updates[@]} -gt 0 ]; then
    output_section "🚀 Updated App Support" "${updates[@]}"
fi

if [ ${#improvements[@]} -gt 0 ]; then
    output_section "🔧 Improvements" "${improvements[@]}"
fi

if [ ${#features[@]} -eq 0 ] && [ ${#fixes[@]} -eq 0 ] && [ ${#updates[@]} -eq 0 ] && \
   [ ${#improvements[@]} -eq 0 ]; then
    echo "*No notable changes in this release.*"
fi