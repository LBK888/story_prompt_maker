from app.characters import CharacterCatalog


def test_catalog_exposes_only_introduction_for_card_but_keeps_full_profile(tmp_path):
    folder = tmp_path / "person_Ada"
    folder.mkdir()
    folder.joinpath("profile.txt").write_text("姓名：阿達\n職業：畫家\n介紹：住在海邊的畫家\n服裝：藍衣\n", encoding="utf-8")
    item = CharacterCatalog(tmp_path).item("ada", folder)
    assert item["introduction"] == "住在海邊的畫家"
    assert "職業：畫家" in item["profile"]
    assert item["name"] == "阿達"
