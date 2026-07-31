# Deepening

How to deepen a cluster of shallow modules safely, given its dependencies. Assumes the vocabulary in [LANGUAGE.md](LANGUAGE.md) — **module**, **interface**, **seam**, **adapter**.

## Dependency categories

When assessing a candidate for deepening, classify its dependencies. The category determines how the deepened module is tested across its seam.

### 1. In-process

Pure computation, in-memory state, no I/O. Always deepenable — merge the modules and test through the new interface directly. No adapter needed.

### 2. Local Runtime Equivalent

Dependencies with a real local runtime that executes the same contract, such as PGLite for Postgres or a temporary filesystem. Deepen only when the local runtime exercises the production Interface rather than replacing a collaborator with programmed answers. A throwaway stand-in may help diagnose a hypothesis, but it is explicitly non-proof and cannot satisfy RED/GREEN or production verification.

### 3. Remote But Owned

Your own services across a network boundary: microservices, internal APIs, queues, or similar owned surfaces. Define a **port** only when runtime variation already exists. Required behavior proof crosses the real owned-service Seam in an integration environment. A local protocol harness may provide fast diagnostic feedback, but it is non-proof and cannot satisfy RED/GREEN or production verification.

### 4. True External

Third-party services you do not control. Prefer the provider sandbox/test tenant, contract fixtures captured from the real provider, or an owned end-to-end environment. A programmed response stand-in may isolate a diagnostic question, but it is non-proof and cannot satisfy RED/GREEN or production verification.

## Seam discipline

- **One runtime adapter means a hypothetical seam. Two genuine runtime variants can justify a real one.** A test-only substitute does not count as a second adapter and must not create production indirection.
- **Internal seams vs external seams.** A deep module can have internal seams (private to its implementation, used by its own tests) as well as the external seam at its interface. Don't expose internal seams through the interface just because tests use them.

## Testing strategy: replace, don't layer

- Old unit tests on shallow modules become waste once tests at the deepened module's interface exist — delete them.
- Write new tests at the deepened module's interface. The **interface is the test surface**.
- Tests assert on observable outcomes through the interface, not internal state.
- Tests should survive internal refactors — they describe behaviour, not implementation. If a test has to change when the implementation changes, it's testing past the interface.
