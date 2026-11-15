"""PostgreSQL repository for guidance — the publication of the guidance.

The current versions are the rows of ``guidance_versions`` whose
``last_publication_id`` is the id of the newest publication; before the
first publication nothing is current.  ``publish`` stores the whole
guidance in one transaction: when every item matches the current
version of its key and every current key is in the body, nothing is
written (``changed`` is false).  Otherwise it stores one new row in
``guidance_publications`` (``source`` as sent, ``published_by`` the
caller's username, ``item_count`` the number of items) and then, for
each item, either sets the matching current row's
``last_publication_id`` to the new publication or inserts a new row
whose ``first_publication_id`` and ``last_publication_id`` are both the
new publication.  A current row whose key is not in the body is left
as it is: it stops being current because its ``last_publication_id``
is no longer the newest.  No row of ``guidance_versions`` is ever
deleted and no column of an existing row other than
``last_publication_id`` is ever changed; the stored text is exactly
the string that was sent.  When any write fails, the whole
transaction rolls back and nothing is stored.
"""

from datetime import date

from app.db.connection import get_cursor

# The columns an item and a current row are compared on, besides the
# key itself: all of these equal (an absent optional field equals
# null) is a match.
_MATCH_FIELDS = (
    "hash",
    "type",
    "scope",
    "area",
    "area_title",
    "title",
    "statement",
    "status",
    "decided_by",
    "decided_under",
    "decided_ref",
    "authority",
)


def _matches(row: dict, item: dict) -> bool:
    """True when the item matches the current version row of its key.

    All of ``hash``, ``type``, ``scope``, ``area``, ``area_title``,
    ``title``, ``statement``, ``status``, ``decided_by``,
    ``decided_on``, ``decided_under``, ``decided_ref`` and
    ``authority`` must be equal, where an absent optional field
    equals null.  ``decided_on`` comes back from the date column as a
    ``date`` and is compared in ISO form, the ``YYYY-MM-DD`` the API
    layer already validated.
    """
    if row["decided_on"].isoformat() != item["decided_on"]:
        return False
    return all(row[field] == item[field] for field in _MATCH_FIELDS)


