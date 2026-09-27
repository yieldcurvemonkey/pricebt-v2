"""Test-support market-data providers (spec D6): TEST CODE, never shipped.

Every provider reads `data/fixtures` (pyarrow / pandas / numpy only) and emits `pricebt.snapshot` plain-data snapshots inside a
`SnapshotPricer`; none imports a pricing library. Real-data configs name them by dotted path under `registry.allow: [support]`, e.g.
`type: "support.curves:CurveStore"`.

    support.common       fixture paths, calendars and fixings as data, the publication policy, the time-mapping selector
    support.curves       CurveStore     curve-store partitions -> CurveSnapshot (+ fixings, calendar)
    support.ust_common   the on-the-run universe, the reference table, the quote-panel base class
    support.ust_eod      UstEod         end-of-day clean-price panel -> QuoteSet
    support.ust_minute   UstMinute      minute yield panel -> QuoteSet
    support.known        the known answers of the fixtures, shared by the tests
    support.tiny         builders of tiny fixture trees (tmp dirs) for rule tests with known answers
"""
