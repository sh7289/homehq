import os
import re
import shutil
import subprocess
import tempfile

import yaml

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(name):
    slug = _SLUG_STRIP_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "item"


def _unique_path(directory, slug, ext):
    os.makedirs(directory, exist_ok=True)
    candidate = slug
    n = 2
    while os.path.exists(os.path.join(directory, f"{candidate}{ext}")):
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


def write_catalog_item(
    content_dir, photos_dir, category, name, frontmatter, body, source_image_path=None
):
    """Write a new catalog markdown file (+ copy a source photo, if given).

    Returns (markdown_path, photo_path_relative_to_photos_dir_or_None).
    """
    category_dir = os.path.join(content_dir, category)
    slug = _unique_path(category_dir, slugify(name), ".md")

    photo_rel_path = None
    if source_image_path:
        ext = os.path.splitext(source_image_path)[1] or ".jpg"
        photo_category_dir = os.path.join(photos_dir, category)
        photo_slug = _unique_path(photo_category_dir, f"{slug}-1", ext)
        photo_rel_path = f"{category}/{photo_slug}{ext}"
        shutil.copyfile(source_image_path, os.path.join(photos_dir, photo_rel_path))

    full_frontmatter = {"name": name, "category": category}
    full_frontmatter.update(frontmatter)
    if photo_rel_path:
        full_frontmatter["photos"] = [photo_rel_path]

    markdown_path = os.path.join(category_dir, f"{slug}.md")
    with open(markdown_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        yaml.safe_dump(full_frontmatter, f, sort_keys=False)
        f.write("---\n")
        f.write(body or "")
        f.write("\n")

    return markdown_path, photo_rel_path


def _read_frontmatter_and_body(md_path):
    text = open(md_path, encoding="utf-8").read()
    _, raw_frontmatter, body = text.split("---", 2)
    frontmatter = yaml.safe_load(raw_frontmatter) or {}
    return frontmatter, body.strip()


def add_photo_to_item(content_dir, photos_dir, category, slug, source_image_path):
    """Append a photo to an already-written catalog item. Returns the new
    photo's path relative to photos_dir."""
    md_path = os.path.join(content_dir, category, f"{slug}.md")
    frontmatter, body = _read_frontmatter_and_body(md_path)

    existing_photos = list(frontmatter.get("photos") or [])
    ext = os.path.splitext(source_image_path)[1] or ".jpg"
    photo_category_dir = os.path.join(photos_dir, category)
    photo_slug = _unique_path(photo_category_dir, f"{slug}-{len(existing_photos) + 1}", ext)
    photo_rel_path = f"{category}/{photo_slug}{ext}"
    shutil.copyfile(source_image_path, os.path.join(photos_dir, photo_rel_path))

    frontmatter["photos"] = existing_photos + [photo_rel_path]

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        yaml.safe_dump(frontmatter, f, sort_keys=False)
        f.write("---\n")
        f.write(body)
        f.write("\n")

    return photo_rel_path


def _make_askpass_script(token):
    fd, path = tempfile.mkstemp(prefix="homehq-askpass-")
    with os.fdopen(fd, "w") as f:
        f.write(f'#!/bin/sh\necho "{token}"\n')
    os.chmod(path, 0o700)
    return path


def git_commit_and_push(repo_dir, message, github_token, branch="main", runner=subprocess.run):
    """Stage everything, commit, and push using a write-scoped token.

    The token is passed via GIT_ASKPASS (a short-lived helper script) rather
    than embedded in argv or the remote URL, so it doesn't leak through the
    process list or get written into .git/config.
    """
    runner(["git", "add", "-A"], cwd=repo_dir, check=True)
    runner(["git", "commit", "-m", message], cwd=repo_dir, check=True)

    askpass_path = _make_askpass_script(github_token)
    try:
        env = dict(os.environ)
        env["GIT_ASKPASS"] = askpass_path
        runner(
            [
                "git",
                "-c",
                "credential.username=x-access-token",
                "push",
                "origin",
                f"HEAD:{branch}",
            ],
            cwd=repo_dir,
            check=True,
            env=env,
        )
    finally:
        os.remove(askpass_path)
