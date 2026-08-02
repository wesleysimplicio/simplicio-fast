# Issue #344 Knowledge quality corpus

`fixtures/knowledge/v1/issue344-quality.json` is a versioned fixture for the
bounded Knowledge projection. It includes active, revoked, expired and
conflicting facts, plus two labeled queries. The benchmark records precision,
recall, nDCG and the explain fields returned by the projection.

The receipt is deliberately `partial`: it measures the Python lexical
fallback on a frozen fixture and does not claim real-corpus quality, vector
ranking quality, Rust parity or installed consumer E2E.
