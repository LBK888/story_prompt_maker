from pathlib import Path


SOURCE = Path("templates/index.html").read_text(encoding="utf-8")


def test_recent_project_cards_have_a_separate_delete_button() -> None:
    assert 'class="project-card"' in SOURCE
    assert 'class="project-card-link"' in SOURCE
    assert 'class="danger small delete-project"' in SOURCE


def test_home_delete_archives_project_and_preserves_files() -> None:
    assert "body:JSON.stringify({archived:true})" in SOURCE
    assert "專案資料與生成檔案都會保留" in SOURCE
    assert "card.remove()" in SOURCE
    assert "document.querySelector('.section-head .count').textContent = remaining" in SOURCE
