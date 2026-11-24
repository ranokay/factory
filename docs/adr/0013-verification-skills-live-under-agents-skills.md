# Verification skills live under .agents/skills

Every project commits its `verify-<app>` skill at `.agents/skills/verify-<app>/`, with a committed relative symlink at `.claude/skills/verify-<app>` pointing to it. The skill's feature map is `references/features/` and its CLI is the project's `verify:<app>` mise task. The previous generator default, `.cursor/skills/`, is retired.

Codex scans repository `.agents/skills`, and OpenCode reads it as a project compatibility source. Claude Code reads only project `.claude/skills`, but follows a symlinked skill folder. No harness in the Factory reads `.cursor/skills`, so a skill there was invisible to the agents that need it.

The alternative is two committed copies, which drift, or a generator that writes only one harness's path, which makes verification harness-specific. The cost is one committed symlink per project and the Factory's `scripts/verify-skill.py --root <project>` check, which fails when the canonical directory is elsewhere or the link is missing or absolute.
