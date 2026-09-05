import os
import re
import uuid
from datetime import date

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

import ai_extract
import catalog_writer
import catalog_stats
import db
import export_csv
import expiry
import matching
import recipe_loader
import recipe_writer
import sections
from catalog_store import CatalogStore
from recipe_store import RecipeStore


# How many unsorted items the bulk section-assignment form offers at once.
# The backfilled pantry has ~106 unsorted rows; showing them all would render
# every item twice and make the page unusable on a phone.
SECTION_SORTER_BATCH = 15


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
    uploads_dir = os.environ["HOMEHQ_UPLOADS_DIR"]
    os.makedirs(uploads_dir, exist_ok=True)
    repo_dir = os.path.dirname(os.path.normpath(content_dir))
    app.catalog = CatalogStore(content_dir)
    app.catalog.reload()

    # Recipes live in their own tree, NOT under content_dir: load_catalog turns
    # every subdirectory there into a catalog category, so recipes would show
    # up as a nav tab and inside the insurance report and CSV.
    recipes_dir = os.environ.get(
        "HOMEHQ_RECIPES_DIR", os.path.join(repo_dir, "recipes")
    )
    app.recipes = RecipeStore(recipes_dir)
    app.recipes.reload()

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

    @app.template_filter("paragraphs")
    def paragraphs_filter(text):
        """Split on blank lines; collapse hard wraps inside a paragraph.

        Recipes get pasted in wrapped at 80 characters, so rendering newlines
        literally breaks sentences mid-line. This is what markdown itself
        does with the same input.
        """
        blocks = re.split(r"\n\s*\n", (text or "").strip())
        return [" ".join(block.split()) for block in blocks if block.strip()]

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
        stats = catalog_stats.compute_stats(app.catalog.all_items())
        return render_template(
            "home.html", categories=app.catalog.categories(), stats=stats, active="home"
        )

    def _render_inventory_page(storage):
        query = request.args.get("q", "").strip().lower()
        items = db.list_items(get_db(), storage=storage)
        if query:
            items = [item for item in items if query in item["name"].lower()]
        expired, expiring_soon = expiry.split_by_expiry(items, storage=storage)
        unsectioned = [item for item in items if not item.get("section")]
        return render_template(
            "inventory.html",
            storage=storage,
            user=current_user.id,
            items=items,
            groups=sections.group_items(items, storage),
            all_sections=sections.sections_for(storage),
            unsectioned=unsectioned[:SECTION_SORTER_BATCH],
            unsectioned_total=len(unsectioned),
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
            section=sections.normalize(storage, request.form.get("section")),
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
        storage = request.form.get("storage") or "pantry"
        db.update_item(
            get_db(),
            item_id,
            quantity=float(request.form.get("quantity") or 0),
            unit=request.form.get("unit", "").strip(),
            location=request.form.get("location", "").strip(),
            expiry_date=request.form.get("expiry_date", "").strip() or None,
            acquired_date=request.form.get("acquired_date", "").strip() or None,
            shelf_life_days=int(shelf_life_days) if shelf_life_days else None,
            section=sections.normalize(storage, request.form.get("section")),
        )
        return _redirect_to_storage_page()

    @app.route("/inventory/sections", methods=["POST"])
    @login_required
    def inventory_assign_sections():
        """Bulk-sort the backfilled rows that predate sections."""
        storage = request.form.get("storage") or "pantry"
        conn = get_db()
        for field, value in request.form.items():
            if not field.startswith("section-"):
                continue
            try:
                item_id = int(field[len("section-") :])
            except ValueError:
                continue
            if value:
                db.set_section(conn, item_id, sections.normalize(storage, value))
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

    @app.route("/import")
    @login_required
    def import_review():
        pending = db.list_staging_items(get_db(), status="pending")
        suggestions = {}
        for staging_item in pending:
            if staging_item["target_type"] == "inventory":
                candidates = db.list_items(
                    get_db(), storage=staging_item.get("storage") or "pantry"
                )
                suggestions[staging_item["id"]] = matching.find_best_match(
                    staging_item["name"], candidates
                )
        return render_template(
            "import_review.html",
            items=pending,
            suggestions=suggestions,
            categories=app.catalog.categories(),
            section_choices={
                "pantry": sections.sections_for("pantry"),
                "freezer": sections.sections_for("freezer"),
            },
            active="import",
        )

    @app.route("/import/upload", methods=["GET", "POST"])
    @login_required
    def import_upload():
        if request.method == "POST":
            photo = request.files.get("photo")
            if not photo or not photo.filename:
                return render_template("import_upload.html", error="Choose a photo first.")

            ext = os.path.splitext(photo.filename)[1] or ".jpg"
            upload_path = os.path.join(uploads_dir, f"{uuid.uuid4().hex}{ext}")
            photo.save(upload_path)
            media_type = photo.mimetype or "image/jpeg"

            try:
                api_key = os.environ["HOMEHQ_ANTHROPIC_API_KEY"]
            except KeyError:
                return render_template(
                    "import_upload.html",
                    error="AI import isn't configured yet -- set HOMEHQ_ANTHROPIC_API_KEY.",
                )

            try:
                with open(upload_path, "rb") as f:
                    rows = ai_extract.extract_from_image(
                        f.read(), media_type, api_key=api_key
                    )
            except ai_extract.ExtractionError as exc:
                return render_template("import_upload.html", error=str(exc))

            for row in rows:
                db.add_staging_item(get_db(), source_image_path=upload_path, **row)
            return redirect(url_for("import_review"))

        return render_template("import_upload.html", error=None)

    _INGREDIENT_HELP = (
        "One per line: name | quantity | unit | flags. "
        "Flags are 'fresh' and/or 'staple', comma-separated. "
        "Only the name is required."
    )

    def _parse_ingredient_lines(text):
        """Parse the textarea format into ingredient dicts.

        A plain-text box beats a dynamic add-a-row widget here: it pastes,
        it dictates, and it is trivially editable.
        """
        ingredients = []
        for line in (text or "").splitlines():
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split("|")]
            flags = parts[3].lower() if len(parts) > 3 else ""
            ingredients.append(
                recipe_loader.normalize_ingredient(
                    {
                        "name": parts[0],
                        "quantity": parts[1] if len(parts) > 1 and parts[1] else None,
                        "unit": parts[2] if len(parts) > 2 and parts[2] else None,
                        "fresh": "fresh" in flags,
                        "staple": "staple" in flags,
                    }
                )
            )
        return ingredients

    @app.route("/recipes")
    @login_required
    def recipes():
        kind = request.args.get("kind") or None
        max_effort = request.args.get("max_effort")
        matches = app.recipes.filter(
            kind=kind,
            cuisine=request.args.get("cuisine") or None,
            max_effort=int(max_effort) if max_effort else None,
            favorites_only=request.args.get("favorites") == "on",
        )
        groups = {}
        for recipe in matches:
            groups.setdefault(recipe.kind, []).append(recipe)
        return render_template(
            "recipes.html",
            groups=[{"kind": k, "recipes": groups[k]} for k in sorted(groups)],
            kinds=app.recipes.kinds(),
            cuisines=app.recipes.cuisines(),
            selected_kind=kind,
            total=len(app.recipes.all()),
            active="recipes",
        )

    @app.route("/recipes/new", methods=["GET", "POST"])
    @login_required
    def recipe_new():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                return render_template(
                    "recipe_form.html",
                    error="Give the recipe a name.",
                    form=request.form,
                    ingredient_help=_INGREDIENT_HELP,
                    active="recipes",
                )

            effort = request.form.get("effort", "").strip()
            frontmatter = {
                "kind": request.form.get("kind") or "meal",
                "cuisine": request.form.get("cuisine", "").strip() or None,
                "favorite": request.form.get("favorite") == "on",
                "effort": int(effort) if effort else None,
                "serves": int(request.form["serves"]) if request.form.get("serves") else None,
                "added": date.today().isoformat(),
            }
            recipe_writer.write_recipe(
                recipes_dir,
                name=name,
                frontmatter=frontmatter,
                ingredients=_parse_ingredient_lines(request.form.get("ingredients")),
                body=request.form.get("body", ""),
            )
            app.recipes.reload()
            return redirect(url_for("recipes"))

        return render_template(
            "recipe_form.html",
            error=None,
            form={},
            ingredient_help=_INGREDIENT_HELP,
            active="recipes",
        )

    @app.route("/recipes/<slug>")
    @login_required
    def recipe_detail(slug):
        recipe = app.recipes.get(slug)
        if recipe is None:
            abort(404)
        return render_template("recipe_detail.html", recipe=recipe, active="recipes")

    @app.route("/capture", methods=["GET", "POST"])
    @login_required
    def capture():
        if request.method != "POST":
            return render_template("capture.html", error=None, text="", active="capture")

        text = request.form.get("text", "").strip()
        if not text:
            return render_template(
                "capture.html",
                error="Describe what you're adding first.",
                text="",
                active="capture",
            )

        try:
            api_key = os.environ["HOMEHQ_ANTHROPIC_API_KEY"]
        except KeyError:
            return render_template(
                "capture.html",
                error="AI capture isn't configured yet -- set HOMEHQ_ANTHROPIC_API_KEY.",
                text=text,
                active="capture",
            )

        try:
            rows = ai_extract.extract_from_text(text, api_key=api_key)
        except ai_extract.ExtractionError as exc:
            # Keep the user's words so they can retry without retyping.
            return render_template(
                "capture.html", error=str(exc), text=text, active="capture"
            )

        for row in rows:
            db.add_staging_item(get_db(), **row)
        return redirect(url_for("import_review"))

    @app.route("/import/<int:item_id>/approve", methods=["POST"])
    @login_required
    def import_approve(item_id):
        staging_item = db.get_staging_item(get_db(), item_id)
        if staging_item is None:
            abort(404)

        def field(name):
            return request.form.get(name) or staging_item.get(name)

        if staging_item["target_type"] == "inventory":
            quantity = float(request.form.get("quantity") or 0)
            name = field("name")
            if request.form.get("action") == "match":
                matched_item_id = int(request.form["matched_item_id"])
                db.adjust_quantity(get_db(), matched_item_id, delta=quantity)
            else:
                db.add_item(
                    get_db(),
                    name=name,
                    quantity=quantity,
                    unit=field("unit") or "",
                    location="",
                    storage=field("storage") or "pantry",
                    section=sections.normalize(
                        field("storage") or "pantry", field("section")
                    ),
                )
        else:
            frontmatter = {
                key: field(key) for key in ("brand", "model", "serial_number") if field(key)
            }
            estimated_value = field("estimated_value")
            if estimated_value:
                try:
                    value = float(estimated_value)
                    frontmatter["estimated_value"] = int(value) if value.is_integer() else value
                    frontmatter["estimated_value_date"] = date.today()
                except ValueError:
                    pass
            catalog_writer.write_catalog_item(
                content_dir,
                photos_dir,
                category=field("category") or "kitchen",
                name=field("name"),
                frontmatter=frontmatter,
                body=field("notes") or "",
                source_image_path=staging_item.get("source_image_path"),
            )
            app.catalog.reload()
            github_token = os.environ.get("HOMEHQ_GITHUB_TOKEN")
            if github_token:
                catalog_writer.git_commit_and_push(
                    repo_dir, f"Add catalog item: {field('name')}", github_token
                )

        db.delete_staging_item(get_db(), item_id)
        return redirect(url_for("import_review"))

    @app.route("/import/<int:item_id>/reject", methods=["POST"])
    @login_required
    def import_reject(item_id):
        db.delete_staging_item(get_db(), item_id)
        return redirect(url_for("import_review"))

    @app.route("/report")
    @login_required
    def report():
        catalog = app.catalog.all_items()
        stats = catalog_stats.compute_stats(catalog)
        return render_template(
            "report.html",
            catalog=catalog,
            categories=sorted(catalog.keys()),
            stats=stats,
            active="report",
        )

    @app.route("/report.csv")
    @login_required
    def report_csv():
        body = export_csv.full_catalog_csv(app.catalog.all_items())
        return Response(
            body.encode("utf-8-sig"),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=insurance-report.csv"},
        )

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

    @app.route("/catalog/<category>/<slug>/add-photo", methods=["POST"])
    @login_required
    def catalog_add_photo(category, slug):
        item = app.catalog.get(category, slug)
        if item is None:
            abort(404)

        photo = request.files.get("photo")
        if not photo or not photo.filename:
            return redirect(url_for("catalog_detail", category=category, slug=slug))

        ext = os.path.splitext(photo.filename)[1] or ".jpg"
        upload_path = os.path.join(uploads_dir, f"{uuid.uuid4().hex}{ext}")
        photo.save(upload_path)

        catalog_writer.add_photo_to_item(content_dir, photos_dir, category, slug, upload_path)
        app.catalog.reload()

        github_token = os.environ.get("HOMEHQ_GITHUB_TOKEN")
        if github_token:
            catalog_writer.git_commit_and_push(
                repo_dir, f"Add photo to catalog item: {item.name}", github_token
            )

        return redirect(url_for("catalog_detail", category=category, slug=slug))

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
        app.recipes.reload()
        return redirect(url_for("pantry"))

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=8502)
