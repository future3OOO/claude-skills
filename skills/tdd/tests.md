# Behavior Test Reference

A strong test proves one **independently-failable observable outcome** through the public Interface or externally observable state governed by that Interface.

A behavior test survives internal refactoring: if observable behavior is unchanged but the test breaks, the test is coupled to implementation.

Several assertions are valid when they jointly prove one behavior. One assertion can still hide an over-broad behavior.

## What a slice must prove

| Slice | Proof shape |
|---|---|
| Atomic behavior | One outcome under one relevant precondition; split outcomes that different defects could break independently. |
| Complete failure contract | Expected error or refusal, the observable state required by the contract, and the correct outward result, exit status, or propagated exception. |
| Touched-Seam preservation | An existing public operation rerouted through the new path retains its material observable contract. |
| Architecture falsifier | A reachable semantic bypass challenges a load-bearing guard or state boundary, not merely its obvious spelling. If the probe passes, keep it only as material regression evidence; do not manufacture a RED. |
| Interaction slice | Two individually GREEN behaviors sharing state, lifecycle, or ordering cannot invalidate one another's guarantee. |

## Complete failure example

```python
def test_rejected_transfer_preserves_balances():
    before = balances(account_a, account_b)

    result = transfer(account_a, account_b, amount=-1)

    assert result.error == "invalid amount"
    assert balances(account_a, account_b) == before
    assert result.committed is False
```

The error, contract-required balance preservation, and outward result together prove one failure behavior.

## Observable state

Verify through the public Interface or the externally observable state governed by that Interface. Do not inspect an internal store merely because it is convenient. Direct state inspection is valid when the state itself is a public product artifact, or when the Interface explicitly promises its exact persisted state.

Avoid tests that:

- replace production collaborators with programmed answers;
- assert private methods, call counts, or interior sequencing instead of behavior;
- fail because setup, syntax, or fixture shape is wrong;
- combine independently-failable outcomes under one broad name;
- prove only the happy path while omitting the declared failure state;
- inspect private persistence when the public contract does not expose or govern it.
