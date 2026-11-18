"""Search text is redacted from logged URLs; everything else is kept."""

import pytest

from app.common import redaction


@pytest.mark.parametrize(
    ("url", "redacted"),
    [
        (
            "http://search/v1/search?q=Jo+Bloggs&limit=5",
            "http://search/v1/search?q=[redacted]&limit=5",
        ),
        (
            "/v1/search?title=AB123456C&offset=10",
            "/v1/search?title=[redacted]&offset=10",
        ),
        (
            "/v1/search?q=a&q=b&group=document",
            "/v1/search?q=[redacted]&q=[redacted]&group=document",
        ),
        ("/v1/search?q=", "/v1/search?q=[redacted]"),
        ("/v1/search?tag=0199b8a2&limit=5", "/v1/search?tag=0199b8a2&limit=5"),
        ("http://search/health", "http://search/health"),
    ],
)
def test_redacts_only_the_search_text(url, redacted):
    assert redaction.redact_query(url) == redacted
