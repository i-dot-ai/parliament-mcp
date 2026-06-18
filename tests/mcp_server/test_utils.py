import pytest

from parliament_mcp.mcp_server.utils import extract_party_info, gather_sections


async def _ok(value):
    return value


async def _boom():
    raise RuntimeError


@pytest.mark.asyncio
async def test_gather_sections_keeps_successes_and_drops_failures():
    result = await gather_sections({"a": _ok(1), "bad": _boom(), "b": _ok(2)})
    assert result == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_gather_sections_keeps_falsy_results():
    # An empty section is a real result, not a failure, so it must be kept.
    result = await gather_sections({"empty": _ok([]), "bad": _boom()})
    assert result == {"empty": []}


def test_extract_party_info_skips_vacant_and_partyless_holders():
    posts = [
        {"postHolders": []},  # vacant post
        {"postHolders": [{"member": {}}]},  # holder without a party
        {"postHolders": [{"member": {"latestParty": {"name": "Labour"}}}]},
    ]
    assert extract_party_info(posts) == {"name": "Labour"}


def test_extract_party_info_returns_none_when_no_party_present():
    assert extract_party_info([{"postHolders": [{}]}]) is None
    assert extract_party_info([]) is None
