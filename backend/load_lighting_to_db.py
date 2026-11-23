import pandas as pd
import psycopg2
from pyproj import Transformer

DB_CONFIG = {
    "dbname": "safepath",
    "user": "postgres",
    "password": "safepath",
    "host": "localhost",
    "port": 5432
}

def load_council_lighting(filepath="data/street_lighting_2025.csv"):
    # Convert British National Grid (EPSG:27700) to WGS84 GPS (EPSG:4326)
    transformer = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)

    df = pd.read_csv(filepath)
    # Drop the 7 rows missing coordinates and keep only street lights ('L')
    df = df.dropna(subset=["Eastings", "Northings"])
    df = df[df["Unit_Type"] == "L"]

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Prepare rows in memory first for fast batch insert
    records = []
    for _, row in df.iterrows():
        lon, lat = transformer.transform(row["Eastings"], row["Northings"])
        records.append((
            str(row["Unit_Id"]),
            str(row["Unit_Type_Description"]),
            str(row["Street"]),
            lon,
            lat
        ))

    cur.executemany("""
        INSERT INTO council_streetlights (unit_id, unit_type, street_name, geom)
        VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
    """, records)

    conn.commit()
    cur.close()
    conn.close()
    print(f"Successfully inserted {len(records)} street lights into PostGIS!")

if __name__ == "__main__":
    load_council_lighting()