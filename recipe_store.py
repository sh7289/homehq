"""In-memory recipe index, reloaded on demand.

Same justification as CatalogStore: gunicorn runs a single worker, so one
process holds the cache and `/reload` keeps it coherent.
"""

import recipe_loader


class RecipeStore:
    def __init__(self, recipes_dir):
        self.recipes_dir = recipes_dir
        self._by_slug = {}

    def reload(self):
        self._by_slug = {
            recipe.slug: recipe for recipe in recipe_loader.load_recipes(self.recipes_dir)
        }

    def all(self):
        return sorted(self._by_slug.values(), key=lambda r: r.name.lower())

    def get(self, slug):
        return self._by_slug.get(slug)

    def favorites(self):
        return [recipe for recipe in self.all() if recipe.is_favorite]

    def kinds(self):
        return sorted({recipe.kind for recipe in self._by_slug.values()})

    def cuisines(self):
        return sorted(
            {recipe.cuisine for recipe in self._by_slug.values() if recipe.cuisine}
        )

    def filter(self, kind=None, cuisine=None, max_effort=None, favorites_only=False):
        results = self.all()
        if kind:
            results = [r for r in results if r.kind == kind]
        if cuisine:
            results = [r for r in results if r.cuisine == cuisine]
        if max_effort is not None:
            # A recipe with no effort recorded is not excluded by an effort
            # filter -- absence of data isn't evidence of difficulty.
            results = [r for r in results if r.effort is None or r.effort <= max_effort]
        if favorites_only:
            results = [r for r in results if r.is_favorite]
        return results

    def group_by_kind(self):
        """[{kind, recipes}] in alphabetical kind order, non-empty only."""
        buckets = {}
        for recipe in self.all():
            buckets.setdefault(recipe.kind, []).append(recipe)
        return [
            {"kind": kind, "recipes": buckets[kind]} for kind in sorted(buckets)
        ]
