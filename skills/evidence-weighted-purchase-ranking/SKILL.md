---
name: evidence-weighted-purchase-ranking
description: Evidence-weighted ranking for marketplace purchases. Use when choosing among comparable listings and seller rating plus review/feedback or sales volume are available, or when another skill needs a seller-reputation score to support a purchase recommendation.
---

# Evidence-Weighted Purchase Ranking

Rank **comparable** marketplace listings without confusing seller reputation with item quality. The seller score answers _how much confidence should I place in buying from this seller?_ It does not answer whether the item itself is the right model, condition, specification, or price.

## 1. Establish the comparison set

Before scoring sellers, align the listings on the purchase that matters: model/version, specification, condition, included accessories, authenticity evidence, warranty/returns, shipping, taxes, and total delivered price. Exclude a hard mismatch. Keep a meaningful difference only if it remains explicit in the final comparison.

**Complete when:** every retained listing is genuinely comparable for the user's purchase, or every material difference is explicitly represented rather than silently absorbed into seller reputation.

## 2. Align seller evidence

For every retained listing collect, when available:

- `R`: seller rating
- `[L, U]`: the rating scale
- `n`: seller review/feedback count supporting that rating
- `s`: seller sales/transaction count
- the scope and time horizon of each field

Do not mix scopes. Seller-level rating and feedback belong with seller-level sales. A listing-level "sold" count is item popularity, not seller sales, unless the platform explicitly defines it otherwise. Missing is not zero.

**Complete when:** every candidate has a numeric value or explicit `missing` state for each seller field, and no seller-level and listing-level counts are mislabeled as the same signal.

## 3. Calculate SellerScore

Normalize the rating to `[0,1]`:

```text
r = (R - L) / (U - L)
```

For a 1-5 scale this is `(R - 1) / 4`; for a 0-100 positive-feedback percentage it is `R / 100`.

Shrink thin ratings toward the relevant marketplace/category prior:

```text
q = (n*r + k*mu) / (n + k)
```

where `mu` is the normalized marketplace/category mean and `k` is prior strength. Default `k = 20` when no empirical prior strength is available.

Apply diminishing returns to evidence volume:

```text
Vr = min(1, ln(n + 1) / ln(Nr + 1))
Vs = min(1, ln(s + 1) / ln(Ns + 1))
```

`Nr` and `Ns` are reference counts for the relevant marketplace/category. Prefer same-scope population P95 values. If those baselines are unavailable, read [CALIBRATION.md](CALIBRATION.md) before scoring.

Combine observed evidence dimensions only:

```text
E = weighted_mean(observed=[Vr, Vs], weights=[0.70, 0.30])
SellerScore = 100 * q * (0.58 + 0.42*E)
```

If a count is missing, omit that evidence dimension and renormalize the remaining weights. Do not convert missing to zero. If `n` is missing, do not pretend the displayed rating has known statistical support: use `q = mu` until a defensible review count is found.

Round `SellerScore` only for presentation, never during intermediate calculations.

**Complete when:** every retained listing has either a reproducible SellerScore from scope-compatible evidence or an explicit reason the score cannot be computed defensibly.

## 4. Make the purchase recommendation

Apply the signals in this order:

1. **Hard fit:** required model/specification, authenticity, condition, safety, compatibility, and user constraints.
2. **Item value:** total delivered price, condition, warranty/returns, included extras, and category-specific quality differences.
3. **Seller confidence:** SellerScore.

SellerScore may overturn a small value advantage between otherwise comparable listings; it must not rescue a materially worse, incompatible, suspicious, or overpriced item. Do not add SellerScore to arbitrary item-quality points unless a domain-specific utility model defines what those points mean.

When listing-level sales are available, use them as item-popularity evidence alongside item value; do not inject them into SellerScore as seller-wide sales.

**Complete when:** the recommended purchase traces to item fit/value plus seller confidence, and the explanation makes clear which factor changed the ranking.

## 5. Verify the ranking

Run these invariants before presenting the result:

- Holding everything else fixed, a higher rating cannot lower SellerScore.
- Holding everything else fixed, more reviews or sales cannot lower SellerScore.
- Additional volume has diminishing, not linear, impact.
- A tiny perfect-rating sample does not automatically outrank a slightly lower rating backed by substantial evidence.
- Item price, specification, and condition never alter SellerScore itself.
- Missing evidence is never treated as observed failure.

If any invariant fails, fix the inputs, scope alignment, normalization, or calculation before recommending.

**Complete when:** every invariant holds for the scored candidate set.

## Calibration boundary

The `0.58/0.42`, `0.70/0.30`, and `k=20` values are calibrated defaults, not universal learned optima. When historical marketplace outcomes exist, or platform fields/scales do not match the assumptions above, use [CALIBRATION.md](CALIBRATION.md) to estimate or override them rather than inventing precision.
