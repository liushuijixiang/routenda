"use client";

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";

const fallbackRoute = [
  [121.32, 31.2],
  [120.62, 31.3],
  [120.98, 31.38],
];

export default function ItineraryMap({ routePoints = fallbackRoute }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      center: [120.82, 31.3],
      zoom: 8,
      style: {
        version: 8,
        sources: {
          route: {
            type: "geojson",
            data: {
              type: "Feature",
              geometry: { type: "LineString", coordinates: routePoints },
              properties: {},
            },
          },
        },
        layers: [
          {
            id: "background",
            type: "background",
            paint: { "background-color": "#e8f1f2" },
          },
          {
            id: "route-line",
            type: "line",
            source: "route",
            paint: { "line-color": "#0f766e", "line-width": 4 },
          },
        ],
      },
    });

    routePoints.forEach((point) => {
      new maplibregl.Marker({ color: "#0f172a" }).setLngLat(point).addTo(map);
    });

    return () => map.remove();
  }, [routePoints]);

  return (
    <div
      ref={containerRef}
      className="map maplibre-canvas"
      aria-label="行程地图"
    />
  );
}
