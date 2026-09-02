from content_loader import load_catalog


class CatalogStore:
    """In-memory catalog index, rebuilt via reload().

    Safe to reload at runtime because the app runs a single gunicorn worker
    (see deploy notes) -- there's no cross-worker cache-coherency problem.
    """

    def __init__(self, content_dir):
        self._content_dir = content_dir
        self._catalog = {}

    def reload(self):
        self._catalog = load_catalog(self._content_dir)

    def categories(self):
        return sorted(self._catalog.keys())

    def items(self, category):
        return self._catalog.get(category, [])

    def all_items(self):
        return dict(self._catalog)

    def get(self, category, slug):
        for item in self.items(category):
            if item.slug == slug:
                return item
        return None
