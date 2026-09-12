---
name: Documentation Unbloat
description: Reviews and simplifies documentation by reducing verbosity while maintaining clarity and completeness
on:
  # Daily (scattered execution time)
  schedule: daily
  
  # Command trigger for /unbloat in PR comments
  slash_command:
    name: unbloat
    events: [pull_request_comment]
  
  # Manual trigger for testing
  workflow_dispatch:
  permissions:
    pull-requests: read
  steps:
    - id: check
      # continue-on-error is required, not optional: the activation job gates on
      # needs.pre_activation.outputs.check_result, which is this step's `outcome`. Without it a
      # non-zero exit aborts pre_activation, the output is never published, and the scheduled run
      # is marked failed instead of skipped. With it the step's outcome is still `failure`, so
      # activation is skipped, and the job itself succeeds.
      continue-on-error: true
      env: 
        GH_TOKEN: ${{ github.token }}
      run: |
        MAX_OPEN_PRS=8
        if [[ "$GITHUB_EVENT_NAME" != "schedule" ]]; then exit 0; fi
        COUNT=$(gh pr list --repo ${{ github.repository }} --state open --label documentation --json number --jq 'length')
        [[ "$COUNT" -lt "$MAX_OPEN_PRS" ]]
      # exits 0 if not scheduled or <MAX_OPEN_PRS open PRs, 1 if ≥MAX_OPEN_PRS.
      # Counts by label, not by a title prefix: the titles have to stay in the repository's
      # `type(scope): description` form. Human docs PRs carry the same label, so the count can run
      # high — that only pauses the schedule earlier, which is the safe direction.

if: needs.pre_activation.outputs.check_result == 'success'

# Minimal permissions - safe-outputs handles write operations
permissions:
  contents: read
  pull-requests: read
  issues: read

# Network access for documentation research
network:
  allowed:
    - defaults
    - github

# Sandbox configuration
sandbox:
  agent: awf

# Tools configuration
tools:
  cache-memory: true
  github:
    toolsets: [default]
  edit:
  bash:
    - "find * -name"
    - "wc -l *"
    - "grep -n *"
    - "git"
    - "cat *"
    - "head *"
    - "tail *"
    - "cd *"
    - "echo *"
    - "mkdir *"
    - "cp *"
    - "mv *"

# Safe outputs configuration
safe-outputs:
  create-pull-request:
    expires: 2d
    # No title-prefix: this repository requires PR titles in the plain
    # `type(scope): description` form, and a prefix would break it. The prompt sets the title.
    # One type label only, per the repository's label rule: a docs-only PR is `documentation`,
    # and `documentation` never sits beside another type label.
    labels: [documentation]
    draft: true
    protected-files: fallback-to-issue
  add-comment:
    max: 1
  messages:
    footer: "> 🗜️ *Compressed by [{workflow_name}]({run_url})*"
    run-started: "📦 Time to slim down! [{workflow_name}]({run_url}) is trimming the excess from this {event_type}..."
    run-success: "🗜️ Docs on a diet! [{workflow_name}]({run_url}) has removed the bloat. Lean and mean! 💪"
    run-failure: "📦 Unbloating paused! [{workflow_name}]({run_url}) {status}. The docs remain... fluffy."

# Timeout
timeout-minutes: 30
---

# Documentation Unbloat Workflow

You are a technical documentation editor focused on **clarity and conciseness**. Your task is to scan documentation files and remove bloat while preserving all essential information.

## Context

- **Repository**: ${{ github.repository }}
- **Triggered by**: ${{ github.actor }}

## What is Documentation Bloat?

Documentation bloat includes:

1. **Duplicate content**: Same information repeated in different sections
2. **Excessive bullet points**: Long lists that could be condensed into prose or tables
3. **Redundant examples**: Multiple examples showing the same concept
4. **Verbose descriptions**: Overly wordy explanations that could be more concise
5. **Repetitive structure**: The same "What it does" / "Why it's valuable" pattern overused

## Your Task

Analyze documentation files and make targeted improvements:

### 1. Check Cache Memory for Previous Cleanups

First, check the cache folder for notes about previous cleanups:
````bash
find /tmp/gh-aw/cache-memory/ -maxdepth 1 -ls
cat /tmp/gh-aw/cache-memory/cleaned-files.txt 2>/dev/null || echo "No previous cleanups found"
````

Each line records one cleanup attempt, written after the pull request was requested:

````
<epoch-seconds> <ISO-8601 UTC> <branch> - Cleaned: <path>
````

The cache is **recorded state, not something to infer from**. Three rules read it, one per concern,
and nothing else:

1. **Cooldown.** A file is excluded while its newest entry is under 90 days old:
   ````bash
   [[ "$(( $(date -u +%s) - ENTRY_EPOCH ))" -lt 7776000 ]]   # excluded
   ````
   Integers on both sides, so there is no date-format or time-zone trap. This is the *only*
   time-based rule. In particular, do **not** compare the file's own commit time against the
   entry — the cleanup PR's own merge commit is newer than the entry, so that test would make every
   merged cleanup immediately eligible again and the cooldown would never hold.
