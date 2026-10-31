"""California-relevance gate in the article-extract path + bbox backstop."""

from toast.core.envelope import Envelope, insert_envelope
from toast.pipeline.analyze import _outside_california, extract_article


class FakeLLM:
    def __init__(self, response):
        self.response = response

    def chat_json(self, messages, **kw):
        return self.response


def _article_env(conn):
    return insert_envelope(
        conn,
        Envelope(source_id="s1", external_id="a1", kind="article", payload={"url": "u"}),
    )


def test_non_ca_article_writes_zero_facts(seeded):
    env_id = _article_env(seeded)
    fake = FakeLLM(
        {
            "california_relevant": False,
            "primary_location": "Spain",
            "claims": [
                {
                    "content": "Spain fire 40000 acres",
                    "kind": "acreage",
                    "acreage": 40000,
                    "confidence": 0.8,
                }
            ],
        }
    )
    summary = extract_article(
        seeded,
        url="u",
        source_id="s1",
        tier=4,
        envelope_id=env_id,
        raw_ref=None,
        text="...",
        client=fake,
    )
    assert summary["california_relevant"] is False
    assert summary["skipped"] == "not_california"
    assert summary["facts"] == []
    assert seeded.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"] == 0


def test_ca_article_writes_facts(seeded):
    env_id = _article_env(seeded)
    fake = FakeLLM(
        {
            "california_relevant": True,
            "primary_location": "Butte County, California",
            "claims": [
                {
                    "content": "Flume Fire 1200 acres",
                    "kind": "acreage",
                    "acreage": 1200,
                    "confidence": 0.9,
                }
            ],
        }
    )
    summary = extract_article(
        seeded,
        url="u",
        source_id="s1",
        tier=4,
        envelope_id=env_id,
        raw_ref=None,
        text="The Flume Fire in Butte County has burned 1,200 acres.",
        client=fake,
    )
    assert len(summary["facts"]) == 1
    assert seeded.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"] == 1


def test_bbox_backstop():
    assert _outside_california(39.76, -121.6) is False  # Paradise, CA
    assert _outside_california(40.4, -3.7) is True  # Madrid, Spain
    assert _outside_california(45.5, -122.6) is True  # Portland, OR
    assert _outside_california(None, None) is False  # no coords → not disproven
