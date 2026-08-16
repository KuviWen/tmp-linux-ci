# Additional zero-cost official Taiwan market-data sources

Research date: 2026-08-16

## Decision summary

Additional official, zero-fee sources can improve listing-lifecycle evidence, security-master checks, trading-calendar coverage, halt/resume handling, and price-quality checks. They do **not** provide the missing qualified historical price supply.

The strongest additions are:

- TWSE dataset 11543, `終止上市公司`, is a real historical delisting distribution rather than a current-only row. Its live official payload contains delistings back to at least 2001, including `2448` on 2021-01-06. It has no completeness or revision-history attestation, so it is useful lifecycle evidence but not a complete listing-history authority ([dataset metadata](https://data.gov.tw/dataset/11543), [official API payload](https://openapi.twse.com.tw/v1/company/suspendListingCsvAndHtml)).
- TWSE dataset 11542, `最近上市公司`, contains modern listing dates and explicit predecessor/successor notes for some holding-company transitions. Its retained older rows have increasingly incomplete date fields, so it is a partial historical supplement, not a complete lifecycle archive ([dataset metadata](https://data.gov.tw/dataset/11542), [official API payload](https://openapi.twse.com.tw/v1/company/newlisting)).
- TWSE datasets 11677 and 11761 can support prospective halt/resume and annual calendar ingestion. Neither promises immutable historical backfill ([11677](https://data.gov.tw/dataset/11677), [11761](https://data.gov.tw/dataset/11761)).
- TDCC datasets 11425 and 11449 provide broad current security/status and share-registry-unit snapshots, including terminal securities and some termination dates. They are not time-bounded name/code histories ([11425](https://data.gov.tw/dataset/11425), [11449](https://data.gov.tw/dataset/11449)).
- TDCC dataset 11454 provides the most recent two years of security delivery/allotment event dates and reasons. It is useful corporate-action corroboration, but lacks ratios, cash amounts, and adjustment factors ([11454](https://data.gov.tw/dataset/11454)).
- Ministry of Economic Affairs dataset 6048 preserves monthly company-change registration files from February 2013. It can date issuer-name observations after mapping a business number to a listing, but does not identify the changed field or prior name ([6048](https://data.gov.tw/dataset/6048)).
- TWSE datasets 11548 and 11551 are useful price consistency checks only. One is a daily close/month-average snapshot and the other is an annual aggregate; neither can reconstruct daily OHLCV history ([11548](https://data.gov.tw/dataset/11548), [11551](https://data.gov.tw/dataset/11551)).

**No source below fills the required seven-or-more-year XTAI per-listing, unadjusted daily OHLCV history together with immutable revisions/corrections, complete corporate actions, and complete effective-dated name/code lifecycle.** Historical Taiwan price qualification must therefore remain fail-closed. These sources may narrow non-price gaps or support future prospective accumulation, but cannot turn a mutable current distribution into qualified historical evidence.

## Eligibility boundary

This research applies [ADR 0018](../adr/0018-zero-cost-authenticated-source-credentials.md): an official source may still qualify when a zero-fee self-service account, API key, or click-through is required, provided it requires no payment method, paid trial, sales contact, manual approval, procurement, or negotiated contract and its dataset-specific terms authorize the intended use. Credential state is recorded separately from source-use qualification.

All recommended candidates found in this scan are anonymous (`credential_required: false`). No official self-service credentialed plan was found that changes the historical-price conclusion.

Government Open Data License 1.0 permits use for any purpose and time, reproduction, distribution, public transmission, editing, adaptation, and derivative works without royalty or separate written authorization, subject principally to attribution. A provider may stop supplying future data, which is why immutable receipt storage and backups remain necessary ([official license](https://data.gov.tw/license)).

TWSE's general website terms restrict automated extraction unless approved, but expressly carve out data authorized by TWSE through the Government Open Data Platform. Eligibility therefore attaches to each listed open-data distribution, not to arbitrary TWSE pages or the entire domain ([TWSE terms](https://www.twse.com.tw/zh/terms/use.html)). The TWSE OpenAPI exposes the same named routes without credentials ([official OpenAPI](https://openapi.twse.com.tw/)).

### Verdict vocabulary

- `eligible_historical_partial`: an official licensed distribution exposes actual embedded historical events, but not the complete ticket contract.
- `eligible_prospective`: an official licensed current/event distribution may be accumulated from observation time forward.
- `cross_check_only`: useful for validation, but structurally unable to supply required records.
- `not_eligible`: rights, cost, distribution, or evidence requirements fail.

## Eligibility matrix

| Dataset | Official distribution | What is actually exposed | History / cadence | `credential_required` | Rights and automation | Verdict |
|---|---|---|---|---:|---|---|
| TWSE 11543 `終止上市公司` | [metadata](https://data.gov.tw/dataset/11543); [CSV](https://www.twse.com.tw/company/suspendListingCsvAndHtml?type=open_data); [OpenAPI](https://openapi.twse.com.tw/v1/company/suspendListingCsvAndHtml) | Delisting date, company name, listing code | Live payload contains genuine historical events back to at least 2001; irregular update. It is still a mutable latest distribution with no published correction versions or completeness promise. | false | Free, OGDL 1.0; the dataset-specific open-data route is automation-eligible | `eligible_historical_partial` for delisting lifecycle |
| TWSE 11542 `最近上市公司` | [metadata](https://data.gov.tw/dataset/11542); [CSV](https://www.twse.com.tw/company/newlisting?response=open_data); [OpenAPI](https://openapi.twse.com.tw/v1/company/newlisting) | Code, company, application/approval/listing dates, note | Current payload retains many modern historical rows and transition notes, including predecessor codes, but older rows have blank date fields and no completeness/revision guarantee. | false | Free, OGDL 1.0; dataset-specific open-data route | `eligible_historical_partial` for listing/transition evidence, not a complete lifecycle source |
| TWSE 11677 `暫停交易證券` | [metadata](https://data.gov.tw/dataset/11677); [CSV](https://www.twse.com.tw/exchangeReport/TWTAWU?response=open_data); [OpenAPI](https://openapi.twse.com.tw/v1/exchangeReport/TWTAWU) | Code/name plus suspension and resumption date/time | Irregular event/current list; metadata does not promise full historical retention or revision versions. Treat only observations received after adapter activation as durable evidence. | false | Free, OGDL 1.0; dataset-specific open-data route | `eligible_prospective` for halt/resume events |
| TWSE 11761 `集中交易市場開（休）市日期` | [metadata](https://data.gov.tw/dataset/11761); [CSV](https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=open_data); [OpenAPI](https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule) | Calendar date, weekday, holiday/session description | Metadata describes latest-version annual publication, normally for the following year before December. The live API is a single-year schedule, not a historical calendar archive. | false | Free, OGDL 1.0; dataset-specific open-data route | `eligible_prospective` for annual calendar snapshots |
| TDCC 11425 `證券基本資料` | [metadata](https://data.gov.tw/dataset/11425); [OpenAPI](https://openapi.tdcc.com.tw/v1/opendata/1-1); [API documentation](https://openapi.tdcc.com.tw/tdcc-opendata-api-docs) | Code, name, market, status, currency, par value, transfer agent, update date | Broad daily current master. The current feed retains terminal securities, but does not publish effective-dated name/code changes or revision history. | false | Free, OGDL 1.0; anonymous official endpoint | `eligible_prospective` security-master/status cross-check |
| TDCC 11449 `公司股務單位資料` | [metadata](https://data.gov.tw/dataset/11449); [CSV](https://opendata.tdcc.com.tw/getOD.ashx?id=1-2); [OpenAPI](https://openapi.tdcc.com.tw/v1/opendata/1-2) | Date, code/name, market, registry-unit details, note/termination date | Daily current snapshot; useful termination supplement, not an effective-dated listing/name/code archive. | false | Free, OGDL 1.0; anonymous official endpoint | `eligible_prospective` lifecycle supplement |
| TDCC 11454 `有價證券帳簿劃撥配發／交付日期` | [metadata](https://data.gov.tw/dataset/11454); [CSV](https://opendata.tdcc.com.tw/getOD.ashx?id=1-7); [OpenAPI](https://openapi.tdcc.com.tw/v1/opendata/1-7) | Security code/name, delivery date, delivery reason | Explicitly limited to the most recent two years and updated daily. It records event dates/reasons, not ratios, cash amounts, or adjustment factors. | false | Free, OGDL 1.0; anonymous official endpoint | `eligible_historical_partial` for recent action corroboration and `eligible_prospective` thereafter |
| MOEA 6048 `公司變更登記清冊（月）` | [metadata and monthly distributions](https://data.gov.tw/dataset/6048) | Business number, company name, address, representative, capital, approval date | Separate monthly CSV distributions from February 2013 onward. Rows show that a registration changed, but not the changed field or prior value. | false | Free, OGDL 1.0; official monthly distributions | `eligible_historical_partial` for issuer-level dated observations after an independently verified issuer/listing mapping |
| TWSE 11548 `上市個股日收盤價及月平均價` | [metadata](https://data.gov.tw/dataset/11548); [CSV](https://www.twse.com.tw/exchangeReport/STOCK_DAY_AVG_ALL?response=open_data); [OpenAPI](https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL) | Date, code/name, close, month-average price | Daily current snapshot. It does not expose open/high/low/volume, backfill, or revisions. | false | Free, OGDL 1.0; dataset-specific open-data route | `cross_check_only` against the current close decoder |
| TWSE 11551 `上市個股年成交資訊` | [metadata](https://data.gov.tw/dataset/11551); [CSV](https://www.twse.com.tw/exchangeReport/FMNPTK_ALL?response=open_data); [OpenAPI](https://openapi.twse.com.tw/v1/exchangeReport/FMNPTK_ALL) | Annual volume, value, trades, high/date, low/date, average close by security | Annual aggregate. Aggregation destroys the daily sequence and cannot reconstruct daily OHLCV or corrections. | false | Free, OGDL 1.0; dataset-specific open-data route | `cross_check_only` for year-level sanity checks |

## Candidate details and adapter recommendations

### 1. Add a TWSE delisting-lifecycle adapter for dataset 11543

This is the strongest newly identified historical distribution. The official dataset describes the exact three fields and identifies the source as TWSE, free under OGDL, with irregular updates ([metadata](https://data.gov.tw/dataset/11543)). The current official payload has 264 rows and reaches ROC year 90 (2001); it includes `2448 / 晶電 / 110-01-06`, providing direct evidence for a Ticket 06 listing-code transition case ([live payload](https://openapi.twse.com.tw/v1/company/suspendListingCsvAndHtml)).

Recommended adapter behavior:

- ingest the full payload as an immutable receipt, retaining retrieval time, URL, response hash, and OGDL attribution;
- parse ROC dates without treating the mutable endpoint as an immutable version selector;
- emit delisting events keyed through the canonical instrument/listing identity seam, never using ticker as permanent identity;
- regard absence from a later response as a source conflict, not proof an earlier event never existed;
- record source revisions by comparing immutable receipts; do not invent a publisher revision identifier.

It cannot establish listing start, every company-name interval, every code transition, or corrected historical versions. Its adapter should therefore contribute evidence to lifecycle resolution without becoming the sole lifecycle authority.

### 2. Extend the TWSE listing adapter with dataset 11542 transition notes

Dataset 11542 is more useful than its title suggests. Its current official payload includes explicit notes such as `聯嘉光電(6288)於當日下市，轉投控上市` for successor `3717`; the same payload carries listing dates and other predecessor/successor notes ([live payload](https://openapi.twse.com.tw/v1/company/newlisting)). This can supply direct official evidence for some cross-code transitions.

The feed is nevertheless incomplete as a historical ledger: older retained entries increasingly omit the listing-date fields, it has no full-history or completeness guarantee, and correction versions are not exposed. Ingest it as a partial event source with immutable receipts. Never infer that an unmentioned transition did not occur.

### 3. Add prospective trading-state adapters for 11677 and 11761

Dataset 11677 publishes suspension and resumption timestamps, while 11761 publishes the exchange holiday schedule ([11677 metadata](https://data.gov.tw/dataset/11677), [11761 metadata](https://data.gov.tw/dataset/11761)). Together they improve the distinction between an expected non-trading day, a security-specific halt, and a missing price.

They should be operated prospectively:

- fetch the holiday schedule at least when a new annual version appears and retain every received version;
- fetch the suspension/resumption feed on a low-concurrency schedule appropriate to its irregular update pattern;
- make “not observed before adapter activation” explicit instead of backfilling absence;
- require separate official evidence for historical half-day session length. Dataset 11761's holiday descriptions are not a complete historical session-hours archive.

### 4. Add TDCC security-master and termination cross-checks

TDCC dataset 11425 is a large daily master that includes security status and update date; its live payload retains securities whose market/status is terminal. Dataset 11449 adds registry-unit data and a termination-date note field ([11425 metadata](https://data.gov.tw/dataset/11425), [11449 metadata](https://data.gov.tw/dataset/11449), [TDCC API documentation](https://openapi.tdcc.com.tw/tdcc-opendata-api-docs)).

Recommended use is a once-daily, conditional-fetch current-state adapter with immutable receipts. It may confirm that a code is terminal or currently active and flag conflicts with TWSE lifecycle data. It cannot create a name-history interval merely because today's security name differs from a prior receipt: the interval boundary must come from an observed receipt time or a dated official event.

Because 11425 is a large payload, use streaming parsing, response-size limits, timeouts, and low frequency. The dataset publishes no fixed quota, so absence of a stated limit is not permission for aggressive polling.

### 5. Keep 11548 and 11551 out of the historical price authority

Dataset 11548 can compare a current official close with the current all-securities OHLCV feed already evaluated in the prior research. Dataset 11551 can compare annual volume/value/high/low aggregates with computed yearly summaries ([11548 metadata](https://data.gov.tw/dataset/11548), [11551 metadata](https://data.gov.tw/dataset/11551)).

Both checks may detect decoder or ingestion errors. Neither is independent provenance for historical daily bars, neither yields historical daily rows, and neither exposes correction/revision events. They must not be used to mark seven-year price coverage as qualified.

### 6. Use 11454 and 6048 only as bounded corroborating evidence

TDCC dataset 11454 explicitly covers the most recent two years of book-entry allotment/delivery events and publishes a security code, name, delivery date, and reason ([11454 metadata](https://data.gov.tw/dataset/11454)). It can corroborate that a recent issuance, capital reduction/conversion, allotment, or initial listing delivery occurred. Because it omits the economic terms needed to calculate an adjustment factor, it cannot replace the normalized corporate-action ledger.

The Ministry of Economic Affairs publishes separate monthly company-change registration distributions from February 2013 onward, keyed by business number and carrying the observed company name and approval date ([6048 metadata](https://data.gov.tw/dataset/6048)). These files are unusually useful immutable-by-distribution historical issuer evidence. They are not listing records, however: use requires an independently verified business-number-to-issuer/listing mapping, and a row does not identify whether the name or another company field changed. The source can corroborate dated names but cannot alone mint a complete name transition.

## Coverage against the Ticket 06 gaps

| Required evidence | Best additional source | What it improves | Remaining failure |
|---|---|---|---|
| Seven-plus years of per-listing unadjusted daily OHLCV | None | 11548 checks current close; 11551 checks annual aggregates | No official zero-fee open distribution supplies the daily backfill |
| Listing/name/code lifecycle | 11543 + 11542 + TDCC 11425/11449 + MOEA 6048 | Historical delisting dates, some listing/transition notes, current/terminal status, and dated issuer-name observations | No complete effective-dated listing-name history, code-transition graph, prior-name field, or completeness attestation |
| Trading calendar and half-day sessions | 11761 | Prospective annual holiday/non-trading schedule | Latest annual version only; no complete historical session-hours/half-day archive |
| Security halts/resumptions | 11677 | Prospective official halt/resume observations | No committed immutable historical backfill or revision ledger |
| Corporate actions | TDCC 11454 + 11542 notes | Recent delivery/allotment event dates and a few reorganizations/holding-company transitions | Only two years for 11454; no complete split, dividend, capital-reduction, merger, rights, economic terms, or adjustment-factor ledger |
| Corrections/revisions | Immutable receipts built by this project | Detects change between observations | Publisher supplies no immutable version selector or complete correction history |

## Explicit disqualifications and unresolved unknowns

### TWSE interactive historical-price query

The TWSE historical stock-day page exposes an interactive date/security query and states availability from 2010-01-04 ([official page](https://www.twse.com.tw/zh/trading/historical/stock-day.html)). However, that parameterized history route is not the dataset-specific Government Open Data Platform distribution evaluated above. TWSE's general terms prohibit automated extraction outside the open-data carve-out ([terms](https://www.twse.com.tw/zh/terms/use.html)). It is therefore **not eligible** for automated historical backfill unless TWSE publishes it as an open-data distribution or provides qualifying written terms that do not require negotiated approval.

The Government Data Platform contains a public request noting that the then-open stock datasets returned only the latest/current prices and asking for date/code historical lookup; this corroborates the distinction between open current snapshots and interactive history, but is not itself a data supply ([official suggestion 136936](https://data.gov.tw/suggests/136936)).

### TWSE Data E-Shop historical daily product

TWSE's daily closing-price product has historical depth and daily OHLCV, but its official product page lists monthly fees and contractual usage categories ([official product](https://eshop.twse.com.tw/zh/product/detail/cfec9a1470e448ec91bfde006db361e8)). It is excluded by the project's zero-cost boundary.

### MOPS/FSC open data

MOPS daily material-event data can be an official prospective signal for reorganizations, corporate-action announcements, or corrections, but it is a daily/current disclosure feed rather than a complete normalized corporate-action ledger ([TWSE/MOPS dataset 18415](https://data.gov.tw/dataset/18415), [official distribution](https://openapi.twse.com.tw/v1/opendata/t187ap04_L)). FSC dataset 143016 covers only a subset of stock issuances and is not a complete action history ([official metadata](https://data.gov.tw/dataset/143016), [official export](https://stat.fsc.gov.tw/api/v1/public/datasets/143016/export)). Both are optional prospective signal adapters, not substitutes for the missing historical contract.

### Non-official aggregators

An aggregator may technically expose Taiwan daily prices, dividends, delistings, capital reductions, or splits, but that does not prove a dataset-specific right to ingest and retain the original exchange history. In particular, FinMind documents a free API and machine-learning use, while its disclaimer describes aggregation from public/open channels rather than providing immutable first-party exchange distributions and revision attestations for every dataset ([API overview](https://finmind.github.io/en/), [data-source disclaimer](https://finmind.github.io/Disclaimer/)). It may be used for investigation or QA only, not as a formal qualifying source under the official-first policy.

Yahoo Finance and scraped public pages similarly lack first-party dataset provenance and qualifying dataset-specific retention/transform terms. They are `not_eligible` for formal ingestion.

### Operational unknowns

No fixed TWSE or TDCC quota was found in the official dataset metadata reviewed. This is an **unknown**, not evidence of unlimited automation. Adapters should use low concurrency, dataset cadence-aligned polling, exponential backoff, `Retry-After` when present, conditional requests where supported, response-size/time limits, and circuit breaking. A persistent `429` or access-policy response must fail closed and trigger operator review.

No candidate publishes a durable revision identifier, immutable historical object address, correction ledger, or completeness checksum. The platform may retain its own immutable receipts and observation history, but must label those as platform-observed evidence rather than publisher-certified history.

## Recommended integration order

1. **11543 delistings** — highest value because it contains genuine official historical lifecycle events, including the required modern transition cases.
2. **11761 calendar and 11677 halts** — start prospective accumulation early; these improve missing-bar classification.
3. **TDCC 11425 and 11449** — add current security/status and termination cross-checks with careful handling of the large daily feed.
4. **11454 delivery events and MOEA 6048 registrations** — add bounded recent-action and issuer-name corroboration without promoting either to listing or adjustment authority.
5. **11542 transition notes** — enrich listing-code transitions without treating the incomplete old rows as negative evidence.
6. **11548 and 11551 checks** — use only as ingestion-quality assertions, never as historical price qualification.

This order improves the official evidence available to the system at zero monetary cost while preserving the Ticket 06 fail-closed boundary: the required seven-year Taiwan daily price dataset remains unavailable from a qualified official zero-fee distribution.
