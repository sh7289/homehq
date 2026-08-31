import pytest


@pytest.fixture
def conn(tmp_path):
    import db

    connection = db.get_connection(str(tmp_path / "pantry.db"))
    db.init_db(connection)
    yield connection
    connection.close()


def test_add_and_list_shopping_list_items(conn):
    import db

    db.add_shopping_list_item(conn, name="Rice", storage="pantry", quantity_to_buy=2)

    items = db.list_shopping_list_items(conn)

    assert len(items) == 1
    assert items[0]["name"] == "Rice"
    assert items[0]["storage"] == "pantry"
    assert items[0]["quantity_to_buy"] == 2


def test_add_shopping_list_item_quantity_to_buy_is_optional(conn):
    import db

    db.add_shopping_list_item(conn, name="Peas", storage="freezer")

    assert db.list_shopping_list_items(conn)[0]["quantity_to_buy"] is None


def test_get_shopping_list_item_returns_none_when_missing(conn):
    import db

    assert db.get_shopping_list_item(conn, 999) is None


def test_get_shopping_list_item_returns_the_item(conn):
    import db

    item_id = db.add_shopping_list_item(conn, name="Rice", storage="pantry")

    item = db.get_shopping_list_item(conn, item_id)

    assert item["name"] == "Rice"


def test_delete_shopping_list_item_removes_it(conn):
    import db

    item_id = db.add_shopping_list_item(conn, name="Rice", storage="pantry")

    db.delete_shopping_list_item(conn, item_id)

    assert db.list_shopping_list_items(conn) == []
