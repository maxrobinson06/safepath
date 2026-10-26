from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

app = FastAPI(title="SafePath Routing Engine")

# Allow your frontend HTML file to talk to FastAPI without CORS errors
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RouteRequest(BaseModel):
    start: List[float]  # [longitude, latitude]
    end: List[float]    # [longitude, latitude]

@app.post("/route")
def calculate_route(req: RouteRequest):

    # GeoJSON LineString format expected by Mapbox
    return {
        "status": "success",
        "route": {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    req.start,
                    req.end
                ]
            },
        }
    }