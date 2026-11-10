# ADR 0001: Ship as a Claude Code plugin plus templates, not a template repo only

- Status: accepted
- Date: 2026-11-10

## Context

The harness (guard hook, merge gate, CI, branch protection, orchestrator/builder/reviewer rules)
was proven in one real project. To reuse it we could:

1. **Template repo only.** "Use this template" or copy the files by hand.
2. **Plugin only.** Everything runs from the plugin install dir; nothing is copied.
3. **Plugin + templates.** A plugin ships a `harness-init` skill and a plugin-level guard hook.
   The skill copies `harness/` templates into the target project and adapts them to its stack.

## Decision

Option 3.

## Why

- **It adapts to each stack.** Projects differ: Go, TS or both; monorepo dirs; existing CI and
  CLAUDE.md; default branch; which paths are sensitive. A template is one fixed shape that
  each user must hand-edit. The init skill detects the stack, asks about what it cannot detect,
  merges into existing files instead of clobbering them, and runs verify until green.
- **It updates centrally.** Guard fixes (such as blocking `--force-with-lease`) ship as a plugin
  update. The plugin-level hook protects every opted-in project at once, and re-running
  `harness-init` refreshes the copied files. Copies of a template repo drift and never get fixes.
- **Some files must still be copied.** CI workflows, branch protection, `.claude/settings.json`
  and `scripts/verify.sh` have to live in the target repo: GitHub Actions and teammates without
  the plugin need them, and CI cannot see a plugin install dir. So "plugin only" cannot work.
- **Fail-safe defaults.** The plugin hook is inert in projects without `harness.config.json`
  (`--require-config`), so installing the plugin never surprises an unrelated project.

## Consequences

- There are two copies of the guard: the plugin's and the target's. The target copy can lag until
  `harness-init` is re-run. Both fail closed, and branch protection is the backstop.
- This repo must keep the files it uses itself in sync with the copies it ships. A unit test
  enforces it.
- The plugin name cannot start with `claude-` (reserved by the validator), so the plugin is
  `agent-harness` in the `claude-harness` marketplace.
