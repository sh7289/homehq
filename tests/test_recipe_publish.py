"""Recipe saves are committed and pushed like catalog edits, so the server checkout stays clean."""
import catalog_writer as cw
from test_recipe_routes import TINGA, _login, _write_recipe


def _capture(monkeypatch, fail=False):
    calls = []

    def push(repo_dir, message, token, **kwargs):
        calls.append((message, kwargs.get("paths")))
        if fail:
            raise cw.PushFailed("no network")

    monkeypatch.setenv("HOMEHQ_GITHUB_TOKEN", "fake")
    monkeypatch.setattr(cw, "git_commit_and_push", push)
    return calls


def test_new_recipe_is_committed_with_only_the_recipes_folder(client, app, monkeypatch):
    calls = _capture(monkeypatch)
    _login(client)
    client.post("/recipes/new", data={"name": "Fish Pie", "kind": "meal", "ingredients": "cod | 1 | lb", "body": "Bake."})
    assert calls == [("Add recipe: Fish Pie", ("recipes",))]


def test_every_recipe_edit_is_committed(client, app, monkeypatch):
    _write_recipe(app, "chicken-tinga-tacos.md", TINGA)
    calls = _capture(monkeypatch)
    _login(client)
    client.post("/recipes/chicken-tinga-tacos/edit", data={"name": "Chicken Tinga Tacos", "kind": "meal", "body": "x"})
    client.post("/recipes/chicken-tinga-tacos/ingredients",
                data={"ingredients": "chicken thighs | 2 | lb"})
    assert [m for m, _ in calls][:2] == ["Update recipe: Chicken Tinga Tacos", "Update recipe ingredients: Chicken Tinga Tacos"]


def test_a_failed_push_still_saves_and_says_so(client, app, monkeypatch):
    _write_recipe(app, "chicken-tinga-tacos.md", TINGA)
    _capture(monkeypatch, fail=True)
    _login(client)
    response = client.post("/recipes/chicken-tinga-tacos/edit",
                           data={"name": "Chicken Tinga Tacos", "kind": "meal", "body": "Saved anyway."})
    assert response.status_code == 302 and "push_failed=1" in response.location
    assert "Saved anyway." in app.recipes.get("chicken-tinga-tacos").body
    assert "not yet backed up to GitHub" in client.get(response.location).data.decode()


def test_no_token_means_no_commit(client, app, monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.delenv("HOMEHQ_GITHUB_TOKEN")
    _login(client)
    client.post("/recipes/new", data={"name": "Fish Pie", "kind": "meal", "ingredients": "cod | 1 | lb", "body": "Bake."})
    assert calls == []
