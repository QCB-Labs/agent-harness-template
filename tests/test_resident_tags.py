"""Made-up resident index; no agent memory is used."""
from pathlib import Path

import pytest

from tools.cards import extract_tags, load_patterns

ROOT = Path(__file__).resolve().parents[1]
CITY = ROOT / "config" / "tag-patterns.city.json"

TABLE = (
    "# Resident Index\r\n\r\n"
    "| Handle | ID | Focus | Dossier |\r\n"
    "|:---|:---:|:---|:---|\r\n"
    "| **mira** | 12 | maps | [`mira.md`](mira.md) |\r\n"
    "| **old-tom** | 345 | clocks | [`old-tom.md`](old-tom.md) |\r\n"
    "| **nobody-yet** | — | unknown | [`nobody-yet.md`](nobody-yet.md) |\r\n"
)
BULLETS = "# Residents\n\n- **mira** — resident #12\n- **old-tom**\n"


def tags(tmp_path, text, index=TABLE, title=None):
    path = tmp_path / "index.md"
    path.write_bytes(index.encode("utf-8"))
    return extract_tags(kind="thread", text=text, title=title, patterns=load_patterns(CITY), name_index=path)


@pytest.mark.parametrize("index", [TABLE, BULLETS])
def test_handle_with_its_own_id_beside_it_is_a_resident(tmp_path, index):
    found = tags(tmp_path, "A talk about tides.", index=index, title="Mira (#12) — The Tide Room")
    assert {"name:mira", "resident:12"} <= set(found)
    assert "ref:12" not in found


def test_table_index_gives_name_tags(tmp_path):
    found = tags(tmp_path, "Old-Tom wound the clock while nobody-yet watched.")
    assert {"name:old-tom", "name:nobody-yet"} <= set(found)
    assert not [tag for tag in found if tag.startswith("resident:")]


def test_a_different_number_beside_the_name_stays_a_ref(tmp_path):
    found = tags(tmp_path, "Mira (#99) signed the slate.")
    assert "name:mira" in found and "ref:99" in found
    assert "resident:99" not in found and "resident:12" not in found


def test_id_far_from_the_name_is_not_typed(tmp_path):
    found = tags(tmp_path, "Mira spoke first. Later, item #12 was moved.")
    assert "name:mira" in found and "ref:12" in found
    assert "resident:12" not in found


def test_longer_number_is_not_mistaken_for_the_id(tmp_path):
    found = tags(tmp_path, "Mira (#123) arrived.")
    assert "resident:12" not in found and "ref:123" in found


def test_unknown_name_with_a_number_stays_a_ref(tmp_path):
    found = tags(tmp_path, "Stranger (#77) passed through.")
    assert "ref:77" in found and "resident:77" not in found
