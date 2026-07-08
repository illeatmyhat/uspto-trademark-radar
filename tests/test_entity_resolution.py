"""Multi-signal operation keys: address preferred, canonical name fallback.
All names/addresses are fictional test data."""

from uspto_trademark_radar.gold.entity_resolution import (
    address_key,
    operation_key,
)


def test_address_key_invariant_to_case_and_punctuation():
    a = address_key("1 Example Road, Suite 200", "Exampleville, CA 90000")
    b = address_key("1 EXAMPLE ROAD SUITE 200", "EXAMPLEVILLE CA 90000")
    assert a == b == "1EXAMPLEROADSUITE200EXAMPLEVILLECA90000"


def test_address_key_none_when_unusable():
    assert address_key(None, None) is None
    assert address_key("", "  --  ") is None


def test_operation_key_prefers_address():
    key = operation_key("Dana K. Rivers", "1 Example Road", None, None)
    assert key == "ADDR:1EXAMPLEROAD"


def test_operation_key_falls_back_to_canonical_name():
    a = operation_key("Dana K. Rivers", None, None, None)
    b = operation_key("DANA RIVERS, ESQ.", "", None, None)
    assert a == b == "NAME:DANA RIVERS"


def test_operation_key_none_when_no_signal():
    assert operation_key(None, None, None, None) is None
    assert operation_key("QO2281", None, None, None) is None  # docket noise


def test_signal_namespaces_cannot_collide():
    by_addr = operation_key(None, "DANA RIVERS")   # address happens to be a name
    by_name = operation_key("Dana Rivers")
    assert by_addr != by_name
