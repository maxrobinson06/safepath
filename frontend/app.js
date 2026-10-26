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