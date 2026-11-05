import datetime

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tracker import services
from tracker.models import Stage

DAY = datetime.date(2026, 10, 1)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("me", password="x")


@pytest.fixture
def other_user(db):
    return get_user_model().objects.create_user("someone-else", password="x")


@pytest.fixture
def make_app(user):
    def make(company="Kiezbank", role="Backend Engineer", stage=Stage.APPLIED, owner=None, **fields):
        return services.create_application(owner or user, company_name=company, role=role, stage=stage, **fields)
    return make


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client
