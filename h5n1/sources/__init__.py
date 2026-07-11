"""Raw data pulls — one module per source (usda, noaa, usgs, ...).

Each module fetches from the source, writes an immutable dated snapshot to the raw
GCS bucket, and records a manifest (url, checksum, capture_date). Nick's USDA module
is the reference implementation the others copy.
"""
