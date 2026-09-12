from typing import cast

import pytest

from clearinghouse.application.pagination import (
    CursorCodec,
    InvalidCursor,
    KeysetPage,
    validate_limit,
)


def test_cursor_round_trips_and_binds_every_query_dimension() -> None:
    codec = CursorCodec("a sufficiently long cursor secret")
    token = codec.encode(
        collection="offers",
        scope="acct_1",
        filters={"capability": "live", "model": None},
        key=("live", "", "2", "price_1"),
        snapshot="generation-1",
        stale=True,
    )
    decoded = codec.decode(
        token,
        collection="offers",
        scope="acct_1",
        filters={"capability": "live", "model": None},
    )
    assert decoded.key == ("live", "", "2", "price_1")
    assert decoded.snapshot == "generation-1"
    assert decoded.stale is True

    cases: tuple[tuple[str, str, dict[str, str | None]], ...] = (
        ("workloads", "acct_1", {"capability": "live", "model": None}),
        ("offers", "acct_2", {"capability": "live", "model": None}),
        ("offers", "acct_1", {"capability": None, "model": None}),
    )
    for collection, scope, filters in cases:
        with pytest.raises(InvalidCursor):
            codec.decode(token, collection=collection, scope=scope, filters=filters)


def test_cursor_rejects_tampering_and_limits_are_bounded() -> None:
    codec = CursorCodec("a sufficiently long cursor secret")
    token = codec.encode(collection="usage", scope="acct", filters={}, key=("time", "id"))
    with pytest.raises(InvalidCursor):
        codec.decode(
            token[:-1] + ("A" if token[-1] != "A" else "B"),
            collection="usage",
            scope="acct",
            filters={},
        )
    with pytest.raises(InvalidCursor):
        codec.decode("not-a-cursor", collection="usage", scope="acct", filters={})
    invalid_key = codec.encode(
        collection="usage", scope="acct", filters={}, key=cast(tuple[str, ...], (1,))
    )
    with pytest.raises(InvalidCursor, match="key"):
        codec.decode(invalid_key, collection="usage", scope="acct", filters={})
    invalid_snapshot = codec.encode(
        collection="usage",
        scope="acct",
        filters={},
        key=("time", "id"),
        snapshot=cast(str, 1),
    )
    with pytest.raises(InvalidCursor, match="snapshot"):
        codec.decode(invalid_snapshot, collection="usage", scope="acct", filters={})
    assert codec.decode(None, collection="usage", scope="acct", filters={}).key == ()
    assert validate_limit(1) == 1
    assert validate_limit(200) == 200
    with pytest.raises(ValueError):
        validate_limit(201)
    with pytest.raises(ValueError):
        validate_limit(0)
    with pytest.raises(ValueError, match="at least 16"):
        CursorCodec("short")


def test_keyset_page_retains_sequence_compatibility() -> None:
    page = KeysetPage((1, 2), ("2",))
    assert len(page) == 2
    assert page[0] == 1
    assert page[:1] == (1,)
    assert page == [1, 2]
    assert page == KeysetPage((1, 2), ("2",))
    assert page != KeysetPage((1, 2), None)
    assert page != object()