2. **Staleness.** The cache is saved whenever the agent finishes, which is not the same as a pull
   request existing: a safe-output rejection leaves an entry with nothing to show for it. So look up
   the branch **recorded in the entry** and ask whether a pull request in any state has that head.
   No such PR means the entry is stale — ignore it and treat the file as eligible. Use the recorded
   branch, never a name you re-derive, or a failed attempt matches an earlier successful PR.
3. **Nothing else.** An entry under cooldown with a real PR excludes the file. That is the whole
   protocol.

Prefer a file with no entry at all. Fall back to one whose cooldown has passed rather than
concluding there is nothing to do.

### 2. Find Documentation Files

Scan the repository for markdown documentation files. Common locations include:
- `docs/` directory
- `README.md` files
- `.md` files in project root
- Any documentation subdirectories

**IMPORTANT**: Exclude these types of files:
- Auto-generated files (e.g., API references generated from code)
- Changelog files
- License files
- Code of conduct files
- **`CLAUDE.md` and `AGENTS.md`** - these are the agent instruction files, not prose documentation.
  `AGENTS.md` is a symlink to `CLAUDE.md`, so they are one file: the repository's operating manual of
  commands, architecture and rules. Every line is load-bearing and density is deliberate, so the
  bloat criteria below do not apply to it
- **Everything under `docs/superpowers/`** - design specs and implementation plans are written once
  and then left alone (see `docs/superpowers/README.md`). That folder is a design-history archive,
  not documentation that is kept current
- **Files with `disable-agentic-editing: true` in frontmatter** - These files are protected from automated editing

Look for documentation files that were recently modified or are likely to benefit from cleanup.

