# Boundary Strategies Under the Canonical Mock Ban

The canonical mock-ban statement lives in `~/.claude/CLAUDE.md` and governs every claimed RED/GREEN or production proof. This reference does not restate or weaken it.

Use the closest real production Interface available:

- **In-process behavior:** call the public Interface with real implementation code.
- **Filesystem or local runtime:** use a temporary filesystem or a real local runtime that executes the production contract.
- **Owned remote service:** use the owned integration environment or a real service instance configured for tests.
- **Third-party provider:** use the provider sandbox/test tenant or an owned end-to-end environment. Captured provider fixtures may support contract analysis, but they do not replace the live production Seam as behavior proof.
- **Browser or device behavior:** use the authenticated staging flow or the strongest real runtime harness available.

A throwaway programmed stand-in can isolate a diagnostic hypothesis. Label it diagnostic-only, delete it after use, and never count it as RED/GREEN, regression, or production verification.

When no real Seam can be driven, record the missing proof surface and use `/codebase-design` or `/improve-codebase-architecture`; do not invent a second production path for the test.
