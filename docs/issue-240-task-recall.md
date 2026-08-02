# Issue #240 task-recall corpus

`fixtures/delivery/v1/issue240-task-recall.json` is a versioned frozen source
task fixture covering four current Fast delivery paths. The benchmark runs the
real `DeliveryEngine` in standalone bootstrap mode, deduplicates selected
spans by file, and records cold/warm wall time, p95 and recall/precision for
the expected files.

The receipt intentionally reports `status=partial`: this local fixture is not
presented as historical user traffic, downstream consumer success, or an
installed cross-platform result. Those gates remain explicitly unverified.
