mapboxgl.accessToken = MAPBOX_TOKEN;

const map = new mapboxgl.Map({
    container: 'map',
    style: 'mapbox://styles/mapbox/dark-v11',
    center: [-2.2426, 53.4808], // Manchester
    zoom: 13
});
map.addControl(new mapboxgl.NavigationControl(), 'top-right');

let startPoint = null;
let endPoint = null;
let startMarker = null;
let endMarker = null;

const startDisplay = document.getElementById('start-coords');
const endDisplay = document.getElementById('end-coords');
const routeBtn = document.getElementById('route-btn');
const resetBtn = document.getElementById('reset-btn');

// Click listener to set Start and End points
map.on('click', (e) => {
    const coords = [e.lngLat.lng, e.lngLat.lat];

    if (!startPoint) {
        startPoint = coords;
        startMarker = new mapboxgl.Marker({ color: '#22c55e' }).setLngLat(startPoint).addTo(map);
        startDisplay.textContent = `${coords[1].toFixed(4)}, ${coords[0].toFixed(4)}`;
    } else if (!endPoint) {
        endPoint = coords;
        endMarker = new mapboxgl.Marker({ color: '#ef4444' }).setLngLat(endPoint).addTo(map);
        endDisplay.textContent = `${coords[1].toFixed(4)}, ${coords[0].toFixed(4)}`;
        routeBtn.disabled = false;
    }
});

// Call Python FastAPI when "Calculate Safe Route" is clicked
routeBtn.addEventListener('click', async () => {
    if (!startPoint || !endPoint) return;

    try {
        const response = await fetch('http://localhost:8000/route', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ start: startPoint, end: endPoint })
        });
        const data = await response.json();
        const routeGeoJSON = data.route;
        const props = routeGeoJSON.properties;
        document.getElementById('route-distance').textContent = `${props.distance_meters} m`;
        document.getElementById('route-lit').textContent = `${props.lit_percentage}%`;

        // Render or update route line on Mapbox
        if (map.getSource('safe-route')) {
            map.getSource('safe-route').setData(routeGeoJSON);
        } else {
            map.addSource('safe-route', { type: 'geojson', data: routeGeoJSON });
            map.addLayer({
                id: 'safe-route',
                type: 'line',
                source: 'safe-route',
                layout: { 'line-join': 'round', 'line-cap': 'round' },
                paint: {
                    'line-color': '#38bdf8',
                    'line-width': 5
                }
            });
        }
    } catch (err) {
        console.error("Failed to connect to FastAPI backend:", err);
        alert("Make sure your FastAPI server is running on port 8000!");
    }
});

// Reset points
resetBtn.addEventListener('click', () => {
    if (startMarker) startMarker.remove();
    if (endMarker) endMarker.remove();
    startPoint = null;
    endPoint = null;
    startMarker = null;
    endMarker = null;
    startDisplay.textContent = 'Not selected';
    endDisplay.textContent = 'Not selected';
    routeBtn.disabled = true;

    if (map.getLayer('safe-route')) map.removeLayer('safe-route');
    if (map.getSource('safe-route')) map.removeSource('safe-route');
});

// --- Street Lighting Heatmap Layer ---
let heatmapVisible = false;
const heatmapBtn = document.getElementById('heatmap-btn');

async function loadLightingHeatmap() {
    try {
        const res = await fetch('http://localhost:8000/streetlights');
        const geojsonData = await res.json();

        map.addSource('streetlights', {
            type: 'geojson',
            data: geojsonData
        });

        map.addLayer({
            id: 'lighting-heatmap',
            type: 'heatmap',
            source: 'streetlights',
            maxzoom: 17,
            layout: {
                visibility: 'none'
            },
            paint: {
                'heatmap-weight': ['get', 'weight'],
                'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 11, 0.25, 14, 0.7, 16, 1.3],
                'heatmap-color': [
                    'interpolate',
                    ['linear'],
                    ['heatmap-density'],
                    0, 'rgba(0, 0, 0, 0)',
                    0.25, 'rgba(30, 58, 138, 0.25)',
                    0.55, 'rgba(217, 119, 6, 0.50)',
                    0.85, 'rgba(251, 191, 36, 0.70)',
                    1.0, 'rgba(254, 240, 138, 0.85)'
                ],
                'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 11, 3, 13, 6, 16, 16],
                'heatmap-opacity': 0.65
            }
        });
    } catch (err) {
        console.error('Failed to load street lighting heatmap:', err);
    }
}

map.on('load', () => {
    loadLightingHeatmap();
});

heatmapBtn.addEventListener('click', () => {
    if (!map.getLayer('lighting-heatmap')) return;

    heatmapVisible = !heatmapVisible;
    map.setLayoutProperty(
        'lighting-heatmap',
        'visibility',
        heatmapVisible ? 'visible' : 'none'
    );
    heatmapBtn.textContent = heatmapVisible
        ? 'Hide Street Lighting Heatmap'
        : 'Show Street Lighting Heatmap';
});