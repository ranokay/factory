# Codex shells inherit registry secrets

The MCP renderer enables `shell_environment_policy.ignore_default_excludes` so registry variables such as `CONTEXT7_API_KEY` reach Codex shells. In Codex 0.171.0 the default exclusion runs before `include_only`, so that option cannot restore individual excluded names. This also passes other inherited names containing KEY, SECRET or TOKEN. Claude and OpenCode already pass these variables. The 1Password service account token is supplied only to individual `op` processes and is never installed in launchd or a shell environment.
