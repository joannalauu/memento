import os

# Must be set before app import: the lifespan starts the pipeline worker loop
# unless disabled, and tests have no Mongo for it to poll.
os.environ.setdefault("PIPELINE_WORKER_ENABLED", "0")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(scope="session")
async def client():
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