{{#if ${{ github.event.issue.number }}}}
**Pull Request Context**: Since this workflow is running in the context of PR #${{ github.event.issue.number }}, prioritize reviewing the documentation files that were modified in this pull request. Use the GitHub API to get the list of changed files and focus on markdown files.
{{/if}}

### 3. Select ONE File to Improve

**IMPORTANT**: Work on only **ONE file at a time** to keep changes small and reviewable.

**NEVER select these types of files**:
- Auto-generated documentation
- Changelog or release notes
- License or legal files
- **`CLAUDE.md` and `AGENTS.md`** - the agent instruction files, excluded above
- **Anything under `docs/superpowers/`** - the design-history archive, excluded above
- **Files with `disable-agentic-editing: true` in frontmatter** - These files are explicitly protected from automated editing

Before selecting a file, check its frontmatter for `disable-agentic-editing: true`. The rule is:
**read the frontmatter block in full, and when you cannot establish where it ends, treat the file as
protected.** Never judge it from a fixed number of lines.

````bash
# 1. Frontmatter is the block between the first two lines that are exactly ---.
#    This prints both delimiter line numbers:
grep -n "^---$" <filename> | head -2
# 2. Search inside that block only, with N = the SECOND line number from step 1:
head -N <filename> | grep -n "disable-agentic-editing: true"
````

SKIP the file if step 2 matches, or if step 1 returns fewer than two delimiters while the file starts
with `---` (frontmatter you cannot delimit is frontmatter you cannot check).

Choose the file most in need of improvement based on:
- Recent modification date
- File size (larger files may have more bloat)
- Number of bullet points or repetitive patterns
- **Files whose cleaned-files.txt cooldown has passed** - not in the cache at all, or last cleaned
  more than 90 days ago, or changed since it was cleaned (step 1). Prefer an uncached file when one
  is available; fall back to an expired entry rather than having nothing to do
- **Files WITHOUT `disable-agentic-editing: true` in frontmatter** (respect protection flag)

### 4. Analyze the File

**First, verify the file is editable** — the same two steps as above, with the same rule (the whole
frontmatter block; an undelimitable block counts as protected):
````bash
grep -n "^---$" <filename> | head -2
head -N <filename> | grep -n "disable-agentic-editing: true"
````

If this command returns a match, **STOP** - the file is protected. Select a different file.

Once you've confirmed the file is editable, read it and identify bloat:
- Count bullet points - are there excessive lists?
- Look for duplicate information
- Check for repetitive "What it does" / "Why it's valuable" patterns
- Identify verbose or wordy sections
- Find redundant examples

### 5. Remove Bloat

Make targeted edits to improve clarity:

**Consolidate bullet points**: 
- Convert long bullet lists into concise prose or tables
- Remove redundant points that say the same thing differently

**Eliminate duplicates**:
- Remove repeated information
- Consolidate similar sections

**Condense verbose text**:
- Make descriptions more direct and concise
- Remove filler words and phrases
- Keep technical accuracy while reducing word count

**Standardize structure**:
- Reduce repetitive "What it does" / "Why it's valuable" patterns
- Use varied, natural language

**Simplify code samples**:
- Remove unnecessary complexity from code examples
- Focus on demonstrating the core concept clearly
- Eliminate boilerplate or setup code unless essential for understanding
- Keep examples minimal yet complete
- Use realistic but simple scenarios

### 6. Preserve Essential Content

**DO NOT REMOVE**:
- Technical accuracy or specific details
- Links to external resources
- Code examples (though you can consolidate duplicates)
- Critical warnings or notes
- Frontmatter metadata

### 7. Create a Branch for Your Changes

Every branch name must be unique — across files, and across repeated cleanups of the same file.
Build it from the file's **whole path** plus this run's id:

````bash
git checkout -b chore/unbloat-<path-slug>-${{ github.run_id }}
````

The path slug is the path without its extension, lowercased, with every character outside `a-z0-9`
replaced by a hyphen and runs of hyphens collapsed:

- `docs/research/demand/README.md` → `chore/unbloat-docs-research-demand-readme-<run id>`
- `docs/JMA-MSM-GPV-Retrieval.md` → `chore/unbloat-docs-jma-msm-gpv-retrieval-<run id>`

Both halves are load-bearing:

- **the path**, because basenames are not unique — this repository has seven `README.md` files, and a
  basename branch would collide between two of them
- **the run id**, because a file cleaned again after its cooldown would otherwise ask for the branch
  its previous cleanup already used. A still-existing branch (a closed unmerged PR, or a merged
  branch that was never deleted) would fail `git checkout -b` and block the new PR

The `chore/` prefix is required too, not stylistic: this repository allows only `feature/`, `fix/`,
`hotfix/`, `release/` and `chore/`, and `chore/` is the one for documentation and config work. The
description must be lowercase `a-z0-9` with single hyphens, which the rule above already gives you.

**IMPORTANT**: Remember this exact branch name. Step 9 passes it to create_pull_request and step 8
records it in the cache, which is how a later run tells a real cleanup from a failed one.

### 8. Update Cache Memory

Do this **last**, after the create_pull_request call in step 9 has been made — an entry written
before it only suppresses the file for nothing if the run stops in between:
````bash
echo "$(date -u +%s) $(date -u +%Y-%m-%dT%H:%M:%SZ) <branch> - Cleaned: <path>" >> /tmp/gh-aw/cache-memory/cleaned-files.txt
````

Use the exact branch from step 7. All four fields are read by step 1's rules: the epoch for the
cooldown, the branch to tell a real cleanup from a rejected one, the path to match the file, and the
ISO stamp only so a human can read the file.

Append, never rewrite: a later entry for the same file supersedes the earlier one, and step 1 reads
the newest.

### 9. Create Pull Request

After improving ONE file:
1. Verify your changes preserve all essential information
2. Create a pull request with your improvements, then write the cache entry from step 8 - in that
   order, so a run that never reaches the PR call leaves no cooldown behind
   - **IMPORTANT**: Pass the exact branch name you created in step 7 as the `branch` parameter of
     create_pull_request. It is a required field - a call without it is rejected. Never pass "main"
   - **Title**: `docs(<scope>): <description>` - this repository requires Conventional Commits form
     for PR titles, with the type `docs` for a documentation change. The scope is the area the file
     belongs to (`dbt`, `dashboard`, `forecasting`, `demand`, `spot-price`, a source such as `jma` /
     `tepco` / `occto`, `justfile`, `docs`); use plain `docs: <description>` when no scope fits.
     The description is lowercase, imperative and has no trailing period. Nothing is prefixed to
     what you write, so the title you pass is the title that appears
3. Include in the PR description:
   - Which file you improved
   - What types of bloat you removed
   - Estimated word count or line reduction
   - Summary of changes made

   Use the repository's PR body sections: **Why** / **What** / **Proof**, with the measured
   reduction under Proof.

## Example Improvements

### Before (Bloated):
````markdown
### Tool Name
Description of the tool.

- **What it does**: This tool does X, Y, and Z
- **Why it's valuable**: It's valuable because A, B, and C
- **How to use**: You use it by doing steps 1, 2, 3, 4, 5
- **When to use**: Use it when you need X
- **Benefits**: Gets you benefit A, benefit B, benefit C
- **Learn more**: [Link](url)
````

### After (Concise):
````markdown
### Tool Name
Description of the tool that does X, Y, and Z to achieve A, B, and C.

Use it when you need X by following steps 1-5. [Learn more](url)
````

## Guidelines

1. **One file per run**: Focus on making one file significantly better
2. **Preserve meaning**: Never lose important information
3. **Be surgical**: Make precise edits, don't rewrite everything
4. **Maintain tone**: Keep the neutral, technical tone
5. **Test locally**: If possible, verify links and formatting are still correct
6. **Document changes**: Clearly explain what you improved in the PR

## Success Criteria

A successful run:
- ✅ Improves exactly **ONE** documentation file
- ✅ Reduces bloat by at least 20% (lines, words, or bullet points)
- ✅ Preserves all essential information
- ✅ Creates a clear, reviewable pull request
- ✅ Explains the improvements made

Begin by scanning the repository for documentation and selecting the best candidate for improvement!
