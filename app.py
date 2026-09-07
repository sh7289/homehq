import os
import re
import uuid
from datetime import date, timedelta

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
    session,
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
from markupsafe import Markup, escape
from werkzeug.middleware.proxy_fix import ProxyFix

import ai_extract
import catalog_writer
import catalog_stats
import db
import export_csv
import expiry
import matching
import recipe_loader
import recipe_scale
import recipe_steps
import recipe_writer
import sections
from catalog_store import CatalogStore
from recipe_store import RecipeStore


# How many unsorted items the bulk section-assignment form offers at once.
# The backfilled pantry has ~106 unsorted rows; showing them all would render
# every item twice and make the page unusable on a phone.
SECTION_SORTER_BATCH = 15


def _batch_summary_message(staged_count, failed_count):
    """Build the "N ready; M failed" line shown after an upload redirect.

    Kept as a pure function (no db/request access) so the wording is easy to
    reason about and test on its own.
    """
    parts = []
    if staged_count:
        parts.append(f"{staged_count} item{'' if staged_count == 1 else 's'} ready to review")
    if failed_count:
        parts.append(
            f"{failed_count} photo{'' if failed_count == 1 else 's'} could not be read"
        )
    return "; ".join(parts) + "." if parts else None


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
    # Matches `client_max_body_size 10M` in deploy/nginx-homehq.conf. Without
    # this Flask accepts an unbounded upload; keeping the two in step means a
    # rejection is explainable rather than a bare nginx 413.
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
    # Without this a login cookie never expires server-side, so a stolen one
    # is good forever.
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)

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
    # "strong" ties the session to the client's identity and drops it when
    # that changes, which limits what a lifted cookie is worth.
    login_manager.session_protection = "strong"
    login_manager.init_app(app)
    app.login_manager = login_manager

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

        `**bold**` becomes <strong> -- the recipe importer writes it and
        people paste it. Everything is HTML-escaped *first*, so the only tags
        that survive are the ones added here.
        """
        paragraphs = []
        for block in re.split(r"\n\s*\n", (text or "").strip()):
            if not block.strip():
                continue
            escaped = str(escape(" ".join(block.split())))
            paragraphs.append(
                Markup(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped))
            )
        return paragraphs

    @app.context_processor
    def inject_nav():
        if current_user.is_authenticated:
            return {"nav_categories": app.catalog.categories()}
        return {"nav_categories": []}

    @app.errorhandler(413)
    def upload_too_large(error):
        return (
            render_template(
                "import_upload.html",
                error="Those photos are too large (10 MB total). Try fewer at a time.",
                active="import",
            ),
            413,
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "").encode("utf-8")

            # Throttle per username, deliberately NOT per source address:
            # both household users share a home IP, so an address-based
            # lockout would mean one person's typos lock the other out. With
            # only two valid usernames there is nothing to enumerate, so
            # per-username throttling is what actually protects the password.
            identifier = f"user:{username}"
            conn = get_db()
            wait = db.lockout_remaining(conn, identifier)
            if wait:
                app.logger.warning(
                    "Login blocked by lockout for %r from %s", username, request.remote_addr
                )
                return (
                    render_template(
                        "login.html",
                        error=(
                            "Too many failed attempts. Try again in "
                            f"{wait // 60 + 1} minute(s)."
                        ),
                    ),
                    429,
                )

            password_hash = users.get(username)
            if password_hash and bcrypt.checkpw(password, password_hash.encode("utf-8")):
                db.clear_login_failures(conn, identifier)
                # Opt into PERMANENT_SESSION_LIFETIME; without this the cookie
                # is a session cookie with no server-side expiry at all.
                session.permanent = True
                login_user(User(username))
                return redirect(url_for("home"))

            db.record_login_failure(conn, identifier)
            # Never log the submitted password.
            app.logger.warning(
                "Failed login for %r from %s", username, request.remote_addr
            )
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
            "home.html",
            categories=app.catalog.categories(),
            stats=stats,
            recipe_count=len(app.recipes.all()),
            active="home",
        )

    # The four summary views offered above the inventory rows. "all" is the
    # default and is never put in the query string (a bare /pantry means
    # "all"); the other three key straight into expiry.split_by_expiry()'s
    # two buckets and the existing "no section" check, so filtering never
    # reimplements that logic.
    INVENTORY_FILTERS = ("all", "expired", "expiring_soon", "unsorted")

    def _render_inventory_page(
        storage,
        query=None,
        filter_key=None,
        add_error=None,
        add_values=None,
        edit_error=None,
        edit_values=None,
    ):
        if query is None:
            query = request.args.get("q", "")
        query = query.strip()
        query_lc = query.lower()

        if filter_key is None:
            filter_key = request.args.get("filter", "all")
        if filter_key not in INVENTORY_FILTERS:
            filter_key = "all"

        raw_items = db.list_items(get_db(), storage=storage)
        is_storage_empty = not raw_items

        items = (
            [item for item in raw_items if query_lc in item["name"].lower()]
            if query_lc
            else raw_items
        )
        # Mutates every dict in `items` in place (effective_expiry,
        # effective_expiry_estimated, expiry_status) -- expired/expiring_soon
        # below share those same objects, as does unsectioned_all, so the
        # rows keep their expiry markers no matter which filter is shown.
        expired, expiring_soon = expiry.split_by_expiry(items, storage=storage)
        unsectioned_all = [item for item in items if not item.get("section")]

        filtered_items = {
            "all": items,
            "expired": expired,
            "expiring_soon": expiring_soon,
            "unsorted": unsectioned_all,
        }[filter_key]

        return render_template(
            "inventory.html",
            storage=storage,
            user=current_user.id,
            items=items,
            groups=sections.group_items(filtered_items, storage),
            all_sections=sections.sections_for(storage),
            unsectioned=unsectioned_all[:SECTION_SORTER_BATCH],
            unsectioned_total=len(unsectioned_all),
            expired_count=len(expired),
            expiring_soon_count=len(expiring_soon),
            is_storage_empty=is_storage_empty,
            query=query,
            active_filter=filter_key,
            add_error=add_error,
            add_values=add_values or {},
            edit_error=edit_error or {},
            edit_values=edit_values or {},
            active=storage,
        )

    def _inventory_redirect(storage, query="", filter_key="all", item_id=None):
        """Redirect back to a storage page, preserving search/filter state.

        Restores `q` and `filter` as query params (when not their defaults)
        and, when acting on a single row, appends a `#row-N` fragment so the
        browser scrolls back to it -- this is what keeps an adjust/edit/
        delete from dropping the user back on the unfiltered top of the list.
        """
        kwargs = {}
        if query:
            kwargs["q"] = query
        if filter_key and filter_key != "all":
            kwargs["filter"] = filter_key
        anchor = f"row-{item_id}" if item_id is not None else None
        return redirect(url_for(storage, _anchor=anchor, **kwargs))

    def _add_inventory_item(storage):
        name = request.form.get("name", "").strip()
        quantity_raw = request.form.get("quantity", "").strip()
        query = request.form.get("q", "")
        filter_key = request.form.get("filter") or "all"

        error = None
        quantity = None
        if not name:
            error = "Give the item a name."
        elif not quantity_raw:
            error = "Enter a quantity."
        else:
            try:
                quantity = float(quantity_raw)
            except ValueError:
                error = "Quantity must be a number."

        shelf_life_days = None
        if not error:
            shelf_life_raw = request.form.get("shelf_life_days", "").strip()
            if shelf_life_raw:
                try:
                    shelf_life_days = int(shelf_life_raw)
                except ValueError:
                    error = "Shelf life override must be a whole number of days."

        if error:
            return _render_inventory_page(
                storage,
                query=query,
                filter_key=filter_key,
                add_error=error,
                add_values=request.form,
            )

        db.add_item(
            get_db(),
            name=name,
            quantity=quantity,
            unit=request.form.get("unit", "").strip(),
            location=request.form.get("location", "").strip(),
            storage=storage,
            expiry_date=request.form.get("expiry_date", "").strip() or None,
            acquired_date=request.form.get("acquired_date", "").strip() or None,
            shelf_life_days=shelf_life_days,
            section=sections.normalize(storage, request.form.get("section")),
        )
        return _inventory_redirect(storage, query=query, filter_key=filter_key)

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

    def _redirect_to_storage_page(item_id=None):
        storage = request.form.get("storage") or "pantry"
        if storage not in ("pantry", "freezer"):
            storage = "pantry"
        return _inventory_redirect(
            storage,
            query=request.form.get("q", "").strip(),
            filter_key=request.form.get("filter") or "all",
            item_id=item_id,
        )

    @app.route("/inventory/<int:item_id>/adjust", methods=["POST"])
    @login_required
    def inventory_adjust(item_id):
        db.adjust_quantity(get_db(), item_id, delta=float(request.form["delta"]))
        return _redirect_to_storage_page(item_id=item_id)

    @app.route("/inventory/<int:item_id>/delete", methods=["POST"])
    @login_required
    def inventory_delete(item_id):
        db.delete_item(get_db(), item_id)
        return _redirect_to_storage_page(item_id=item_id)

    @app.route("/inventory/<int:item_id>/update", methods=["POST"])
    @login_required
    def inventory_update(item_id):
        storage = request.form.get("storage") or "pantry"
        if storage not in ("pantry", "freezer"):
            storage = "pantry"
        query = request.form.get("q", "")
        filter_key = request.form.get("filter") or "all"

        quantity_raw = request.form.get("quantity", "").strip()
        error = None
        quantity = None
        if not quantity_raw:
            error = "Enter a quantity."
        else:
            try:
                quantity = float(quantity_raw)
            except ValueError:
                error = "Quantity must be a number."

        shelf_life_days = None
        if not error:
            shelf_life_raw = request.form.get("shelf_life_days", "").strip()
            if shelf_life_raw:
                try:
                    shelf_life_days = int(shelf_life_raw)
                except ValueError:
                    error = "Shelf life override must be a whole number of days."

        if error:
            return _render_inventory_page(
                storage,
                query=query,
                filter_key=filter_key,
                edit_error={"item_id": item_id, "message": error},
                edit_values=request.form,
            )

        db.update_item(
            get_db(),
            item_id,
            quantity=quantity,
            unit=request.form.get("unit", "").strip(),
            location=request.form.get("location", "").strip(),
            expiry_date=request.form.get("expiry_date", "").strip() or None,
            acquired_date=request.form.get("acquired_date", "").strip() or None,
            shelf_life_days=shelf_life_days,
            section=sections.normalize(storage, request.form.get("section")),
        )
        return _redirect_to_storage_page(item_id=item_id)

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
        conn = get_db()
        pending = db.list_staging_items(conn, status="pending")
        failed = db.list_staging_items(conn, status="failed")
        suggestions = {}
        for staging_item in pending:
            if staging_item["target_type"] == "inventory":
                candidates = db.list_items(
                    conn, storage=staging_item.get("storage") or "pantry"
                )
                suggestions[staging_item["id"]] = matching.find_best_match(
                    staging_item["name"], candidates
                )

        # ?batch=<id> is set only on the redirect right after an upload (the
        # same one-shot query-param pattern as push_failed below) -- it scopes
        # the summary line to *that* request instead of every pending/failed
        # row that happens to exist, and it's read from the database here
        # rather than trusted from the query string, so the counts always
        # reflect what actually got persisted.
        batch_id = request.args.get("batch")
        batch_summary = None
        if batch_id:
            batch_failed = [item for item in failed if item.get("batch_id") == batch_id]
            staged_count = sum(1 for item in pending if item.get("batch_id") == batch_id)
            message = _batch_summary_message(staged_count, len(batch_failed))
            if message:
                batch_summary = {"message": message, "had_failures": bool(batch_failed)}

        return render_template(
            "import_review.html",
            items=pending,
            failed_items=failed,
            suggestions=suggestions,
            categories=app.catalog.categories(),
            section_choices={
                "pantry": sections.sections_for("pantry"),
                "freezer": sections.sections_for("freezer"),
            },
            push_failed=request.args.get("push_failed"),
            batch_summary=batch_summary,
            active="import",
        )

    @app.route("/import/upload", methods=["GET", "POST"])
    @login_required
    def import_upload():
        if request.method == "POST":
            photos = [p for p in request.files.getlist("photo") if p and p.filename]
            if not photos:
                return render_template("import_upload.html", error="Choose a photo first.")

            try:
                api_key = os.environ["HOMEHQ_ANTHROPIC_API_KEY"]
            except KeyError:
                return render_template(
                    "import_upload.html",
                    error="AI import isn't configured yet -- set HOMEHQ_ANTHROPIC_API_KEY.",
                )

            # One id ties every row this request produces -- staged or
            # failed -- together, so the review page can report on *this*
            # upload specifically after the redirect (see import_review).
            batch_id = uuid.uuid4().hex
            staged = 0
            failures = []
            for photo in photos:
                ext = os.path.splitext(photo.filename)[1] or ".jpg"
                upload_path = os.path.join(uploads_dir, f"{uuid.uuid4().hex}{ext}")
                photo.save(upload_path)
                media_type = photo.mimetype or "image/jpeg"

                try:
                    with open(upload_path, "rb") as f:
                        rows = ai_extract.extract_from_image(
                            f.read(), media_type, api_key=api_key
                        )
                except ai_extract.ExtractionError as exc:
                    # One unreadable shelf photo shouldn't discard the rest of
                    # the batch -- record it and carry on. This is persisted
                    # as a 'failed' staging row (not just kept in `failures`
                    # for this request) so it survives the redirect and can
                    # be retried without re-uploading -- the file is already
                    # saved at upload_path.
                    failures.append(f"{photo.filename}: {exc}")
                    db.add_staging_item(
                        get_db(),
                        target_type="failed",
                        name=photo.filename,
                        status="failed",
                        error=str(exc),
                        media_type=media_type,
                        source_image_path=upload_path,
                        batch_id=batch_id,
                    )
                    continue

                for row in rows:
                    db.add_staging_item(
                        get_db(), source_image_path=upload_path, batch_id=batch_id, **row
                    )
                    staged += 1

            if not staged:
                return render_template(
                    "import_upload.html",
                    error="; ".join(failures) or "Nothing could be read from those photos.",
                )
            return redirect(url_for("import_review", batch=batch_id))

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
        category = request.args.get("category") or None
        favorites_only = request.args.get("favorites") == "on"
        matches = app.recipes.filter(
            kind=kind,
            category=category,
            max_effort=int(max_effort) if max_effort else None,
            favorites_only=favorites_only,
        )
        groups = {}
        for recipe in matches:
            groups.setdefault(recipe.kind, []).append(recipe)
        return render_template(
            "recipes.html",
            groups=[{"kind": k, "recipes": groups[k]} for k in sorted(groups)],
            kinds=app.recipes.kinds(),
            categories=app.recipes.categories(),
            selected_kind=kind,
            selected_category=category,
            selected_effort=max_effort,
            favorites_only=favorites_only,
            total=len(app.recipes.all()),
            active="recipes",
        )

    @app.route("/recipes/paste", methods=["GET", "POST"])
    @login_required
    def recipe_paste():
        """Paste a recipe from anywhere and get the new-recipe form prefilled.

        Deliberately hands off to the existing form rather than writing a file:
        the model will get some of it wrong, and correcting it before it is
        saved is easier than correcting it afterwards.
        """
        if request.method != "POST":
            return render_template("recipe_paste.html", error=None, text="", active="recipes")

        text = request.form.get("text", "").strip()
        if not text:
            return render_template(
                "recipe_paste.html",
                error="Paste a recipe first.",
                text="",
                active="recipes",
            )

        try:
            api_key = os.environ["HOMEHQ_ANTHROPIC_API_KEY"]
        except KeyError:
            return render_template(
                "recipe_paste.html",
                error="AI import isn't configured yet -- set HOMEHQ_ANTHROPIC_API_KEY.",
                text=text,
                active="recipes",
            )

        try:
            parsed = ai_extract.extract_recipe(text, api_key=api_key)
        except ai_extract.ExtractionError as exc:
            return render_template(
                "recipe_paste.html", error=str(exc), text=text, active="recipes"
            )

        if not parsed["ingredients"]:
            return render_template(
                "recipe_paste.html",
                error="That didn't look like a recipe -- no ingredients found in it.",
                text=text,
                active="recipes",
            )

        return render_template(
            "recipe_form.html",
            error=None,
            form={
                "name": parsed["name"],
                "kind": parsed["kind"],
                "serves": parsed["serves"] or "",
                "ingredients": recipe_writer.ingredients_to_lines(parsed["ingredients"]),
                "body": parsed["method"],
            },
            from_paste=True,
            ingredient_help=_INGREDIENT_HELP,
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

    def _render_ingredient_editor(recipe, lines, error=None, suggested=False):
        return render_template(
            "recipe_ingredients.html",
            recipe=recipe,
            lines=lines,
            error=error,
            suggested=suggested,
            ingredient_help=_INGREDIENT_HELP,
            active="recipes",
        )

    @app.route("/recipes/<slug>/edit", methods=["GET", "POST"])
    @login_required
    def recipe_edit(slug):
        recipe = app.recipes.get(slug)
        if recipe is None:
            abort(404)

        if request.method == "POST":
            effort = request.form.get("effort", "").strip()
            serves = request.form.get("serves", "").strip()
            frontmatter = dict(recipe.frontmatter)
            frontmatter.update(
                {
                    "kind": request.form.get("kind") or "meal",
                    "category": request.form.get("category", "").strip() or None,
                    "cuisine": request.form.get("cuisine", "").strip() or None,
                    # An unchecked checkbox sends nothing, so absence is false
                    # rather than "leave it as it was".
                    "favorite": request.form.get("favorite") == "on",
                    "effort": int(effort) if effort else None,
                    "serves": int(serves) if serves else None,
                }
            )
            recipe_writer.update_recipe(
                recipes_dir,
                slug,
                name=request.form.get("name", "").strip() or recipe.name,
                frontmatter=frontmatter,
                body=request.form.get("body", "").strip(),
            )
            app.recipes.reload()
            return redirect(url_for("recipe_detail", slug=slug))

        return render_template("recipe_edit.html", recipe=recipe, active="recipes")

    @app.route("/recipes/<slug>/ingredients", methods=["GET", "POST"])
    @login_required
    def recipe_ingredients(slug):
        recipe = app.recipes.get(slug)
        if recipe is None:
            abort(404)

        if request.method == "POST":
            recipe_writer.set_ingredients(
                recipes_dir,
                slug,
                _parse_ingredient_lines(request.form.get("ingredients")),
            )
            app.recipes.reload()
            return redirect(url_for("recipe_detail", slug=slug))

        return _render_ingredient_editor(
            recipe, recipe_writer.ingredients_to_lines(recipe.ingredients)
        )

    def _steps_to_lines(steps):
        """Render steps as `id | action | input, input` for the editor."""
        return "\n".join(
            f"{s['id']} | {s['action']} | {', '.join(s['inputs'])}" for s in steps or []
        )

    def _parse_step_lines(text):
        steps = []
        for index, line in enumerate((text or "").splitlines(), start=1):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split("|")]
            steps.append(
                {
                    "id": parts[0] or f"s{index}",
                    "action": parts[1] if len(parts) > 1 else "",
                    "inputs": [
                        i.strip()
                        for i in (parts[2].split(",") if len(parts) > 2 else [])
                        if i.strip()
                    ],
                }
            )
        return recipe_loader.normalize_steps(steps)

    def _render_step_editor(recipe, lines, error=None, suggested=False):
        return render_template(
            "recipe_steps.html",
            recipe=recipe,
            lines=lines,
            error=error,
            suggested=suggested,
            active="recipes",
        )

    @app.route("/recipes/<slug>/steps", methods=["GET", "POST"])
    @login_required
    def recipe_steps_edit(slug):
        recipe = app.recipes.get(slug)
        if recipe is None:
            abort(404)

        if request.method == "POST":
            recipe_writer.set_steps(
                recipes_dir, slug, _parse_step_lines(request.form.get("steps"))
            )
            app.recipes.reload()
            return redirect(url_for("recipe_detail", slug=slug))

        return _render_step_editor(recipe, _steps_to_lines(recipe.steps))

    @app.route("/recipes/<slug>/suggest-steps", methods=["POST"])
    @login_required
    def recipe_suggest_steps(slug):
        recipe = app.recipes.get(slug)
        if recipe is None:
            abort(404)

        current = _steps_to_lines(recipe.steps)
        try:
            api_key = os.environ["HOMEHQ_ANTHROPIC_API_KEY"]
        except KeyError:
            return _render_step_editor(
                recipe,
                current,
                error="AI generation isn't configured yet -- set HOMEHQ_ANTHROPIC_API_KEY.",
            )

        try:
            proposed = ai_extract.extract_steps(
                recipe.name, recipe.ingredients, recipe.body, api_key=api_key
            )
        except ai_extract.ExtractionError as exc:
            return _render_step_editor(recipe, current, error=str(exc))

        return _render_step_editor(recipe, _steps_to_lines(proposed), suggested=True)

    @app.route("/recipes/<slug>/suggest-ingredients", methods=["POST"])
    @login_required
    def recipe_suggest_ingredients(slug):
        """Propose ingredients from the recipe's own notes.

        Deliberately does not write anything -- the proposal lands in the
        editable textarea and the human commits it.
        """
        recipe = app.recipes.get(slug)
        if recipe is None:
            abort(404)

        current = recipe_writer.ingredients_to_lines(recipe.ingredients)
        try:
            api_key = os.environ["HOMEHQ_ANTHROPIC_API_KEY"]
        except KeyError:
            return _render_ingredient_editor(
                recipe,
                current,
                error="AI extraction isn't configured yet -- set HOMEHQ_ANTHROPIC_API_KEY.",
            )

        try:
            proposed = ai_extract.extract_ingredients(
                recipe.name, recipe.body, api_key=api_key
            )
        except ai_extract.ExtractionError as exc:
            return _render_ingredient_editor(recipe, current, error=str(exc))

        return _render_ingredient_editor(
            recipe, recipe_writer.ingredients_to_lines(proposed), suggested=True
        )

    @app.route("/recipes/<slug>")
    @login_required
    def recipe_detail(slug):
        recipe = app.recipes.get(slug)
        if recipe is None:
            abort(404)

        base_serves = recipe.frontmatter.get("serves")
        target = request.args.get("serves")
        factor = recipe_scale.factor_for(base_serves, target)
        # Scaling is a view concern: the stored file never changes.
        ingredients = (
            recipe_scale.scale(recipe.ingredients, factor)
            if factor != 1
            else recipe.ingredients
        )
        shown_serves = (
            int(float(target)) if factor != 1 and target else base_serves
        )
        view = request.args.get("view")
        return render_template(
            "recipe_detail.html",
            recipe=recipe,
            ingredients=ingredients,
            view=view,
            table=recipe_steps.build_table(ingredients, recipe.steps)
            if view == "table"
            else None,
            method=recipe_steps.method_lines(ingredients, recipe.steps),
            base_serves=base_serves,
            shown_serves=shown_serves,
            scaled=factor != 1,
            scale_choices=[1, 2, 4, 6, 8],
            active="recipes",
        )

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

        if not rows:
            # Capture only understands groceries. Pasting a recipe here would
            # otherwise redirect to an empty review list and look like the app
            # simply did nothing.
            return render_template(
                "capture.html",
                error=(
                    "Couldn't find any pantry or freezer items in that. If you "
                    "were adding a recipe, paste it into Recipes instead."
                ),
                text=text,
                active="capture",
            )

        for row in rows:
            db.add_staging_item(get_db(), **row)
        return redirect(url_for("import_review"))

    @app.route("/import/<int:item_id>/approve", methods=["POST"])
    @login_required
    def import_approve(item_id):
        staging_item = db.get_staging_item(get_db(), item_id)
        if staging_item is None or staging_item["target_type"] not in ("inventory", "catalog"):
            # A 'failed' staging row (an unreadable photo, not an item) has
            # no approve action -- only Retry/Discard on the review page.
            abort(404)

        push_failed = False

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
                try:
                    catalog_writer.git_commit_and_push(
                        repo_dir, f"Add catalog item: {field('name')}", github_token
                    )
                except catalog_writer.PushFailed as exc:
                    # The file is written and committed locally. Failing the
                    # whole approve here would strand the staging row, and
                    # re-approving would write a duplicate file.
                    app.logger.warning("Catalog push failed: %s", exc)
                    push_failed = True

        db.delete_staging_item(get_db(), item_id)
        return redirect(url_for("import_review", push_failed=1 if push_failed else None))

    @app.route("/import/<int:item_id>/reject", methods=["POST"])
    @login_required
    def import_reject(item_id):
        db.delete_staging_item(get_db(), item_id)
        return redirect(url_for("import_review"))

    @app.route("/import/<int:item_id>/retry", methods=["POST"])
    @login_required
    def import_retry(item_id):
        """Re-run extraction for one failed photo, without touching the rest
        of the batch it came from.

        The photo is already saved on disk (source_image_path), so this
        never asks for a re-upload. The status flip to 'retrying' below is
        the "server-side batch identity" guard: it claims the row before
        doing any work, so a duplicate click (or a slow first request still
        in flight) sees status != 'failed' and no-ops instead of staging the
        same items twice.
        """
        conn = get_db()
        failed_item = db.get_staging_item(conn, item_id)
        if failed_item is None:
            abort(404)
        if failed_item["status"] != "failed":
            return redirect(url_for("import_review"))
        db.set_staging_item_status(conn, item_id, "retrying")

        def _fail_again(message):
            db.update_staging_item(conn, item_id, error=message)
            db.set_staging_item_status(conn, item_id, "failed")
            return redirect(url_for("import_review"))

        try:
            try:
                api_key = os.environ["HOMEHQ_ANTHROPIC_API_KEY"]
            except KeyError:
                return _fail_again(
                    "AI import isn't configured yet -- set HOMEHQ_ANTHROPIC_API_KEY."
                )

            upload_path = failed_item.get("source_image_path")
            if not upload_path or not os.path.exists(upload_path):
                return _fail_again(
                    "That photo is no longer available on the server -- upload it again."
                )

            media_type = failed_item.get("media_type") or "image/jpeg"
            try:
                with open(upload_path, "rb") as f:
                    rows = ai_extract.extract_from_image(f.read(), media_type, api_key=api_key)
            except ai_extract.ExtractionError as exc:
                return _fail_again(str(exc))

            if not rows:
                return _fail_again("Still nothing readable in that photo.")

            for row in rows:
                db.add_staging_item(
                    conn,
                    source_image_path=upload_path,
                    batch_id=failed_item.get("batch_id"),
                    **row,
                )
            db.delete_staging_item(conn, item_id)
            return redirect(url_for("import_review"))
        except Exception:
            # Whatever else went wrong (a bug, a raw network exception
            # ai_extract didn't wrap), the row must not be left stranded in
            # 'retrying' -- neither pending nor failed, invisible on the
            # review page and unretryable. Restore the original failure
            # message and let the 500 propagate like it would anywhere else
            # in this app.
            db.set_staging_item_status(conn, item_id, "failed")
            raise

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

    @app.route("/catalog/<category>/<slug>/delete", methods=["POST"])
    @login_required
    def catalog_delete(category, slug):
        item = app.catalog.get(category, slug)
        if item is None:
            abort(404)

        try:
            catalog_writer.delete_catalog_item(
                content_dir, photos_dir, category, slug, photos=item.photos
            )
        except (ValueError, FileNotFoundError):
            abort(404)

        app.catalog.reload()

        github_token = os.environ.get("HOMEHQ_GITHUB_TOKEN")
        if github_token:
            try:
                catalog_writer.git_commit_and_push(
                    repo_dir, f"Remove catalog item: {item.name}", github_token
                )
            except catalog_writer.PushFailed as exc:
                # The file is gone locally either way; a failed push must not
                # make the deletion look like it failed.
                app.logger.warning("Catalog push failed after delete: %s", exc)

        return redirect(url_for("catalog_list", category=category))

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
