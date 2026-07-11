# manifests/

One JSON manifest per raw snapshot: source URL, checksum (sha256), capture date,
row count, and the GCS path. This is the frozen-snapshot ledger that makes the
"reproducible framework" claim literally true — anyone can see exactly what data
produced the results. Manifests ARE committed (small, no PII); the data they point
to is not.
