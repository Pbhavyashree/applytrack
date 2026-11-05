import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from tracker.models import Application, Stage


@pytest.fixture
def web(user):
    c = Client()
    c.force_login(user)
    return c


def test_pages_require_login(db):
    for url in ["/", "/closed/", "/applications/new/", "/import/"]:
        assert Client().get(url).status_code == 302


def test_dashboard_renders_board(web, make_app):
    make_app(company="Kiezbank")
    body = web.get("/").content.decode()
    assert "Kiezbank" in body and "Follow up" in body


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
