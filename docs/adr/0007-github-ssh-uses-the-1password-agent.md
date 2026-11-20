# GitHub SSH uses the 1Password agent and a first-position block

Bootstrap writes the Factory's `Host github.com` block at the top of `~/.ssh/config` and leaves every other line byte-identically in place. It sets `IdentityAgent` to 1Password's socket and `IdentitiesOnly no`, and never generates a key or calls GitHub's key API. The agent already holds ranokay's GitHub key, while adding a key would need an `admin:public_key` token the Factory's `gh` login does not have and would put a long-lived private key on disk.

Position and `IdentitiesOnly no` are not style choices. OpenSSH keeps the **first** value it obtains for each option, so a catch-all `Host *` with `IdentitiesOnly yes` earlier in the file silently stops the agent's keys from ever being offered; the block must come first, and it must say `no` explicitly so that global hardening cannot hide the agent keys. Both mistakes were caught only by a live `ssh -T`: a block at the end with `IdentitiesOnly yes` parsed cleanly under `ssh -G` and then failed with `Permission denied (publickey)`.

`bootstrap` owns only the marked block: it removes every copy of it, repairs a corrupt or hand-edited copy, replaces it with the current form, and preserves the user's surrounding bytes, including CRLF endings. A user-authored `Host github.com` block without the marker is never edited; when the origin is still HTTPS it gets a loud "SSH auth through it is unverified" warning, and the origin is still moved to SSH. Running bootstrap is the repair path after a config rewrite or a 1Password path change.

Rejected alternatives: appending the block (loses against an earlier `Host *`), `IdentitiesOnly yes` (hides agent keys), and a generated key with `gh ssh-key add` (needs an interactive scope refresh and a private key on disk).
