"""Sources described as configuration (plan U5, 2026-11-13).

`sources/sources.yaml` lists groups of RSS and HTML-listing sources. Each group
names the old `configure_*` command it mirrors. Parsers keep their selectors in
code and recognise a source by its exact (adapter, url, config), so the YAML must
reproduce those values; tests compare them with the old commands.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml
from django.conf import settings

CATALOGUE = Path(settings.BASE_DIR) / "sources" / "sources.yaml"
ADAPTERS = {"feed", "web"}
GROUP_KEYS = {"command", "note", "defaults", "sources"}
SOURCE_KEYS = {
    "registry",
    "slug",
    "name",
    "adapter",
    "url",
    "config",
    "interval_minutes",
    "language",
    "scope",
    "replaces",
    "registry_requires",
    "registry_state",
    "reuse_notes",
}
# Fields a `replaces` item may pin; every listed field must match exactly.
REPLACE_KEYS = {"adapter", "url", "config", "interval_minutes", "scope"}


FROM_REGISTRY = "registry"


class CatalogueError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSpec:
    group: str
    registry: int
    slug: str
    name: str
    adapter: str
    url: str
    config: dict
    interval_minutes: int | str
    language: str = "pl"
    scope: str = ""
    replaces: tuple = ()
    registry_requires: tuple = ()
    registry_state: str = ""
    # None: copy "Reuse / licence" from the registry entry on create.
    reuse_notes: str | None = None

    @property
    def identity(self):
        return (self.adapter, self.url, self.config)


def load(path=None):
    with open(path or CATALOGUE, encoding="utf-8") as handle:
        try:
            data = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise CatalogueError(f"YAML syntax: {exc}") from exc
    return parse(data)


def parse(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise CatalogueError("Catalogue must be a mapping with version: 1")
    groups = data.get("groups")
    if not isinstance(groups, dict) or not groups:
        raise CatalogueError("Catalogue has no groups")
    specs, slugs = [], set()
    for group, body in groups.items():
        if not isinstance(body, dict) or set(body) - GROUP_KEYS:
            raise CatalogueError(f"{group}: unknown group keys")
        defaults = body.get("defaults") or {}
        if set(defaults) - SOURCE_KEYS:
            raise CatalogueError(f"{group}: unknown default keys")
        for raw in body.get("sources") or []:
            if not isinstance(raw, dict) or set(raw) - SOURCE_KEYS:
                raise CatalogueError(f"{group}: unknown source keys in {raw!r}")
            spec = build(group, {**defaults, **raw})
            if spec.slug in slugs:
                raise CatalogueError(f"{spec.slug}: listed twice")
            slugs.add(spec.slug)
            specs.append(spec)
    return specs


def build(group, item):
    registry = item.get("registry")
    if type(registry) is not int or registry < 1:
        raise CatalogueError(f"{group}: registry id must be a positive integer")
    slug = item.get("slug") or f"registry-{registry:03d}"
    adapter, url, config = item.get("adapter"), item.get("url"), item.get("config", {})
    if adapter not in ADAPTERS:
        raise CatalogueError(f"{slug}: adapter must be one of {sorted(ADAPTERS)}")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise CatalogueError(f"{slug}: url must be https")
    if not isinstance(config, dict):
        raise CatalogueError(f"{slug}: config must be a mapping")
    interval = item.get("interval_minutes")
    # "registry": take the workbook poll interval when the source is created.
    if interval != FROM_REGISTRY and (type(interval) is not int or not 15 <= interval <= 10080):
        raise CatalogueError(f"{slug}: interval_minutes must be 15..10080 or registry")
    language = item.get("language", "pl")
    if language not in {"pl", "en", FROM_REGISTRY}:
        raise CatalogueError(f"{slug}: language must be pl, en or registry")
    replaces = item.get("replaces") or []
    for previous in replaces:
        if not isinstance(previous, dict) or not previous or set(previous) - REPLACE_KEYS:
            raise CatalogueError(f"{slug}: invalid replaces item")
    return SourceSpec(
        group=group,
        registry=registry,
        slug=slug,
        name=item.get("name") or "{organization}",
        adapter=adapter,
        url=url,
        config=config,
        interval_minutes=interval,
        language=language,
        scope=item.get("scope", ""),
        replaces=tuple(replaces),
        registry_requires=tuple(item.get("registry_requires") or ()),
        registry_state=item.get("registry_state", ""),
        reuse_notes=item.get("reuse_notes"),
    )