class GuidancePostgresRepository:
    """PostgreSQL repository for the guidance tables."""

    def current(self) -> dict:
        """The newest publication and the versions it makes current.

        Read-only: nothing is ever written.  When nothing was ever
        published, ``{"publication": None, "items": []}``; otherwise
        ``publication`` is the newest row of
        ``guidance_publications`` (the one with the highest ``id``)
        and ``items`` the rows of ``guidance_versions`` whose
        ``last_publication_id`` is that id, each row carrying
        ``since`` besides its own columns — the ``published_at`` of
        the publication the row's ``first_publication_id`` names.
        The rows come back in the database's own order: the route
        sorts them, so the answer never depends on the database's
        collation.
        """
        with get_cursor() as cur:
            cur.execute("SELECT * FROM guidance_publications ORDER BY id DESC LIMIT 1")
            newest = cur.fetchone()
            if newest is None:
                return {"publication": None, "items": []}
            publication = dict(newest)
            cur.execute(
                "SELECT v.*, fp.published_at AS since "
                "FROM guidance_versions v "
                "JOIN guidance_publications fp "
                "ON fp.id = v.first_publication_id "
                "WHERE v.last_publication_id = %s",
                (publication["id"],),
            )
            items = [dict(row) for row in cur.fetchall()]
        return {"publication": publication, "items": items}

    def item_history(self, key: str) -> dict | None:
        """Every version row of one key, newest first; None when absent.

        Read-only: nothing is ever written.  When no row of
        ``guidance_versions`` has the key, ``None``; otherwise
        ``{"newest_publication_id": ..., "versions": [...]}`` where
        ``newest_publication_id`` is the id of the newest row of
        ``guidance_publications`` (None before the first
        publication) and ``versions`` the key's rows in descending
        ``id`` order, each carrying its own columns plus the
        publications its ``first_publication_id`` and
        ``last_publication_id`` name (``first_source``,
        ``first_published_at``, ``first_published_by`` and the same
        for ``last``).
        """
        with get_cursor() as cur:
            cur.execute("SELECT id FROM guidance_publications ORDER BY id DESC LIMIT 1")
            newest = cur.fetchone()
            cur.execute(
                "SELECT v.*, "
                "fp.source AS first_source, "
                "fp.published_at AS first_published_at, "
                "fp.published_by AS first_published_by, "
                "lp.source AS last_source, "
                "lp.published_at AS last_published_at, "
                "lp.published_by AS last_published_by "
                "FROM guidance_versions v "
                "JOIN guidance_publications fp ON fp.id = v.first_publication_id "
                "JOIN guidance_publications lp ON lp.id = v.last_publication_id "
                "WHERE v.key = %s "
                "ORDER BY v.id DESC",
                (key,),
            )
            rows = [dict(row) for row in cur.fetchall()]
        if not rows:
            return None
        return {
            "newest_publication_id": newest["id"] if newest is not None else None,
            "versions": rows,
        }

    def publish(self, source: str, items: list[dict], published_by: str) -> dict:
        """Publish the whole guidance; the answer of the route.

        One transaction: the newest publication before the call names
        the current versions; when everything matches and nothing is
        missing, nothing is written.  Otherwise one new
        ``guidance_publications`` row and, per item, a bump of the
        matching current row's ``last_publication_id`` or a new
        ``guidance_versions`` row.  Returns ``changed``,
        ``publication_id`` (the id of the newest publication after the
        call), ``items``, ``new_versions`` and ``withdrawn`` (the keys
        that were current before the call and are not in the body).
        """
        with get_cursor() as cur:
            cur.execute("SELECT id FROM guidance_publications ORDER BY id DESC LIMIT 1")
            newest = cur.fetchone()
            newest_id = newest["id"] if newest else None

            current: dict[str, dict] = {}
            if newest_id is not None:
                cur.execute(
                    "SELECT * FROM guidance_versions " "WHERE last_publication_id = %s",
                    (newest_id,),
                )
                for row in cur.fetchall():
                    current[row["key"]] = dict(row)

            body_keys = {item["key"] for item in items}
            if newest_id is not None and body_keys == set(current):
                # Every item matches and every current key is in the
                # body: nothing changed, nothing is written.
                if all(_matches(current[item["key"]], item) for item in items):
                    return {
                        "changed": False,
                        "publication_id": newest_id,
                        "items": len(items),
                        "new_versions": 0,
                        "withdrawn": 0,
                    }

            cur.execute(
                "INSERT INTO guidance_publications "
                "(source, published_by, item_count) "
                "VALUES (%s, %s, %s) RETURNING id",
                (source, published_by, len(items)),
            )
            publication_id = cur.fetchone()["id"]

            new_versions = 0
            for item in items:
                row = current.get(item["key"])
                if row is not None and _matches(row, item):
                    cur.execute(
                        "UPDATE guidance_versions "
                        "SET last_publication_id = %s WHERE id = %s",
                        (publication_id, row["id"]),
                    )
                else:
                    cur.execute(
                        "INSERT INTO guidance_versions "
                        "(key, hash, type, scope, area, area_title, title, "
                        "statement, status, decided_by, decided_on, "
                        "decided_under, decided_ref, authority, "
                        "first_publication_id, last_publication_id) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                        "%s, %s, %s, %s, %s)",
                        (
                            item["key"],
                            item["hash"],
                            item["type"],
                            item["scope"],
                            item["area"],
                            item["area_title"],
                            item["title"],
                            item["statement"],
                            item["status"],
                            item["decided_by"],
                            date.fromisoformat(item["decided_on"]),
                            item["decided_under"],
                            item["decided_ref"],
                            item["authority"],
                            publication_id,
                            publication_id,
                        ),
                    )
                    new_versions += 1

            withdrawn = sum(1 for key in current if key not in body_keys)
            return {
                "changed": True,
                "publication_id": publication_id,
                "items": len(items),
                "new_versions": new_versions,
                "withdrawn": withdrawn,
            }
