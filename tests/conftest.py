"""Fixture builders: tiny v2.0-shaped XML case files zipped like the real
TRTYRAP/TRTDXFAP products.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from uspto_trademark_radar.config import Config
from uspto_trademark_radar.ledger import Ledger

_WRAPPER = """<?xml version="1.0" encoding="UTF-8"?>
<trademark-applications-daily>
<version><version-no>{version}</version-no><version-date>20061020</version-date></version>
<creation-datetime>20250101020202</creation-datetime>
<application-information>
<file-segments>
<file-segment>TRMK</file-segment>
<action-keys>
<action-key>Curator</action-key>
{case_files}
</action-keys>
</file-segments>
</application-information>
</trademark-applications-daily>
"""


def make_case_file(serial: str, mark: str = "VOFULY",
                   filing: str = "20200105",
                   owner_name: str = "Example Widget Trading Co., Ltd.",
                   owner_country: str = "CN",
                   extra_owner: str | None = None,
                   statement: str = "Cell phone cases",
                   header_extra: str = "",
                   extra_events: str = "") -> str:
    owners = f"""
<case-file-owner>
<entry-number>1</entry-number>
<party-type>10</party-type>
<nationality><country>{owner_country}</country></nationality>
<legal-entity-type-code>03</legal-entity-type-code>
<party-name>{owner_name}</party-name>
<address-1>101 Some Rd</address-1>
<city>Exampleville</city>
<country>{owner_country}</country>
<postcode>518000</postcode>
</case-file-owner>"""
    if extra_owner:
        owners += f"""
<case-file-owner>
<entry-number>1</entry-number>
<party-type>20</party-type>
<party-name>{extra_owner}</party-name>
<country>{owner_country}</country>
</case-file-owner>"""
    return f"""
<case-file>
<serial-number>{serial}</serial-number>
<registration-number>0000000</registration-number>
<transaction-date>{filing}</transaction-date>
<case-file-header>
<filing-date>{filing}</filing-date>
<status-code>630</status-code>
<status-date>{filing}</status-date>
<mark-identification>{mark}</mark-identification>
<mark-drawing-code>4</mark-drawing-code>
<attorney-name>Jane Q. Filer</attorney-name>
<domestic-representative-name>Example Rep LLC</domestic-representative-name>
<current-location>PUBLICATION AND ISSUE SECTION</current-location>
<location-date>{filing}</location-date>
<standard-characters-claimed-in>T</standard-characters-claimed-in>
<use-application-currently-in>T</use-application-currently-in>
<intent-to-use-current-in>F</intent-to-use-current-in>
<filed-as-use-application-in>T</filed-as-use-application-in>
<intent-to-use-in>F</intent-to-use-in>
<opposition-pending-in>F</opposition-pending-in>
<section-8-filed-in>F</section-8-filed-in>
<color-drawing-current-in>F</color-drawing-current-in>
<trademark-in>T</trademark-in>
<service-mark-in>F</service-mark-in>
{header_extra}
</case-file-header>
<international-registration>
<international-registration-number>1234567</international-registration-number>
<international-registration-date>{filing}</international-registration-date>
<international-status-code>400</international-status-code>
<first-refusal-in>F</first-refusal-in>
</international-registration>
<prior-registration-applications>
<other-related-in>T</other-related-in>
<prior-registration-application><number>75000001</number><relationship-type>0</relationship-type></prior-registration-application>
</prior-registration-applications>
<case-file-statements>
<case-file-statement><type-code>GS0091</type-code><text>{statement}</text></case-file-statement>
</case-file-statements>
<case-file-event-statements>
<case-file-event-statement><code>NWAP</code><type>I</type><date>{filing}</date><number>1</number></case-file-event-statement>
<case-file-event-statement><code>DOCK</code><type>D</type><date>{filing}</date><number>2</number></case-file-event-statement>
{extra_events}
</case-file-event-statements>
<classifications>
<classification>
<international-code-total-no>1</international-code-total-no>
<us-code-total-no>1</us-code-total-no>
<international-code>9</international-code>
<us-code>021</us-code>
<status-code>6</status-code>
<status-date>{filing}</status-date>
<first-use-anywhere-date>{filing}</first-use-anywhere-date>
<first-use-in-commerce-date>{filing}</first-use-in-commerce-date>
<primary-code>009</primary-code>
</classification>
</classifications>
<correspondent>
<address-1>ACME IP LLC</address-1>
<address-2>1 Main St</address-2>
</correspondent>
{f'<case-file-owners>{owners}</case-file-owners>'}
<design-searches>
<design-search><code>261721</code></design-search>
</design-searches>
</case-file>"""


def make_xml(case_files: list[str], version: str = "2.0") -> bytes:
    return _WRAPPER.format(version=version,
                           case_files="\n".join(case_files)).encode()


def make_zip(path: Path, xml: bytes, member: str = "payload.xml") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, xml)
    return path


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config(repo_root=tmp_path, data_root=tmp_path / "data")
    c.ensure_dirs()
    return c


@pytest.fixture
def ledger(cfg: Config) -> Iterator[Ledger]:
    led = Ledger(cfg.ledger_path)
    yield led
    led.close()
