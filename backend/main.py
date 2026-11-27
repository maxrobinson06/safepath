import math
from contextlib import asynccontextmanager
from typing import List, Optional

import networkx as nx
import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from shapely import wkb

# 1. PostGIS Database Configuration
DB_CONFIG = {
    "dbname": "safepath",
    "user": "postgres",
    "password": "safepath",  # Update if you used a different PostgreSQL password
    "host": "localhost",
    "port": 5432,
}

# Global graph instance held in memory for fast routing
G = nx.Graph()

# Minimum possible safety multiplier (updated to 0.65 to keep A* heuristic admissible)
MIN_SAFETY_MULTIPLIER = 0.65

# Empirical mean segment length (meters) from Manchester PostGIS dataset
AVG_SEGMENT_LENGTH_M = 43.43


def calculate_lighting_multiplier(length_m: float, lighting_score: float, is_lit_osm: bool) -> float:
    """
    Calculates a safety cost multiplier based on weighted council lighting density
    normalized against the empirical mean segment length (43.43m), falling back to
    OSM is_lit tags for private campus grounds (e.g., UoM / MMU).
    """
    expected_units = max(length_m / AVG_SEGMENT_LENGTH_M, 1.0)
    density = lighting_score / expected_units

    if density >= 1.0:
        return 0.65   # Well-lit corridor (at least 1 full street light per 43.43m)
    elif density >= 0.4:
        return 0.75   # Moderately lit street or bollard-lined path
    elif lighting_score > 0:
        return 0.85   # Partially lit / sparse lighting
    elif is_lit_osm:
        return 0.80   # OSM fallback for private campus lighting
    else:
        return 1.60   # Confirmed unlit in both datasets


def haversine_meters(coord1: tuple, coord2: tuple) -> float:
    """
    Calculate the great-circle distance in meters between two (lng, lat) points.
    """
    lon1, lat1 = coord1
    lon2, lat2 = coord2
    radius_earth = 6371000.0  # meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * radius_earth * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def build_graph_from_postgis():
    """
    Loads street_network geometries, OSM tags, and municipal lighting scores
    from PostGIS and constructs a connected NetworkX routing graph.
    """
    global G
    G.clear()

    print("Loading street_network from PostGIS into NetworkX graph...")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Fetch lighting_score and council_lamp_count alongside existing attributes
    cur.execute("""
        SELECT 
            id, 
            highway_type, 
            is_lit, 
            COALESCE(lighting_score, 0), 
            COALESCE(council_lamp_count, 0),
            has_dropped_kerb, 
            surface, 
            ST_AsEWKB(geom)
        FROM street_network
        WHERE geom IS NOT NULL;
    """)
    rows = cur.fetchall()

    for row in rows:
        (
            edge_id,
            highway_type,
            is_lit,
            lighting_score,
            council_lamp_count,
            has_dropped_kerb,
            surface,
            geom_ewkb,
        ) = row
        line = wkb.loads(bytes(geom_ewkb))

        if line.geom_type != "LineString":
            continue

        coords = list(line.coords)
        # Break each LineString into connected vertex-to-vertex segments
        # Rounding to 6 decimal places (~11cm precision) ensures shared intersections snap together
        for i in range(len(coords) - 1):
            u = (round(coords[i][0], 6), round(coords[i][1], 6))
            v = (round(coords[i + 1][0], 6), round(coords[i + 1][1], 6))

            if u == v:
                continue

            dist = haversine_meters(u, v)

            G.add_edge(
                u,
                v,
                edge_id=edge_id,
                length=dist,
                highway_type=highway_type or "pedestrian",
                is_lit=bool(is_lit),
                lighting_score=float(lighting_score),
                council_lamp_count=int(council_lamp_count),
                has_dropped_kerb=bool(has_dropped_kerb),
                surface=surface or "unknown",
            )

    cur.close()
    conn.close()

    # Keep the largest connected component so clicked pins never snap to isolated "island" paths
    if G.number_of_nodes() > 0:
        largest_cc = max(nx.connected_components(G), key=len)
        G = G.subgraph(largest_cc).copy()

    print(
        f"Graph ready: {G.number_of_nodes()} intersection nodes, "
        f"{G.number_of_edges()} walkable segments."
    )


def snap_to_nearest_node(target_lng: float, target_lat: float) -> tuple:
    """
    Finds the closest graph node to the user's clicked [lng, lat] pin.
    """
    target = (target_lng, target_lat)
    best_node = None
    min_dist = float("inf")

    for node in G.nodes:
        d = haversine_meters(target, node)
        if d < min_dist:
            min_dist = d
            best_node = node

    return best_node


