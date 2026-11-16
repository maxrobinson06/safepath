import math
from contextlib import asynccontextmanager
from typing import List, Optional

import networkx as nx
import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from shapely import wkb

# 1. PostGIS Database Configuration (matches load_osm_to_db.py)
DB_CONFIG = {
    "dbname": "safepath",
    "user": "postgres",
    "password": "safepath",  # Update if you used a different PostgreSQL password
    "host": "localhost",
    "port": 5432,
}

# Global graph instance held in memory for fast routing
G = nx.Graph()

# Minimum possible safety multiplier (used to keep the A* heuristic admissible)
MIN_SAFETY_MULTIPLIER = 0.7


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
    Loads street_network geometries and tags from PostGIS and constructs
    a connected NetworkX routing graph.
    """
    global G
    G.clear()

    print("Loading street_network from PostGIS into NetworkX graph...")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Fetch geometry as hex EWKB alongside safety and accessibility attributes
    cur.execute("""
        SELECT id, highway_type, is_lit, has_dropped_kerb, surface, ST_AsEWKB(geom)
        FROM street_network
        WHERE geom IS NOT NULL;
    """)
    rows = cur.fetchall()

    for row in rows:
        edge_id, highway_type, is_lit, has_dropped_kerb, surface, geom_ewkb = row
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

        # 3. SafePath weighting: reward well-lit streets, penalize unlit paths
        lighting_multiplier = MIN_SAFETY_MULTIPLIER if is_lit else 1.6

        # Slightly penalize narrow isolated alleyways/steps at night compared to open streets
        highway_multiplier = 1.25 if highway in {"steps", "path"} else 1.0

        return length * lighting_multiplier * highway_multiplier

    return edge_weight


def astar_heuristic(u: tuple, v: tuple) -> float:
    """
    Admissible geographic heuristic: straight-line distance in meters scaled by
    the minimum possible safety multiplier (0.7) so A* never overestimates cost.
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
    start: List[float]                  # [longitude, latitude]
    end: List[float]                    # [longitude, latitude]
    mode: Optional[str] = "safe"        # "safe" or "shortest"
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

    # 3. Compute route metrics (total distance & % of path that is lit)
    total_distance_m = 0.0
    lit_distance_m = 0.0

    for i in range(len(path_nodes) - 1):
        u, v = path_nodes[i], path_nodes[i + 1]
        edge_data = G.get_edge_data(u, v)
        seg_len = edge_data.get("length", 0.0)
        total_distance_m += seg_len
        if edge_data.get("is_lit", False):
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