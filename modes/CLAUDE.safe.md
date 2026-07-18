# Safe Mode

Operate conservatively. Pause to confirm before taking broad or irreversible actions.

- Before deleting files, dropping features, or running destructive commands (`hackplate dropfeature`, `rm -r`, `alembic downgrade`), summarize what will change and ask the user to confirm.
- Before starting the dev server or docker compose mid-task, confirm with the user.
- Explain your plan before making significant changes.

## Testing

Write tests in `/tests/` as standard pytest files. Use the async client for API tests:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_example():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/ping")
    assert response.status_code == 200
```

Run with: `uv run pytest`

## Before Finishing

Run all three in order and resolve any errors:

```bash
hackplate run                  # verify server starts clean, then Ctrl+C
# if using the keycloak plate:
hackplate run --docker-compose # starts Keycloak via docker compose, waits, then tails logs
uv run pytest                  # run test suite (skip if no tests exist yet)
hackplate precommit            # lint and format
```
