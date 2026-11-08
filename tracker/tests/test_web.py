import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from tracker import services
from tracker.models import Application, Stage

from .conftest import DAY


@pytest.fixture
def web(user):
    c = Client()
    c.force_login(user)
    return c


def test_pages_require_login(db):
    for url in ["/", "/closed/", "/applications/new/", "/import/"]:
        assert Client().get(url).status_code == 302


def test_dashboard_lists_everything_by_default(web, make_app):
    make_app(company="Kiezbank")
    services.move_to_stage(make_app(company="Brightgrid"), Stage.REJECTED)
    body = web.get("/").content.decode()
    assert "Kiezbank" in body and "Brightgrid" in body and "Where everything stands" in body


def test_dashboard_filters_by_group_and_search(web, make_app):
    make_app(company="Kiezbank", role="Backend Engineer")
    services.move_to_stage(make_app(company="Brightgrid", role="Data Engineer"), Stage.REJECTED)
    rejected = web.get("/?group=rejected").content.decode()
    assert "Brightgrid" in rejected and "Kiezbank" not in rejected
    searched = web.get("/?q=backend").content.decode()
    assert "Kiezbank" in searched and "Brightgrid" not in searched
    assert web.get("/?group=nonsense").status_code == 200


def test_dashboard_shows_follow_ups_only_when_due(web, make_app):
    make_app(company="Kiezbank", applied_on=DAY)
    assert "Follow up" in web.get("/").content.decode()


def test_empty_dashboard_points_to_import(web):
    assert "import a spreadsheet" in web.get("/").content.decode()


def test_old_closed_url_redirects_to_filter(web):
    res = web.get("/closed/")
    assert res.status_code == 302 and res["Location"].endswith("?group=rejected")


def test_add_form_creates_application(web, user):
    res = web.post("/applications/new/", {"company_name": "Brightgrid", "role": "Platform Engineer",
                                          "source": "linkedin", "stage": "applied"})
    app = Application.objects.get(owner=user)
    assert res.status_code == 302 and res["Location"] == f"/applications/{app.pk}/"
    assert app.stage == Stage.APPLIED and app.applied_on


def test_move_button(web, make_app):
    app = make_app()
    web.post(f"/applications/{app.pk}/move/", {"stage": "screening", "note": "Call booked"})
    app.refresh_from_db()
    assert app.stage == Stage.SCREENING


def test_invalid_move_shows_error_not_500(web, make_app):
    app = make_app()
    res = web.post(f"/applications/{app.pk}/move/", {"stage": "accepted"}, follow=True)
    assert res.status_code == 200 and "Can&#x27;t move" in res.content.decode()


def test_adding_interview_moves_to_interviewing(web, make_app):
    app = make_app()
    web.post(f"/applications/{app.pk}/interviews/", {"iv-kind": "technical", "iv-scheduled_at": "2026-11-12T10:00"})
    app.refresh_from_db()
    assert app.stage == Stage.INTERVIEW and app.interviews.count() == 1


def test_other_users_pages_are_404(web, make_app, other_user):
    theirs = make_app(owner=other_user)
    assert web.get(f"/applications/{theirs.pk}/").status_code == 404
    assert web.post(f"/applications/{theirs.pk}/move/", {"stage": "rejected"}).status_code == 404


def test_import_page(web, user):
    f = SimpleUploadedFile("t.csv", b"Company,Role\nKiezbank,Dev\n", content_type="text/csv")
    body = web.post("/import/", {"file": f}).content.decode()
    assert "1</strong> imported" in body


def test_import_page_replace_option(web, make_app, user):
    make_app(company="Old Co")
    f = SimpleUploadedFile("t.csv", b"Company,Role\nNew Co,Dev\n", content_type="text/csv")
    body = web.post("/import/", {"file": f, "replace": "on"}).content.decode()
    assert "1</strong> replaced" in body
    assert list(Application.objects.filter(owner=user).values_list("company__name", flat=True)) == ["New Co"]


def test_admin_shows_only_own_rows(make_app, user, other_user):
    user.is_staff = True
    user.save()
    from django.contrib.auth.models import Permission
    user.user_permissions.set(Permission.objects.filter(content_type__app_label="tracker"))
    make_app(company="Mine")
    make_app(owner=other_user, company="Theirs")
    c = Client()
    c.force_login(user)
    body = c.get("/admin/tracker/application/").content.decode()
    assert "Mine" in body and "Theirs" not in body
    for model in ["company", "interview"]:
        assert c.get(f"/admin/tracker/{model}/").status_code == 200


def test_healthz(db):
    assert Client().get("/healthz").json() == {"status": "ok"}
