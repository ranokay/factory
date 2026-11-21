# Factory agent pushes use HTTPS

Factory-managed GitHub clones use an HTTPS origin push URL and local `credential.https://github.com.helper` values that reset inherited helpers before `!gh auth git-credential`. Linked worktrees share this configuration. Interactive SSH configuration and fetch URLs remain available. This supersedes ADR 0007's implication that agents push through the 1Password SSH agent. Bootstrap checks the persistent settings and performs a noninteractive HTTPS dry run.