def make_edge_weight_fn(mode: str = "safe", profile: str = "pedestrian"):
    """
    Returns a dynamic cost function for NetworkX A* based on user preferences.
    - mode: 'safe' (prioritizes lighting) or 'shortest' (pure physical distance)
    - profile: 'pedestrian' or 'wheelchair'
    """
    def edge_weight(u, v, data):
        length = data.get("length", 1.0)
        highway = data.get("highway_type", "pedestrian")
        is_lit = data.get("is_lit", False)
        lighting_score = data.get("lighting_score", 0.0)
        surface = data.get("surface", "unknown")

        # 1. Accessibility constraint: Wheelchair users cannot traverse steps
        if profile == "wheelchair":
            if highway == "steps":
                return float("inf")
            if surface in {"cobblestone", "unpaved", "gravel", "dirt", "sand"}:
                length *= 3.0  # Heavy penalty for rough wheelchair surfaces

        # 2. Pure shortest-distance mode (for baseline comparison)
        if mode == "shortest":
            return length

        # 3. SafePath weighting: hybrid council lighting density + OSM fallback
        lighting_multiplier = calculate_lighting_multiplier(length, lighting_score, is_lit)

        # Slightly penalize narrow isolated alleyways/steps at night compared to open streets
        highway_multiplier = 1.25 if highway in {"steps", "path"} else 1.0

        return length * lighting_multiplier * highway_multiplier

    return edge_weight


def astar_heuristic(u: tuple, v: tuple) -> float:
    """
    Admissible geographic heuristic: straight-line distance in meters scaled by
    the minimum possible safety multiplier (0.65) so A* never overestimates cost.
    """
    return haversine_meters(u, v) * MIN_SAFETY_MULTIPLIER


@asynccontextmanager
async def lifespan(app: FastAPI):
    build_graph_from_postgis()
    yield


app = FastAPI(title="SafePath Routing Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RouteRequest(BaseModel):
    start: List[float]                     # [longitude, latitude]
    end: List[float]                       # [longitude, latitude]
    mode: Optional[str] = "safe"           # "safe" or "shortest"
    profile: Optional[str] = "pedestrian"  # "pedestrian" or "wheelchair"


@app.post("/route")
def calculate_route(req: RouteRequest):
    if G.number_of_nodes() == 0:
        raise HTTPException(
            status_code=500,
            detail="Routing graph is empty. Verify PostGIS database connection.",
        )

    # 1. Snap user's Start and Destination pins to nearest street nodes
    start_node = snap_to_nearest_node(req.start[0], req.start[1])
    end_node = snap_to_nearest_node(req.end[0], req.end[1])

    weight_fn = make_edge_weight_fn(mode=req.mode, profile=req.profile)

    # 2. Execute A* Search
    try:
        path_nodes = nx.astar_path(
            G,
            source=start_node,
            target=end_node,
            heuristic=astar_heuristic,
            weight=weight_fn,
        )
    except nx.NetworkXNoPath:
        raise HTTPException(
            status_code=404,
            detail="No valid accessible route found between the selected points.",
        )

    # 3. Compute route metrics (total distance & % of path that is lit by either source)
    total_distance_m = 0.0
    lit_distance_m = 0.0

    for i in range(len(path_nodes) - 1):
        u, v = path_nodes[i], path_nodes[i + 1]
        edge_data = G.get_edge_data(u, v)
        seg_len = edge_data.get("length", 0.0)
        total_distance_m += seg_len

        if edge_data.get("lighting_score", 0.0) > 0 or edge_data.get("is_lit", False):
            lit_distance_m += seg_len

    lit_percentage = (
        round((lit_distance_m / total_distance_m) * 100, 1)
        if total_distance_m > 0
        else 0.0
    )

    # Convert node tuples [(lng, lat), ...] into GeoJSON coordinate arrays
    route_coords = [[node[0], node[1]] for node in path_nodes]

    return {
        "status": "success",
        "route": {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": route_coords,
            },
            "properties": {
                "distance_meters": round(total_distance_m, 1),
                "lit_percentage": lit_percentage,
                "mode": req.mode,
                "profile": req.profile,
                "node_count": len(route_coords),
            },
        },
    }
@app.get("/streetlights")
def get_streetlights():
    """
    Returns council street lighting points within the Manchester testbed area
    as a GeoJSON FeatureCollection for Mapbox heatmap rendering.
    """
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Filter to the wider Manchester City Centre & Oxford Road corridor bounding box
    cur.execute("""
        SELECT 
            c.unit_type,
            ST_X(c.geom) AS lng,
            ST_Y(c.geom) AS lat
        FROM council_streetlights c
        WHERE c.geom && (
            SELECT ST_Extent(geom)::geometry 
            FROM street_network
        );
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    features = []
    for unit_type, lng, lat in rows:
        # Match the exact illumination weights used in the A* routing engine
        if unit_type in ("STREET LIGHT", "HIGH MAST UNIT", "FLOODLIGHT"):
            weight = 1.0
        elif unit_type in ("AMENITY LIGHT", "SUBWAY LIGHT", "WALL MOUNTED"):
            weight = 0.8
        elif unit_type in ("BOLLARD", "Solar Bollard"):
            weight = 0.4
        else:
            weight = 0.5

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lng, lat],
            },
            "properties": {
                "weight": weight,
                "unit_type": unit_type,
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
    }