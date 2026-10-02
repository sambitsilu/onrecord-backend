import json
import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://onrecord:onrecord@localhost:5432/onrecord_test")
os.environ["AUTH_MODE"] = "dev"
os.environ["ENV"] = "test"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.db import init_engine  # noqa: E402
from app.llm import Usage  # noqa: E402

URL = os.environ["DATABASE_URL"]
TABLES = "audit_events, llm_usage, records, extraction_runs, transcript_lines, calls, clients, memberships, organizations, users"


class FakeLLM:
    """Returns scripted replies. `script` maps a substring of the prompt to a reply (dict or str)."""
    model = "fake-model"

    def __init__(self):
        self.script: list[tuple[str, object]] = []
        self.prompts: list[str] = []
        self.fail_with = None

    def on(self, needle: str, reply) -> None:
        self.script.insert(0, (needle, reply))

    def complete(self, system: str, user: str, max_tokens: int, schema=None):
        self.prompts.append(user)
        self.schemas = getattr(self, "schemas", []) + [schema]
        if self.fail_with:
            raise self.fail_with
        for needle, reply in self.script:
            if needle in user:
                return (reply if isinstance(reply, str) else json.dumps(reply)), Usage(100, 50)
        return json.dumps({"summary": "", "items": []}), Usage(100, 50)


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(URL)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    cfg.attributes["database_url"] = URL
    command.upgrade(cfg, "head")
    init_engine(URL)
    return eng


@pytest.fixture(autouse=True)
def clean(engine):
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def client(engine, llm):
    from app.main import create_app
    app = create_app(llm_factory=lambda: llm)
    with TestClient(app) as tc:
        yield tc


def auth(email: str) -> dict:
    return {"Authorization": f"Bearer dev:{email}"}
