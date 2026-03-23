"""
Folium map visualization component for ATM locations and traffic flows.

Displays the NeoBank ATM network on a Bahrain-centered map with markers
colour-coded by location type, optional traffic flow lines for
reallocation scenarios, competitor ATM markers, density heatmap,
and scenario impact overlays.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import folium
from folium.plugins import HeatMap
import streamlit as st
from streamlit_folium import st_folium

from frontend.config import get_bank_name
from frontend.config import (
    MAP_CENTER_LAT,
    MAP_CENTER_LON,
    MAP_DEFAULT_ZOOM,
    MAP_TILE_PROVIDER,
)

logger = logging.getLogger(__name__)

# Colour scheme by location type
LOCATION_COLOURS: dict[str, str] = {
    "branch": "blue",
    "mall": "purple",
    "hospital": "red",
    "airport": "orange",
    "standalone": "green",
}

LOCATION_ICONS: dict[str, str] = {
    "branch": "university",
    "mall": "shopping-cart",
    "hospital": "plus-square",
    "airport": "plane",
    "standalone": "map-marker",
}

# Impact thresholds for scenario colour-coding
IMPACT_THRESHOLDS = {"low": 0.05, "medium": 0.15}

# Default ATM locations (fallback)
DEFAULT_ATM_LOCATIONS: list[dict[str, Any]] = [
    {"atm_id": "ATM_SEEF_01", "name": "Seef - CrediMax Building", "lat": 26.2285, "lon": 50.5280, "type": "branch"},
    {"atm_id": "ATM_AALI_01", "name": "Al Aali Shopping Complex", "lat": 26.2180, "lon": 50.5150, "type": "mall"},
    {"atm_id": "ATM_BMALL_01", "name": "Bahrain Mall", "lat": 26.2230, "lon": 50.4780, "type": "mall"},
    {"atm_id": "ATM_ATRIUM_01", "name": "Atrium Mall", "lat": 26.2100, "lon": 50.5050, "type": "mall"},
    {"atm_id": "ATM_AIRPORT_01", "name": "Bahrain International Airport", "lat": 26.2708, "lon": 50.6336, "type": "airport"},
    {"atm_id": "ATM_SITRA_01", "name": "Sitra Branch", "lat": 26.1540, "lon": 50.6180, "type": "branch"},
    {"atm_id": "ATM_JUFFAIR_01", "name": "Juffair", "lat": 26.2120, "lon": 50.6010, "type": "standalone"},
    {"atm_id": "ATM_HAMAD_01", "name": "Hamad Town", "lat": 26.1150, "lon": 50.4850, "type": "branch"},
    {"atm_id": "ATM_RIFFA_01", "name": "East Riffa", "lat": 26.1300, "lon": 50.5550, "type": "branch"},
    {"atm_id": "ATM_MUHARRAQ_01", "name": "Muharraq Branch", "lat": 26.2570, "lon": 50.6120, "type": "branch"},
    {"atm_id": "ATM_MANAMA_01", "name": "Manama - Head Office", "lat": 26.2235, "lon": 50.5775, "type": "branch"},
    {"atm_id": "ATM_MANAMA_02", "name": "Manama - Government Ave", "lat": 26.2180, "lon": 50.5830, "type": "standalone"},
    {"atm_id": "ATM_GUDAIBIYA_01", "name": "Gudaibiya", "lat": 26.2150, "lon": 50.5680, "type": "branch"},
    {"atm_id": "ATM_ADLIYA_01", "name": "Adliya", "lat": 26.2100, "lon": 50.5520, "type": "standalone"},
    {"atm_id": "ATM_HOORA_01", "name": "Hoora", "lat": 26.2280, "lon": 50.5850, "type": "standalone"},
    {"atm_id": "ATM_SALMANIYA_01", "name": "Salmaniya Medical Complex", "lat": 26.2200, "lon": 50.5600, "type": "hospital"},
    {"atm_id": "ATM_ISA_TOWN_01", "name": "Isa Town", "lat": 26.1730, "lon": 50.5480, "type": "branch"},
    {"atm_id": "ATM_BUDAIYA_01", "name": "Budaiya", "lat": 26.2180, "lon": 50.4500, "type": "standalone"},
    {"atm_id": "ATM_ZINJ_01", "name": "Zinj", "lat": 26.2050, "lon": 50.5450, "type": "standalone"},
    {"atm_id": "ATM_TUBLI_01", "name": "Tubli", "lat": 26.1950, "lon": 50.5350, "type": "standalone"},
    {"atm_id": "ATM_SANABIS_01", "name": "Sanabis", "lat": 26.2350, "lon": 50.5150, "type": "standalone"},
    {"atm_id": "ATM_HIDD_01", "name": "Hidd", "lat": 26.2450, "lon": 50.6500, "type": "standalone"},
    {"atm_id": "ATM_AWALI_01", "name": "Awali", "lat": 26.0780, "lon": 50.5250, "type": "standalone"},
    {"atm_id": "ATM_DURRAT_01", "name": "Durrat Al Bahrain", "lat": 25.9850, "lon": 50.5950, "type": "standalone"},
    {"atm_id": "ATM_AMWAJ_01", "name": "Amwaj Islands", "lat": 26.2850, "lon": 50.6700, "type": "standalone"},
    {"atm_id": "ATM_BAHRAIN_BAY_01", "name": "Bahrain Bay", "lat": 26.2400, "lon": 50.5900, "type": "standalone"},
    {"atm_id": "ATM_CITY_CENTRE_01", "name": "City Centre Bahrain", "lat": 26.2290, "lon": 50.5380, "type": "mall"},
    {"atm_id": "ATM_SEEF_MALL_01", "name": "Seef Mall", "lat": 26.2310, "lon": 50.5200, "type": "mall"},
]


def _create_base_map() -> folium.Map:
    """Create a Bahrain-centered Folium map with English labels."""
    m = folium.Map(
        location=[MAP_CENTER_LAT, MAP_CENTER_LON],
        zoom_start=MAP_DEFAULT_ZOOM,
        tiles=None,
    )
    folium.TileLayer(
        tiles="https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}&hl=en",
        attr="Google",
        name="Google Maps (English)",
        max_zoom=20,
    ).add_to(m)
    return m


def _add_atm_markers(
    m: folium.Map,
    locations: list[dict[str, Any]],
    highlight_atm_id: Optional[str] = None,
) -> folium.FeatureGroup:
    """Add ATM markers to a FeatureGroup on the map."""
    fg = folium.FeatureGroup(name=f"{get_bank_name()} ATMs", show=True)
    for loc in locations:
        atm_id = loc.get("atm_id", "")
        name = loc.get("name", atm_id)
        lat = loc.get("lat", loc.get("latitude", 0))
        lon = loc.get("lon", loc.get("longitude", 0))
        loc_type = loc.get("type", loc.get("location_type", "standalone"))

        colour = LOCATION_COLOURS.get(loc_type, "gray")
        icon_name = LOCATION_ICONS.get(loc_type, "map-marker")

        if highlight_atm_id and atm_id == highlight_atm_id:
            colour = "red"
            icon_name = "exclamation-triangle"

        popup_html = f"<b>{name}</b><br>{atm_id}<br>Type: {loc_type}"

        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=name,
            icon=folium.Icon(color=colour, icon=icon_name, prefix="fa"),
        ).add_to(fg)

    fg.add_to(m)
    return fg


def _add_traffic_flows(
    m: folium.Map,
    source: dict[str, Any],
    targets: list[dict[str, Any]],
) -> None:
    """Draw polylines from a source ATM to traffic reallocation targets."""
    src_lat = source.get("lat", source.get("latitude", 0))
    src_lon = source.get("lon", source.get("longitude", 0))

    for target in targets:
        tgt_lat = target.get("lat", target.get("latitude", 0))
        tgt_lon = target.get("lon", target.get("longitude", 0))
        pct = target.get("traffic_pct", 0)
        weight = max(2, min(8, pct / 10))

        folium.PolyLine(
            locations=[[src_lat, src_lon], [tgt_lat, tgt_lon]],
            color="red",
            weight=weight,
            opacity=0.7,
            tooltip=f"{target.get('name', '')} — {pct:.0f}% traffic",
        ).add_to(m)


def _add_impact_zone(
    m: folium.Map,
    lat: float,
    lon: float,
    radius_km: float = 5.0,
) -> None:
    """Draw a translucent circle showing the impact zone."""
    folium.Circle(
        location=[lat, lon],
        radius=radius_km * 1000,
        color="red",
        fill=True,
        fill_opacity=0.1,
        tooltip=f"Impact zone ({radius_km} km)",
    ).add_to(m)


def _add_competitor_markers(m: folium.Map, competitors: list[dict]) -> folium.FeatureGroup:
    """Add competitor ATM markers in a toggleable FeatureGroup.

    Marker styles by status:
        - active: solid grey marker with bank icon
        - planned: grey marker with dashed outline (DivIcon)
        - closed: grey marker with times icon
    """
    fg = folium.FeatureGroup(name="Competitor ATMs", show=True)

    for comp in competitors:
        lat = comp.get("latitude", 0)
        lon = comp.get("longitude", 0)
        bank = comp.get("bank_name", "Unknown")
        name = comp.get("name", "")
        cid = comp.get("competitor_atm_id", "")
        loc_type = comp.get("location_type", "")
        status = comp.get("status", "active")

        tooltip_text = f"{bank} — {name}"
        popup_html = f"{cid} | {bank} | {name} | {loc_type}"

        if status == "planned":
            icon = folium.DivIcon(
                html=f'<div style="font-size:16px;color:#6b7280;border:2px dashed #6b7280;'
                     f'border-radius:50%;width:24px;height:24px;text-align:center;'
                     f'line-height:20px;background:white;">🏦</div>',
                icon_size=(24, 24),
                icon_anchor=(12, 12),
            )
        elif status == "closed":
            icon = folium.Icon(color="lightgray", icon="times", prefix="fa")
        else:
            icon = folium.Icon(color="gray", icon="university", prefix="fa")

        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=tooltip_text,
            icon=icon,
        ).add_to(fg)

    fg.add_to(m)
    return fg


def _add_competitor_heatmap(m: folium.Map, competitors: list[dict]) -> None:
    """Add Folium HeatMap layer using competitor lat/lon coordinates."""
    fg = folium.FeatureGroup(name="Competitor Density", show=False)

    heat_data = [
        [comp.get("latitude", 0), comp.get("longitude", 0)]
        for comp in competitors
        if comp.get("status", "active") == "active"
    ]

    if heat_data:
        HeatMap(
            heat_data,
            radius=15,
            blur=10,
            max_zoom=13,
            gradient={0.2: "green", 0.5: "yellow", 0.8: "orange", 1.0: "red"},
        ).add_to(fg)

    fg.add_to(m)


def _add_scenario_overlay(m: folium.Map, scenario: dict) -> None:
    """Add scenario visualization overlay.

    Shows simulated competitor ATM with red diamond marker,
    impact radius circle, and colour-coded affected NeoBank markers.
    """
    fg = folium.FeatureGroup(name="Scenario Impact", show=True)

    sim_loc = scenario.get("simulated_location", {})
    lat = sim_loc.get("latitude", 0)
    lon = sim_loc.get("longitude", 0)
    bank = sim_loc.get("bank_name", "Competitor")

    # Red diamond marker for simulated ATM
    folium.Marker(
        location=[lat, lon],
        popup=f"Simulated {bank} ATM",
        tooltip=f"Simulated: {bank}",
        icon=folium.DivIcon(
            html='<div style="font-size:20px;color:#ef4444;text-align:center;">◆</div>',
            icon_size=(24, 24),
            icon_anchor=(12, 12),
        ),
    ).add_to(fg)

    # Impact radius circle
    radius_km = scenario.get("radius_km", 2.0)
    folium.Circle(
        location=[lat, lon],
        radius=radius_km * 1000,
        color="#ef4444",
        fill=True,
        fill_opacity=0.08,
        dash_array="5",
        tooltip=f"Impact radius ({radius_km} km)",
    ).add_to(fg)

    # Colour-coded affected ATM markers
    for atm in scenario.get("affected_atms", []):
        current = atm.get("current_daily_transactions", 1)
        projected = atm.get("projected_daily_transactions", current)
        if current > 0:
            pct_change = abs(projected - current) / current
        else:
            pct_change = 0

        if pct_change < IMPACT_THRESHOLDS["low"]:
            color = "green"
        elif pct_change <= IMPACT_THRESHOLDS["medium"]:
            color = "orange"
        else:
            color = "red"

        rev_change = atm.get("projected_daily_revenue_change", 0)
        folium.CircleMarker(
            location=[0, 0],  # We don't have lat/lon for affected ATMs in scenario result
            radius=8,
            color=color,
            fill=True,
            fill_opacity=0.7,
        )
        # Note: affected ATMs don't have lat/lon in the scenario result,
        # so we skip adding them as markers. The scenario overlay shows
        # the simulated location and impact radius only.

    fg.add_to(m)


def render_map(
    locations: Optional[list[dict[str, Any]]] = None,
    highlight_atm_id: Optional[str] = None,
    traffic_flows: Optional[dict[str, Any]] = None,
    competitor_locations: Optional[list[dict[str, Any]]] = None,
    scenario_result: Optional[dict[str, Any]] = None,
    show_competitor_heatmap: bool = False,
) -> None:
    """Render the ATM network map with optional competitor layers.

    Parameters
    ----------
    locations:
        List of ATM dicts with lat/lon. Falls back to DEFAULT_ATM_LOCATIONS.
    highlight_atm_id:
        ATM ID to highlight.
    traffic_flows:
        Dict with source and targets for reallocation visualisation.
    competitor_locations:
        List of competitor ATM dicts for competitor layer.
    scenario_result:
        Output from simulate_competitor_scenario for overlay.
    show_competitor_heatmap:
        Toggle for competitor density heatmap.
    """
    st.subheader("🗺️ ATM Network Map")

    atm_data = locations or DEFAULT_ATM_LOCATIONS
    m = _create_base_map()

    # Bank ATM markers (always on top via z-index ordering)
    _add_atm_markers(m, atm_data, highlight_atm_id=highlight_atm_id)

    # Competitor layers
    if competitor_locations:
        _add_competitor_markers(m, competitor_locations)
        if show_competitor_heatmap:
            _add_competitor_heatmap(m, competitor_locations)

    # Scenario overlay
    if scenario_result and "simulated_location" in scenario_result:
        _add_scenario_overlay(m, scenario_result)

    # Traffic flows
    if traffic_flows:
        source = traffic_flows.get("source", {})
        targets = traffic_flows.get("targets", [])
        if source:
            _add_traffic_flows(m, source, targets)
            src_lat = source.get("lat", source.get("latitude", 0))
            src_lon = source.get("lon", source.get("longitude", 0))
            _add_impact_zone(m, src_lat, src_lon)

    # Layer control for toggling
    folium.LayerControl(collapsed=False).add_to(m)

    # Legend
    bank_name = get_bank_name()
    legend_html = f"""
    <div style="position:fixed; bottom:30px; left:30px; z-index:1000;
         background:white; padding:10px; border-radius:5px;
         border:1px solid #ccc; font-size:12px; max-width:280px;">
    <b>{bank_name} ATMs</b><br>
    🔵 Branch &nbsp; 🟣 Mall &nbsp; 🔴 Hospital<br>
    🟠 Airport &nbsp; 🟢 Standalone<br>
    <b style="margin-top:4px;display:inline-block;">Competitor ATMs</b><br>
    ⚫ Active &nbsp; ◻ Planned &nbsp; ✕ Closed
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    st_folium(m, width=None, height=500, use_container_width=True)
