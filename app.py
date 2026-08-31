import os

import bcrypt
from flask import (
    Flask,
    Response,
    abort,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from werkzeug.middleware.proxy_fix import ProxyFix

import db
import export_csv
import expiry
import matching
from catalog_store import CatalogStore


class User(UserMixin):
    def __init__(self, username):
        self.id = username


def _load_users():
    """Read the two household accounts from env vars.

    HOMEHQ_USER1_NAME / HOMEHQ_USER1_HASH, HOMEHQ_USER2_NAME / HOMEHQ_USER2_HASH.
    """
    users = {}
    for i in (1, 2):
        name = os.environ.get(f"HOMEHQ_USER{i}_NAME")
        password_hash = os.environ.get(f"HOMEHQ_USER{i}_HASH")
        if name and password_hash:
            users[name] = password_hash
    return users


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    behind_tls_proxy = os.environ.get("HOMEHQ_BEHIND_TLS_PROXY", "").lower() == "true"
    app.config["SESSION_COOKIE_SECURE"] = behind_tls_proxy
    if behind_tls_proxy:
        # nginx terminates TLS and forwards plain HTTP; trust its headers so
        # request.is_secure (and therefore secure cookies) work correctly,
        # and url_for(_external=True) generates https:// links.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    users = _load_users()

    content_dir = os.environ["HOMEHQ_CONTENT_DIR"]
    photos_dir = os.environ["HOMEHQ_PHOTOS_DIR"]
    db_path = os.environ["HOMEHQ_DB_PATH"]
    app.catalog = CatalogStore(content_dir)
    app.catalog.reload()

    def get_db():
        if "db_conn" not in g:
            g.db_conn = db.get_connection(db_path)
            db.init_db(g.db_conn)
        return g.db_conn

    @app.teardown_appcontext
    def close_db(exception=None):
        conn = g.pop("db_conn", None)
        if conn is not None:
            conn.close()

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(username):
        if username in users:
            return User(username)
        return None

    @app.template_filter("titlecase")
    def titlecase_filter(value):
        return value.replace("_", " ").replace("-", " ").title()

    @app.context_processor
    def inject_nav():
        if current_user.is_authenticated:
            return {"nav_categories": app.catalog.categories()}
        return {"nav_categories": []}

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "").encode("utf-8")
            password_hash = users.get(username)
            if password_hash and bcrypt.checkpw(password, password_hash.encode("utf-8")):
                login_user(User(username))
                return redirect(url_for("home"))
            return render_template("login.html", error="Invalid username or password"), 401
        return render_template("login.html", error=None)

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def home():
        return render_template(
            "home.html", categories=app.catalog.categories(), active="home"
        )

    def _render_inventory_page(storage):
        query = request.args.get("q", "").strip().lower()
        items = db.list_items(get_db(), storage=storage)
        if query:
            items = [item for item in items if query in item["name"].lower()]
        expired, expiring_soon = expiry.split_by_expiry(items, storage=storage)
        return render_template(
            "inventory.html",
            storage=storage,
            user=current_user.id,
            items=items,
            expired=expired,
            expiring_soon=expiring_soon,
            query=query,
            active=storage,
        )

    def _add_inventory_item(storage):
        expiry_date = request.form.get("expiry_date", "").strip() or None
        acquired_date = request.form.get("acquired_date", "").strip() or None
        shelf_life_days = request.form.get("shelf_life_days", "").strip() or None
        db.add_item(
            get_db(),
            name=request.form["name"].strip(),
            quantity=float(request.form.get("quantity") or 0),
            unit=request.form.get("unit", "").strip(),
            location=request.form.get("location", "").strip(),
            storage=storage,
            expiry_date=expiry_date,
            acquired_date=acquired_date,
            shelf_life_days=int(shelf_life_days) if shelf_life_days else None,
        )
        return redirect(url_for(storage))

    @app.route("/pantry")
    @login_required
    def pantry():
        return _render_inventory_page("pantry")

    @app.route("/pantry/add", methods=["POST"])
    @login_required
    def pantry_add():
        return _add_inventory_item("pantry")

    @app.route("/freezer")
    @login_required
    def freezer():
        return _render_inventory_page("freezer")

    @app.route("/freezer/add", methods=["POST"])
    @login_required
    def freezer_add():
        return _add_inventory_item("freezer")

    def _redirect_to_storage_page():
        storage = request.form.get("storage") or "pantry"
        return redirect(url_for(storage if storage in ("pantry", "freezer") else "pantry"))

    @app.route("/inventory/<int:item_id>/adjust", methods=["POST"])
    @login_required
    def inventory_adjust(item_id):
        db.adjust_quantity(get_db(), item_id, delta=float(request.form["delta"]))
        return _redirect_to_storage_page()

    @app.route("/inventory/<int:item_id>/delete", methods=["POST"])
    @login_required
    def inventory_delete(item_id):
        db.delete_item(get_db(), item_id)
        return _redirect_to_storage_page()

    @app.route("/inventory/<int:item_id>/update", methods=["POST"])
    @login_required
    def inventory_update(item_id):
        shelf_life_days = request.form.get("shelf_life_days", "").strip()
        db.update_item(
            get_db(),
            item_id,
            quantity=float(request.form.get("quantity") or 0),
            unit=request.form.get("unit", "").strip(),
            location=request.form.get("location", "").strip(),
            expiry_date=request.form.get("expiry_date", "").strip() or None,
            acquired_date=request.form.get("acquired_date", "").strip() or None,
            shelf_life_days=int(shelf_life_days) if shelf_life_days else None,
        )
        return _redirect_to_storage_page()

    @app.route("/shopping-list")
    @login_required
    def shopping_list():
        list_items = db.list_shopping_list_items(get_db())
        suggestions = {}
        for list_item in list_items:
            candidates = db.list_items(get_db(), storage=list_item["storage"])
            suggestions[list_item["id"]] = matching.find_best_match(
                list_item["name"], candidates
            )
        return render_template(
            "shopping_list.html",
            list_items=list_items,
            suggestions=suggestions,
            active="shopping-list",
        )

    @app.route("/shopping-list/add", methods=["POST"])
    @login_required
    def shopping_list_add():
        quantity_to_buy = request.form.get("quantity_to_buy", "").strip()
        db.add_shopping_list_item(
            get_db(),
            name=request.form["name"].strip(),
            storage=request.form.get("storage", "pantry"),
            quantity_to_buy=float(quantity_to_buy) if quantity_to_buy else None,
        )
        return redirect(url_for("shopping_list"))

    @app.route("/shopping-list/<int:item_id>/delete", methods=["POST"])
    @login_required
    def shopping_list_delete(item_id):
        db.delete_shopping_list_item(get_db(), item_id)
        return redirect(url_for("shopping_list"))

    @app.route("/shopping-list/<int:item_id>/resolve", methods=["POST"])
    @login_required
    def shopping_list_resolve(item_id):
        list_item = db.get_shopping_list_item(get_db(), item_id)
        if list_item is None:
            abort(404)
        quantity = float(request.form.get("quantity") or 0)
        action = request.form.get("action")

        if action == "match":
            matched_item_id = int(request.form["matched_item_id"])
            db.adjust_quantity(get_db(), matched_item_id, delta=quantity)
        else:
            db.add_item(
                get_db(),
                name=list_item["name"],
                quantity=quantity,
                unit="",
                location="",
                storage=list_item["storage"],
            )

        db.delete_shopping_list_item(get_db(), item_id)
        return redirect(url_for("shopping_list"))

    @app.route("/catalog/<category>")
    @login_required
    def catalog_list(category):
        return render_template(
            "catalog_list.html",
            category=category,
            items=app.catalog.items(category),
            active=category,
        )

    @app.route("/catalog/<category>/<slug>")
    @login_required
    def catalog_detail(category, slug):
        item = app.catalog.get(category, slug)
        if item is None:
            abort(404)
        return render_template("catalog_detail.html", item=item, active=category)

    @app.route("/photos/<path:filename>")
    @login_required
    def photo(filename):
        photos_root = os.path.realpath(photos_dir)
        requested = os.path.realpath(os.path.join(photos_root, filename))
        if os.path.commonpath([photos_root, requested]) != photos_root:
            abort(404)
        return send_from_directory(photos_root, filename)

    @app.route("/export/pantry.csv")
    @login_required
    def export_pantry_csv():
        body = export_csv.pantry_csv(db.list_items(get_db(), storage="pantry"))
        return Response(
            body.encode("utf-8-sig"),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=pantry.csv"},
        )

    @app.route("/export/freezer.csv")
    @login_required
    def export_freezer_csv():
        body = export_csv.pantry_csv(db.list_items(get_db(), storage="freezer"))
        return Response(
            body.encode("utf-8-sig"),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=freezer.csv"},
        )

    @app.route("/export/<category>.csv")
    @login_required
    def export_catalog_csv(category):
        body = export_csv.catalog_csv(app.catalog.items(category))
        return Response(
            body.encode("utf-8-sig"),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={category}.csv"},
        )

    @app.route("/reload", methods=["POST"])
    @login_required
    def reload_catalog():
        app.catalog.reload()
        return redirect(url_for("pantry"))

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=8502)
