from pathlib import Path

from app.models import Base


ROOT = Path(__file__).parents[1]
VERSIONS = ROOT / "alembic" / "versions"


def test_foundation_covers_every_pre_incremental_model_table():
    foundation = (VERSIONS / "20261007_01_foundation_schema.py").read_text()
    incremental_tables = {"auth_sessions", "published_leaderboard"}

    for table in set(Base.metadata.tables) - incremental_tables:
        assert f'"{table}"' in foundation


def test_migration_chain_has_foundation_and_current_schema_columns():
    auth_revision = (VERSIONS / "20261008_01_add_auth_sessions.py").read_text()
    pause_revision = (VERSIONS / "20261009_02_add_round_paused_at.py").read_text()
    cards_revision = (VERSIONS / "20261010_01_add_published_card_usage.py").read_text()
    deadline_revision = (VERSIONS / "20261010_02_add_global_deadline.py").read_text()
    solution_revision = (VERSIONS / "20261010_03_add_published_solution.py").read_text()

    assert 'down_revision = "20261007_01"' in auth_revision
    assert '"paused_at"' in pause_revision
    assert '"card_usage_rows"' in cards_revision
    assert '"global_deadline_at"' in deadline_revision
    assert 'down_revision = "20261010_01"' in deadline_revision
    assert '"published_solution"' in solution_revision
    assert 'down_revision = "20261010_02"' in solution_revision


def test_production_startup_and_seed_do_not_mutate_schema():
    main = (ROOT / "app" / "main.py").read_text()
    seed = (ROOT / "scripts" / "seed.py").read_text()

    for source in (main, seed):
        assert "metadata.create_all" not in source
        assert "ALTER TABLE" not in source
        assert "CREATE TABLE" not in source
    assert "_ensure_round_state_schema" not in main
    assert "RoundState(id=1, phase=\"idle\")" in main
    assert "ADMIN_USERNAME / ADMIN_PASSWORD" in seed
