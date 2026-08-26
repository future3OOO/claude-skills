# Calibration

Read this file when population baselines are unavailable, seller fields use unusual scopes/scales, or historical outcomes can replace the runtime defaults owned by `SKILL.md`.

## Choose `mu`, `Nr`, and `Ns`

Use the narrowest defensible reference population that still has enough sellers to be stable: same platform, category, geography, seller type, and time horizon where those materially change the distributions.

### `mu`: prior rating quality

Preference order:

1. empirical marketplace/category mean on the same normalized rating scale;
2. a broader same-platform mean;
3. for a 1-5 scale only, normalized `4.0/5` (`mu = 0.75`) as a conservative fallback.

Do not transplant the `4.0/5` fallback to percentage-positive systems or other compressed scales. If no defensible prior exists there, report the missing calibration instead of fabricating one.

### `Nr`, `Ns`: evidence reference counts

Preference order:

1. P95 review and seller-sales counts from a same-scope marketplace/category population;
2. P95 from a broad, representative search sample;
3. P95 from the current candidate set, with a floor of `100` for each reference count.

The floor prevents a weak candidate set from making, for example, 12 reviews look like maximum-strength evidence merely because every retrieved seller is new.

Keep review and sales reference populations scope-compatible with the fields being scored. Lifetime seller feedback cannot be normalized against 30-day item sales.

## Why logarithms

Volume is evidence with diminishing returns. The difference between 2 and 20 observations should matter far more than the difference between 10,002 and 10,020. `ln(count + 1)` preserves order while compressing scale, then the reference count maps that evidence to `[0,1]`.

Do not rank directly on raw review or sales counts.

## Research basis for quality versus evidence

Qiu and Zhang's 2024 meta-analysis covered 156 studies, 214 effect sizes, and 69,006 observations. It reports combined correlations with purchase intention of:

- review rating: `r = 0.443`
- review volume: `r = 0.317`

Normalizing those two magnitudes gives approximately `0.583` and `0.417`. That motivates the rounded quality/evidence split defined in `SKILL.md`. It does **not** prove those values are optimal ranking coefficients: the study aggregates correlations across heterogeneous contexts, not coefficients from this seller-ranking model.

Source: Keda Qiu & Liyi Zhang, *How online reviews affect purchase intention: A meta-analysis across contextual and cultural factors*, Data and Information Management 8(2), 2024, DOI `10.1016/j.dim.2023.100058`.

## Why review evidence outweighs sales evidence

The review-heavy evidence split defined in `SKILL.md` is a design prior, not a published causal estimate.

Review count directly supports the displayed rating and has independent empirical evidence as a purchase-intention cue. Seller sales add useful experience/popularity evidence but are often correlated with review count, so giving sales equal influence would double-count scale more aggressively.

eBay's buyer guidance explicitly tells buyers to consider seller rating, feedback score/count, and number of items sold when judging seller reputation. Its Top Rated program also requires an established sales history, including at least 100 transactions over the relevant 12-month period on ebay.com. These are supporting signals that transaction volume matters, not evidence for any universal sales coefficient.

Sources:

- https://www.ebay.com/help/buying/resolving-issues-sellers/seller-ratings?id=4023
- https://www.ebay.com/help/policies/seller-performance/seller-performance-standards?id=4347

## Prior strength

The fallback prior strength is owned by `SKILL.md`. It controls how quickly a thin observed rating escapes shrinkage toward `mu`; it is a pragmatic default, not a research-derived optimum.

If enough marketplace data exists, replace it with an empirical-Bayes prior strength estimated from between-seller variation and within-seller review uncertainty. Keep the prior category/platform-specific when rating distributions differ materially.

## Learn the coefficients when outcomes exist

If historical recommendation outcomes are available, stop treating the defaults as optimal. Fit the ranking against the outcome that actually defines a good purchase, for example:

- completed purchase/conversion;
- return/refund or dispute rate;
- item-not-as-described rate;
- delivery failure;
- post-purchase satisfaction;
- a composite expected-loss or utility target.

Keep the raw features separate during fitting:

```text
q   = Bayesian-adjusted rating quality
Vr  = log-normalized review evidence
Vs  = log-normalized sales evidence
```

Include item-level features separately from seller features. Evaluate on held-out future periods or sellers, not the same observations used to fit. Prefer a simpler model when its out-of-sample ranking quality is indistinguishable from a more complex one.

Replace runtime coefficients only when the learned model improves the chosen held-out ranking metric or expected utility and remains directionally sane: better rating quality, more supporting reviews, and more seller transactions must not become penalties without a separately identified adverse signal.

## Scope and anomaly checks

Do not score until these are resolved or explicitly marked:

- rating is product-level while reviews are seller-level;
- sales count is listing-level while rating is seller-level;
- review count covers a different time horizon from the displayed rating;
- a marketplace reports positive-feedback percentage rather than mean stars;
- counts appear duplicated, rounded, abbreviated, or capped;
- review count exceeds seller sales because the platform fields refer to different scopes;
- platform badges encode richer defect/return information that should be reported separately rather than reverse-engineered into the formula.

The cure for incompatible evidence is not another coefficient. Keep the signals separate.

## Sanity cases

After calibration, test at least these orderings:

1. Same rating: more observed reviews/sales must not reduce SellerScore.
2. Same evidence: higher rating must increase SellerScore.
3. Very small sample: a nominally perfect rating should be pulled materially toward `mu`.
4. Large sample: Bayesian shrinkage should become negligible.
5. Huge volume: another equal absolute increase should matter less than it did at low volume.
6. Missing field: removing an unobserved metric must not silently turn it into a zero-performance observation.

If the fitted or configured model violates one of these without a deliberate, evidence-backed reason, reject the calibration.
