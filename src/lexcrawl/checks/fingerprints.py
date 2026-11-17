"""### Deterministic source fingerprints (the fingerprint service)."""

from __future__ import annotations

import hashlib


def hash_url_set(urls: list[str]) -> str:
    """### Hash an order-independent set of URLs."""
    return hashlib.sha256("\n".join(sorted(urls)).encode("utf-8")).hexdigest()


def uae_fingerprint(urls: list[str]) -> str:
    """### Fingerprint the UAE listing's current article set."""
    return hash_url_set(urls)


def cba_fingerprint(article_count: int, newest_lastmod: str) -> str:
    """### Fingerprint the CBA sitemap's article count and newest modification."""
    return f"{article_count}:{newest_lastmod}"


def commerce_fingerprint(head_identity: str, total: int | None) -> str:
    """### Fingerprint the Commerce listing's newest report and total count."""
    return f"{head_identity}:{total}"
