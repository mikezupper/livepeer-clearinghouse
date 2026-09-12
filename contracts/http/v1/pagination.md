# Cursor pagination contract

Collection endpoints return at most 50 items by default and accept `limit` from
1 through 200. A response has this stable envelope:

```json
{"items": [], "next_cursor": null}
```

When `next_cursor` is non-null, pass it unchanged as the next request's
`cursor` query parameter. Clients must treat the cursor as opaque. Cursors are
versioned and HMAC authenticated, and bind the collection name, authenticated
account or administrator scope, filters, sort order, and—on `/v1/offers`—the
discovery snapshot generation. Reusing a cursor with different filters or an
account returns `400`. Continuing an offer cursor after discovery refreshes
returns `409` with `detail.code` set to `stale_cursor`; restart without a
cursor.

The stable keyset orders are:

| Collection | Order |
| --- | --- |
| Offers | capability, model, price numerator, offer ID ascending |
| Credentials | creation time, credential ID descending |
| Workloads and costs | creation time, workload ID descending |
| Usage | occurrence time, usage event ID descending |
| Administration users | normalized email, user ID ascending |

The implementation fetches `limit + 1` rows to establish continuation and
never uses SQL `OFFSET`. Lists intentionally omit total counts. Dashboard
metrics come from `/v1/summary` and `/v1/admin/overview`, whose values are
computed with database `COUNT` and `SUM` aggregates rather than hidden list
fetches.

`/v1/discovery` is the deliberate exception: it retains the complete array
shape required by `livepeer-python-gateway`. Its internal SQLite reads are
still performed in bounded keyset batches.
