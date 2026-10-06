"""Reviewed catalogue references, not activated measurement connectors."""

CATALOGUE = (
    (34655, 46, "Programy monitoringu wód powierzchniowych"),
    (34649, 47, "Programy i punkty monitoringu wód podziemnych"),
    (34653, 47, "Metodyki, oceny i trendy stanu wód podziemnych"),
    (34644, 48, "Hałas: mapy, pomiary i programy ochrony"),
    (34584, 49, "PRTR: uwolnienia i transfery zanieczyszczeń"),
    (34643, 50, "Poważne awarie i zakłady zagrożone awarią"),
)
DATASETS = {dataset: registry for dataset, registry, _ in CATALOGUE}


def catalogue_for_registry(registry_id):
    return [
        {
            "dataset_id": dataset,
            "registry_id": registry,
            "title": title,
            "url": f"https://dane.gov.pl/pl/dataset/{dataset}",
        }
        for dataset, registry, title in CATALOGUE
        if registry_id == 6 or registry_id == registry
    ]
