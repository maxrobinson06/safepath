-- 1. Enable PostGIS spatial capabilities
CREATE EXTENSION IF NOT EXISTS postgis;

-- 2. Verify PostGIS is installed and running
SELECT PostGIS_Version();

-- 3. Create table for Manchester pedestrian network edges
CREATE TABLE IF NOT EXISTS street_network (
    id SERIAL PRIMARY KEY,
    osm_id BIGINT,
    highway_type VARCHAR(50),
    is_lit BOOLEAN DEFAULT FALSE,
    has_dropped_kerb BOOLEAN DEFAULT FALSE,
    surface VARCHAR(50),
    geom GEOMETRY(LineString, 4326)
);

-- 4. Create spatial index for fast routing queries
CREATE INDEX IF NOT EXISTS idx_street_network_geom 
ON street_network USING GIST (geom);