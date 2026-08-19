# Behavior Test Reference

A strong test proves one **independently-failable observable outcome** through the public Interface or externally observable state governed by that Interface.

A behavior test survives internal refactoring: if observable behavior is unchanged but the test breaks, the test is coupled to implementation. Several assertions are valid when they jointly prove one behavior; one assertion can still hide an over-broad behavior.

## What a slice must prove

| Slice | Proof shape |
|---|---|
| Atomic behavior | One outcome under one relevant precondition; split outcomes that different defects could break independently. |
| Complete failure contract | Expected error or refusal, the observable state required by the contract, and the correct outward result, exit status, or propagated exception. |
| Touched-Seam preservation | A rerouted public operation retains each material success, failure, input-form, state, and atomicity guarantee the new path can alter. |
| Architecture falsifier | A reachable semantic bypass challenges a load-bearing mechanism or state boundary, not merely its obvious spelling. A passing probe is regression evidence, not a manufactured RED. |
| Interaction slice | One behavior cannot mutate state or invalidate a guarantee owned by another through shared state, lifecycle, ordering, or a touched Seam. |

## A real RED

The RED must reach the mapped Seam and fail at the assertion for the claimed product behavior. Give that assertion a behavior-specific marker and record the same marker as `redFailure` in preflight. For directly invoked pytest and unittest, the recorder also requires at least one executed test and refuses collection, setup, loader, or zero-test failures; opaque runners are exact-bound and recorded as weaker marker-only evidence.

A test for “rollback restores exact state” is **not** a RED for rollback when it stops first at `AttributeError: enable_safe_import`. That proves only that an API is absent. Split API availability from rollback semantics and drive each independently.

```python
def test_rejected_transfer_preserves_balances():
    before = balances(account_a, account_b)

    result = transfer(account_a, account_b, amount=-1)

    assert result.error == "invalid amount", "INVALID_AMOUNT_WAS_ACCEPTED"
    assert balances(account_a, account_b) == before
    assert result.committed is False
```

The error, contract-required balance preservation, and outward result jointly prove one failure behavior.

## Observable state

Verify through the public Interface or the externally observable state governed by that Interface. Do not inspect an internal store merely because it is convenient. Direct state inspection is valid when the state itself is a public product artifact, or when the Interface explicitly promises its exact persisted state.

Avoid tests that:

- replace production collaborators with programmed answers;
- assert private methods, call counts, or interior sequencing instead of behavior;
- fail because setup, syntax, collection, fixture shape, or a missing API prevents the mapped Seam from being reached;
- combine independently-failable outcomes under one broad name;
- prove only the happy path while omitting declared failure or preservation behavior;
- inspect private persistence when the public contract does not expose or govern it.
