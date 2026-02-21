# AIlways Backend

## Setup (uv)

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies and lock
uv sync

# Install with dev dependencies
uv sync --dev
```

## Run

```bash
# Start the server
uv run python -m app

# Run migrations
uv run alembic upgrade head
```

## Test

```bash
uv run pytest tests/ -v --tb=short
```
