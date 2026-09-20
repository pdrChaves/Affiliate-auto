import pytest

from app.amazon.mock import MockClient
from app.config import Settings, load_niches
from app.db import DB
from app.pipeline.copywriter import Copywriter
from app.senders.base import NullNotifier
from app.service import PromoService


class ListNotifier:
    def __init__(self):
        self.msgs = []

    def notify(self, text):
        self.msgs.append(text)


@pytest.fixture
def settings():
    return Settings(_env_file=None, catalog_mode="mock", amazon_partner_tag="teste-20",
                    database_path=":memory:", niches_file="config/niches.yaml")


@pytest.fixture
def svc(settings):
    client = MockClient(settings, jitter=0)
    return PromoService(settings, load_niches(settings.niches_file), DB(":memory:"), client,
                        Copywriter(settings), ListNotifier())
