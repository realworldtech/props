"""Add SearchVectorField with GIN index and PostgreSQL trigger to Asset."""

import django.contrib.postgres.indexes
from django.contrib.postgres.search import SearchVectorField
from django.db import migrations


def backfill_search_vector(apps, schema_editor):
    """Populate search_vector for existing rows (PostgreSQL only)."""
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        UPDATE assets_asset
        SET search_vector =
            setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(description, '')), 'B')
        """)


def create_trigger(apps, schema_editor):
    """Create PostgreSQL trigger to auto-update search_vector."""
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        CREATE OR REPLACE FUNCTION assets_asset_search_vector_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', coalesce(NEW.name, '')), 'A') ||
                setweight(
                    to_tsvector('english', coalesce(NEW.description, '')), 'B'
                );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS assets_asset_search_vector_trigger
            ON assets_asset;

        CREATE TRIGGER assets_asset_search_vector_trigger
            BEFORE INSERT OR UPDATE OF name, description
            ON assets_asset
            FOR EACH ROW
            EXECUTE FUNCTION assets_asset_search_vector_update();
        """)


def drop_trigger(apps, schema_editor):
    """Drop the search_vector trigger and function (reverse)."""
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        DROP TRIGGER IF EXISTS assets_asset_search_vector_trigger
            ON assets_asset;
        DROP FUNCTION IF EXISTS assets_asset_search_vector_update();
        """)


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0038_location_created_at_not_null"),
    ]

    operations = [
        # 1. Add the SearchVectorField column
        migrations.AddField(
            model_name="asset",
            name="search_vector",
            field=SearchVectorField(editable=False, null=True),
        ),
        # 2. Add GIN index on the new column
        migrations.AddIndex(
            model_name="asset",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_vector"],
                name="idx_asset_search_vector",
            ),
        ),
        # 3. Backfill existing rows
        migrations.RunPython(
            backfill_search_vector,
            reverse_code=migrations.RunPython.noop,
        ),
        # 4. Create trigger for automatic updates
        migrations.RunPython(
            create_trigger,
            reverse_code=drop_trigger,
        ),
    ]
