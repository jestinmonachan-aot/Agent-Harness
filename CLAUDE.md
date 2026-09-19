# CLAUDE.md — Agent Harness Project

## Context
This repo is an agent harness used to analyze a single legacy
microservice (GLPI / osTicket / MantisBT — pick one) ahead of a
modernization migration. Claude Code is invoked here to inspect the
target codebase and report vulnerabilities/blockers, not to modify
the target repo directly.

## Rules for Claude Code in this project
- Do not modify files in any cloned target repo under `reports/` or
  temp clone directories — analysis only, read-only access.
- When asked to analyze a codebase, return findings as a structured
  markdown list (severity, file/line if known, short description).
- Flag hardcoded secrets, outdated/vulnerable dependencies, and
  insecure auth patterns as high priority.
- Keep responses concise — this harness parses your output
  programmatically, so avoid conversational filler.

## Notes
- Extend this file as you discover prompt patterns that work well
  for the specific target microservice you choose.
