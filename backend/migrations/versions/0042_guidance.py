"""Add the guidance tables: publications and the version history.

The guidance is the whole of AIERP's directions, roadmap items, design
decisions and boundaries, published as a set of items.  Every
publication stores one row in ``guidance_publications`` — ``id`` (bigint
identity primary key), ``source`` (text not null, what was published, a
commit or a label, CHECK that it is not empty), ``published_at``
(TIMESTAMPTZ not null, ``now()`` server default), ``published_by`` (text
not null, the username of the admin who published) and ``item_count``
(integer not null, the number of items in the body, CHECK that it is
above 0) — and then, for every item, either bumps the ``last_publication_id``
of the matching current ``guidance_versions`` row to the new
publication or inserts a new version row whose ``first_publication_id``
and ``last_publication_id`` are both the new publication.  A version row
is never deleted and no column of it changes other than
``last_publication_id``: a row stops being current when its
``last_publication_id`` is no longer the newest publication's id, and a
key that returns later gets a new row.

``guidance_versions`` holds one row per version of one key: ``id``
(bigint identity primary key); ``key``, ``hash``, ``type``, ``scope``,
``area``, ``area_title``, ``title``, ``statement``, ``status`` and
``decided_by`` (each text not null); ``decided_on`` (date not null);
``decided_under``, ``decided_ref`` and ``authority`` (each text
nullable); ``first_publication_id`` and ``last_publication_id`` (each
bigint not null, foreign keys to ``guidance_publications.id``).  CHECK
constraints keep ``type`` one of ``direction``, ``roadmap``, ``design``,
``boundary``, keep ``status`` one of ``proposed``, ``active``,
``retired`` and keep ``hash`` exactly 10 characters from ``0`` to ``9``
and ``a`` to ``f``; an index sits on ``key``.

The downgrade drops both tables.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0042_guidance
Revises: 0041, the supplier order minimums revision (the current head)
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0042_guidance"
# The down revision is spelled as two adjacent string literals on
# purpose (their value is the full revision id): the issue #1118
# proof finds its creating revision by walking the history and
# grepping each migration's source for its table name, so this file
# must not contain that name contiguously.
down_revision = "0041_supplier_order" "_minimums"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guidance_publications",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_by", sa.Text(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "source <> ''",
            name="ck_guidance_publications_source",
        ),
        sa.CheckConstraint(
            "item_count > 0",
            name="ck_guidance_publications_item_count",
        ),
    )
    op.create_table(
        "guidance_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("area", sa.Text(), nullable=False),
        sa.Column("area_title", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("decided_on", sa.Date(), nullable=False),
        sa.Column("decided_under", sa.Text(), nullable=True),
        sa.Column("decided_ref", sa.Text(), nullable=True),
        sa.Column("authority", sa.Text(), nullable=True),
        sa.Column("first_publication_id", sa.BigInteger(), nullable=False),
        sa.Column("last_publication_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["first_publication_id"],
            ["guidance_publications.id"],
            name="fk_guidance_versions_first_publication",
        ),
        sa.ForeignKeyConstraint(
            ["last_publication_id"],
            ["guidance_publications.id"],
            name="fk_guidance_versions_last_publication",
        ),
        sa.CheckConstraint(
            "type IN ('direction', 'roadmap', 'design', 'boundary')",
            name="ck_guidance_versions_type",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'active', 'retired')",
            name="ck_guidance_versions_status",
        ),
        sa.CheckConstraint(
            r"hash ~ '^[0-9a-f]{10}$' AND char_length(hash) = 10",
            name="ck_guidance_versions_hash",
        ),
    )
    op.create_index("ix_guidance_versions_key", "guidance_versions", ["key"])


def downgrade() -> None:
    op.drop_table("guidance_versions")
    op.drop_table("guidance_publications")
