# go

## Toolchain

- golangci-lint owns lint and uses gofumpt for formatting. Start from the Profile's configuration template.
- govulncheck is a Gate. It needs the Go vulnerability database; an unavailable database fails the check rather than silently skipping it.
- Handle errors at the boundary and preserve context. Prefer the standard library and small interfaces at the point of use.
