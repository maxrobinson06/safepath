import json
import psycopg2
from shapely.geometry import shape

DB_CONFIG = {
    "dbname": "safepath",
    "user": "postgres",
    "password": "safepath",
    "host": "localhost",
    "port": 5432
}

def clean_and_load_geojson(filepath="data/manchester_pedestrian.geojson"):
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    inserted = 0
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        geom_raw = feature.get("geometry")

        if not geom_raw:
            continue

        geom = shape(geom_raw)

        # 1. Clean Geometry: Extract boundary if a pedestrian area/polygon was captured
        if geom.geom_type == "Polygon":
            line_geom = geom.boundary
        elif geom.geom_type == "LineString":
            line_geom = geom
        else:
            continue  # Ignore Points, MultiPolygons, etc.

        # 2. Extract and sanitize relevant fields
        osm_id = props.get("id") or props.get("@id", "").replace("way/", "")
        highway = props.get("highway", "pedestrian")
        is_lit = props.get("lit") == "yes"
        has_dropped_kerb = props.get("kerb") in ["lowered", "flush"]
        surface = props.get("surface", "unknown")

        cur.execute("""
            INSERT INTO street_network (osm_id, highway_type, is_lit, has_dropped_kerb, surface, geom)
            VALUES (%s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))
        """, (int(osm_id) if str(osm_id).isdigit() else None, highway, is_lit, has_dropped_kerb, surface, line_geom.wkt))
        
        inserted += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Successfully cleaned and inserted {inserted} walkable paths into PostGIS!")

if __name__ == "__main__":
    clean_and_load_geojson()