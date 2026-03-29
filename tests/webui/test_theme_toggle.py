from __future__ import annotations

from ams.webui import create_app


def test_login_page_includes_theme_toggle(tmp_path) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()

    response = client.get("/login")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "data-theme-toggle" in html
    assert "Switch to dark mode" in html
    assert "ams-theme" in html
