"""Bounded RDLP identity manifest, without geometry or persistent collection."""

import hashlib
import json

from ingestion.bdl_catalogue import ORIGIN
from ingestion.bdl_probe import LICENSE, read, validate_api
from ingestion.bdl_probe import MAX_BYTES as API_MAX_BYTES
from ingestion.transport import FetchError

URL = ORIGIN + "/collections/rdlp/items?f=json&limit=100&skipGeometry=true"
MAX_BYTES = 256 * 1024
MAX_FEATURES = 100


def plan():
    return {
        "collection": "rdlp",
        "url": URL,
        "max_response_bytes": MAX_BYTES,
        "max_api_response_bytes": API_MAX_BYTES,
        "max_features": MAX_FEATURES,
        "database_writes": False,
        "automatic_collection_enabled": False,
        "geometry_downloaded": False,
        "full_dataset_coverage": False,
        "provider_snapshot_consistency_verified": False,
        "fetched": False,
    }


def fetch_manifest(client):
    client.max_bytes = min(client.max_bytes, API_MAX_BYTES)
    validate_api(client)
    client.max_bytes = min(client.max_bytes, MAX_BYTES)
    data, response = read(client, URL)
    if len(response.body) > MAX_BYTES:
        raise FetchError("BDL manifest response exceeds budget")
    features = data.get("features")
    matched, returned = data.get("numberMatched"), data.get("numberReturned")
    if (
        data.get("type") != "FeatureCollection"
        or not isinstance(features, list)
        or type(matched) is not int
        or not 1 <= matched <= MAX_FEATURES
        or type(returned) is not int
        or returned != matched
        or len(features) != matched
    ):
        raise FetchError("BDL manifest incomplete or count changed")
    links = data.get("links")
    if not isinstance(links, list) or any(not isinstance(link, dict) for link in links):
        raise FetchError("BDL manifest links missing")
    self_links = [link for link in links if link.get("rel") == "self"]
    if (
        len(self_links) != 1
        or self_links[0].get("href") != URL
        or any(link.get("rel") == "next" for link in links)
    ):
        raise FetchError("BDL manifest identity or pagination changed")
    identities = []
    for feature in features:
        if not isinstance(feature, dict):
            raise FetchError("BDL manifest feature required")
        identity, properties = feature.get("id"), feature.get("properties")
        if (
            feature.get("type") != "Feature"
            or "geometry" not in feature
            or feature["geometry"] is not None
            or type(identity) is not int
            or not 1 <= identity <= 1000000
            or not isinstance(properties, dict)
            or (
                "id" in properties
                and (properties["id"] != identity or type(properties["id"]) is not int)
            )
            or type(properties.get("a_year")) is not int
            or not 1900 <= properties["a_year"] <= 2100
            or not isinstance(properties.get("region_cd"), str)
            or not 1 <= len(properties["region_cd"]) <= 32
            or not isinstance(properties.get("region_name"), str)
            or not 1 <= len(properties["region_name"]) <= 200
        ):
            raise FetchError("BDL manifest feature identity or properties changed")
        identities.append({"id": identity, "source_year": properties["a_year"]})
    ids = [item["id"] for item in identities]
    if len(set(ids)) != len(ids):
        raise FetchError("BDL manifest duplicate identity")
    identities.sort(key=lambda item: item["id"])
    scope_hash = hashlib.sha256(
        json.dumps(identities, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        **plan(),
        "fetched": True,
        "http": response.status,
        "robots": response.access_policy,
        "declared_api_license": LICENSE,
        "provider_number_matched": matched,
        "number_returned": returned,
        "identity_manifest": identities,
        "scope_sha256": scope_hash,
        "response_sha256": hashlib.sha256(response.body).hexdigest(),
        "response_bytes": len(response.body),
        "all_manifest_ids_received": True,
        "source_publication_time_known": False,
        "large_geometry_policy": "on_demand_only",
    }
