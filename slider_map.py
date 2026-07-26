"""
Interactive County Siting Readiness Visualization Tool

This Streamlit app loads county-level indicator scores and displays an interactive,
binned choropleth map of the **County Siting Readiness Index**.

Interpretation of the index:
    - lower score  = higher readiness
    - higher score = lower readiness / greater deployment constraint

Usage:
    streamlit run slider_map.py

Requirements:
    pip install streamlit pandas geopandas plotly requests shapely
"""

import streamlit as st
import pandas as pd
import geopandas as gpd
import plotly.graph_objects as go
from plotly.colors import get_colorscale, sample_colorscale
import requests
import json
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
from typing import Dict, List
import numpy as np
import os
import hashlib
import tempfile
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Raw CSV column name for the optional Community Context Layer.
COMMUNITY_COLUMN = "Community Context PCT"

# Main indicators (all raw CSV columns except the Community Context Layer).
MAIN_INDICATORS: List[str] = [
    "State Project Enablement Index PCT",
    "Interconnection Queue",
    "Relevant Workforce Availability",
    "Land Cost",
    "Long-Haul Fiber Optics Presence",
    "Extreme Events (Wildfires, Floodings, Storms) PCT",
    "Water Availability",
    "Sequestration Access (EOR/Pipeline/Primacy)",
]

# Every raw indicator column that participates in the index.
RAW_COLUMNS: List[str] = [COMMUNITY_COLUMN] + MAIN_INDICATORS

# Clean UI labels (paper terminology) mapped from the faithful raw CSV columns.
# Backend keeps the raw CSV column names; only the UI shows these clean names.
DISPLAY_NAMES: Dict[str, str] = {
    "Community Context PCT": "Community Context Layer",
    "State Project Enablement Index PCT": "State Project Enablement",
    "Interconnection Queue": "Interconnection Queue",
    "Relevant Workforce Availability": "Labor Availability",
    "Land Cost": "Land Cost",
    "Long-Haul Fiber Optics Presence": "Long-Haul Fiber Optic",
    "Extreme Events (Wildfires, Floodings, Storms) PCT": "Extreme Events",
    "Water Availability": "Water Availability",
    "Sequestration Access (EOR/Pipeline/Primacy)": "Sequestration Access",
}

# Compact indicator labels used in the paper-style exported map annotation.
SHORT_NAMES: Dict[str, str] = {
    "Community Context PCT": "Commu. Cont.",
    "State Project Enablement Index PCT": "State Proj.",
    "Interconnection Queue": "Int. Queue",
    "Relevant Workforce Availability": "Rel. Work. Avail.",
    "Land Cost": "Land Cost",
    "Long-Haul Fiber Optics Presence": "LHFO",
    "Extreme Events (Wildfires, Floodings, Storms) PCT": "Extr. Events",
    "Water Availability": "Wat. Avail.",
    "Sequestration Access (EOR/Pipeline/Primacy)": "Seq. Access",
}

# The nine indicators are grouped into three categories. The ordering here also
# drives the order of the weight sliders in the UI.
CATEGORIES: Dict[str, List[str]] = {
    "Execution and Community Risk": [
        "Community Context PCT",
        "State Project Enablement Index PCT",
    ],
    "Speed-to-Deploy Enablers": [
        "Interconnection Queue",
        "Relevant Workforce Availability",
        "Land Cost",
        "Long-Haul Fiber Optics Presence",
    ],
    "Operational Risk and Resource Resilience": [
        "Extreme Events (Wildfires, Floodings, Storms) PCT",
        "Water Availability",
        "Sequestration Access (EOR/Pipeline/Primacy)",
    ],
}

# Flat, category-ordered list of every indicator that has a weight slider.
INDICATOR_ORDER: List[str] = [c for cols in CATEGORIES.values() for c in cols]

CATEGORY_DESCRIPTIONS: Dict[str, str] = {
    "Execution and Community Risk": (
        "Captures the likelihood of siting friction and permitting delay. Includes "
        "the Community Context Layer and State Project Enablement, which represents "
        "the broader policy and institutional climate for project execution — the "
        "extent to which a state is generally conducive to large infrastructure "
        "development."
    ),
    "Speed-to-Deploy Enablers": (
        "Captures whether required inputs and build capabilities are already present. "
        "Interconnection Queue Performance (compiled at the ISO level and mapped to "
        "counties) is a schedule-risk proxy for how quickly large generation and load "
        "can reach an interconnection agreement. Labor Availability represents local "
        "workforce depth for construction and operations, and Land Cost proxies "
        "site-acquisition friction and the practicality of assembling large parcels "
        "for both NGCC+CCS and geologic storage. Long-Haul Fiber Optics Presence is a "
        "data-center-specific enabling layer, since robust backbone connectivity "
        "reduces development friction; absence of fiber does not imply infeasibility "
        "but raises expected build-out requirements and timelines."
    ),
    "Operational Risk and Resource Resilience": (
        "Captures the ability to operate reliably over time. Extreme Events exposure "
        "(wildfire, flooding, and major storms) matters because both NGCC+CCS and "
        "data centers are highly sensitive to outages; the index favors counties with "
        "lower hazard exposure. Water Availability / Stress is included because "
        "cooling and capture-related operations can be constrained by drought and "
        "competing uses, so higher water stress is penalized. Carbon Sequestration "
        "Access is a CCS-specific enabling factor reflecting nearby sequestration "
        "opportunities and supporting policy maturity, and incorporates proximity to "
        "EOR-linked infrastructure and EOR potential as long-run optionality for "
        "captured CO₂ — treated as a secondary advantage rather than a substitute for "
        "secure storage."
    ),
}

# Three default paper scenarios. Weights are integer points; the percentage share
# of each indicator is its points divided by the total. The paper's per-scenario
# percentages are recovered exactly because the points sum to 36 (1 point ≈ 2.78%).
# The Community Context Layer is 0 in every default scenario — the user can raise
# its points and choose how to interpret it.
SCENARIOS: Dict[str, Dict[str, float]] = {
    "Storage-First Readiness": {
        "Community Context PCT": 0,
        "State Project Enablement Index PCT": 6,
        "Interconnection Queue": 7,
        "Relevant Workforce Availability": 2,
        "Land Cost": 3,
        "Long-Haul Fiber Optics Presence": 1,
        "Extreme Events (Wildfires, Floodings, Storms) PCT": 4,
        "Water Availability": 5,
        "Sequestration Access (EOR/Pipeline/Primacy)": 8,
    },
    "Grid-Speed Readiness": {
        "Community Context PCT": 0,
        "State Project Enablement Index PCT": 7,
        "Interconnection Queue": 8,
        "Relevant Workforce Availability": 5,
        "Land Cost": 4,
        "Long-Haul Fiber Optics Presence": 6,
        "Extreme Events (Wildfires, Floodings, Storms) PCT": 1,
        "Water Availability": 2,
        "Sequestration Access (EOR/Pipeline/Primacy)": 3,
    },
    "Policy-and-Permitting Readiness": {
        "Community Context PCT": 0,
        "State Project Enablement Index PCT": 8,
        "Interconnection Queue": 7,
        "Relevant Workforce Availability": 4,
        "Land Cost": 3,
        "Long-Haul Fiber Optics Presence": 5,
        "Extreme Events (Wildfires, Floodings, Storms) PCT": 1,
        "Water Availability": 2,
        "Sequestration Access (EOR/Pipeline/Primacy)": 6,
    },
}

DEFAULT_SCENARIO = "Storage-First Readiness"
SCENARIO_ORDER = list(SCENARIOS.keys())

# Community Context Layer interpretation. The layer is a normal weighted column;
# this control only changes how its value is read.
COMMUNITY_SOCIAL = "Social Vulnerability perspective"
COMMUNITY_ECONOMIC = "Economic Development Need perspective"
COMMUNITY_OPTIONS = [COMMUNITY_SOCIAL, COMMUNITY_ECONOMIC]

COMMUNITY_DESCRIPTION = (
    "Social vulnerability can be interpreted in two directions. It may identify "
    "communities requiring additional safeguards and engagement, or communities with "
    "greater economic development need. Set its weight to zero to ignore it, or give "
    "it a weight and choose the interpretation below."
)

# Color scales. Default is green→red (green = higher readiness, red = lower
# readiness); colorblind-friendly alternatives are also offered.
COLOR_SCALES: Dict[str, str] = {
    "Green–Yellow–Red (Readiness)": "RdYlGn_r",
    "Cividis (colorblind-safe)": "Cividis",
    "Viridis (colorblind-safe)": "Viridis",
    "Blues (colorblind-safe)": "Blues",
    "YlOrBr (colorblind-safe)": "YlOrBr",
    "Plasma": "Plasma",
}
DEFAULT_PALETTE = "Green–Yellow–Red (Readiness)"

# Score display modes.
DISPLAY_MODES = ["Binned", "Continuous"]

# Fixed bin-size options.
BIN_SIZE_OPTIONS = [0.1, 0.2, 0.25, 0.5]
DEFAULT_BIN_SIZE = 0.2
BIN_COUNT_OPTIONS = [4, 5, 10]
DEFAULT_BIN_COUNT = 5

READINESS_LEGEND_TITLE = "<i>Lower = higher readiness</i><br> <br> "


# ---------------------------------------------------------------------------
# Geometry loading / caching (unchanged core machinery)
# ---------------------------------------------------------------------------
@st.cache_data
def load_counties_geometry() -> gpd.GeoDataFrame:
    """Download and cache US Census county boundaries (GEOID as 5-digit string)."""
    url = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip"

    st.info("Downloading US Census county boundaries...")
    response = requests.get(url)
    response.raise_for_status()

    with ZipFile(BytesIO(response.content)) as zip_file:
        shp_files = [f for f in zip_file.namelist() if f.endswith('.shp')]
        if not shp_files:
            raise ValueError("No shapefile found in downloaded zip")

        temp_dir = Path("temp_counties")
        temp_dir.mkdir(exist_ok=True)
        zip_file.extractall(temp_dir)
        shp_path = temp_dir / shp_files[0]
        counties = gpd.read_file(shp_path)

    counties['GEOID'] = counties['GEOID'].astype(str).str.zfill(5)
    counties = counties.to_crs("EPSG:5070")

    st.success(f"Loaded {len(counties)} counties")
    return counties


@st.cache_data
def build_geojson(include_territories: bool) -> tuple:
    """Build and cache county GeoJSON + metadata for Plotly."""
    counties_gdf = load_counties_geometry()
    # Always exclude Puerto Rico (no data for that area)
    counties_gdf = counties_gdf[~counties_gdf['STATEFP'].isin(['72'])]
    if not include_territories:
        counties_gdf = counties_gdf[~counties_gdf['STATEFP'].isin(['02', '15'])]
    counties_latlon = counties_gdf.to_crs("EPSG:4326")
    geojson_dict = json.loads(counties_latlon.to_json())
    counties_meta = counties_latlon[['GEOID', 'NAME']].copy()
    return geojson_dict, counties_meta


@st.cache_data
def build_clipped_geojson(mask_bytes: bytes, include_territories: bool) -> tuple:
    """Clip county polygons to a CO2 storage mask (true geometric intersection)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "mask.zip")
        with open(zip_path, "wb") as f:
            f.write(mask_bytes)
        with ZipFile(zip_path) as z:
            shps = [m for m in z.namelist() if m.lower().endswith(".shp")]
            if not shps:
                raise ValueError("No .shp file found inside the ZIP")
            z.extractall(tmpdir)
        mask_gdf = gpd.read_file(os.path.join(tmpdir, shps[0]))
    if mask_gdf.crs is None:
        mask_gdf = mask_gdf.set_crs(4326)
    mask_gdf = mask_gdf.to_crs("EPSG:5070")

    counties_gdf = load_counties_geometry()  # already in EPSG:5070
    counties_gdf = counties_gdf[~counties_gdf['STATEFP'].isin(['72'])]
    if not include_territories:
        counties_gdf = counties_gdf[~counties_gdf['STATEFP'].isin(['02', '15'])]

    clipped = gpd.overlay(
        counties_gdf[['GEOID', 'NAME', 'geometry']],
        mask_gdf[['geometry']].dissolve(),
        how='intersection'
    )
    clipped = clipped.dissolve(by='GEOID').reset_index()
    name_map = counties_gdf.set_index('GEOID')['NAME']
    clipped['NAME'] = clipped['GEOID'].map(name_map)

    clipped_latlon = clipped.to_crs("EPSG:4326")
    geojson_dict = json.loads(clipped_latlon.to_json())
    counties_meta = clipped_latlon[['GEOID', 'NAME']].copy()
    return geojson_dict, counties_meta


# ---------------------------------------------------------------------------
# Data loading & scoring
# ---------------------------------------------------------------------------
@st.cache_data
def load_scores_csv(file_path, fill_na_value: float = 0.5) -> pd.DataFrame:
    """Load the scores CSV, normalize GEOID, and fill missing indicator values."""
    df = pd.read_csv(file_path)

    if 'FIPS' in df.columns:
        df['GEOID'] = df['FIPS'].astype(str).str.zfill(5)
    elif 'GEOID' in df.columns:
        df['GEOID'] = df['GEOID'].astype(str).str.zfill(5)
    else:
        raise ValueError("CSV must have 'FIPS' or 'GEOID' column")

    missing_cols = [col for col in RAW_COLUMNS if col not in df.columns]
    if missing_cols:
        st.warning(f"Missing columns in CSV: {missing_cols}")

    for col in RAW_COLUMNS:
        if col in df.columns:
            missing_count = df[col].isna().sum()
            if missing_count > 0:
                st.info(f"Filled {missing_count} missing values in '{col}' with {fill_na_value}")
                df[col] = df[col].fillna(fill_na_value)

    return df


def compute_index(
    df: pd.DataFrame,
    weights: Dict[str, float],
    community_mode: str,
    normalize_weights: bool = True,
) -> pd.DataFrame:
    """
    Compute the weighted County Siting Readiness Index.

    Interpretation: lower = higher readiness, higher = lower readiness / greater
    deployment constraint.

    Community Context Layer handling:
        - "Social Vulnerability perspective": raw value used as-is (higher social
          vulnerability increases the index -> lower readiness / greater need for
          safeguards).
        - "Economic Development Need perspective": value reversed via 1 - score
          (higher social vulnerability interpreted as greater economic development
          need / higher priority).

    The Community Context Layer is a normal weighted column: set its weight to 0 to
    ignore it. Active weights are normalized to sum to 1 before scoring.
    """
    df_result = df.copy()
    cols = [c for c in RAW_COLUMNS if c in df.columns]

    values = df_result[cols].astype(float).copy()
    if COMMUNITY_COLUMN in values.columns and community_mode == COMMUNITY_ECONOMIC:
        values[COMMUNITY_COLUMN] = 1.0 - values[COMMUNITY_COLUMN]

    w = np.array([float(weights.get(c, 0.0)) for c in cols], dtype=float)

    if normalize_weights and w.sum() > 0:
        w = w / w.sum()

    composite = values.values.dot(w)
    df_result['composite_score'] = composite

    c_min, c_max = composite.min(), composite.max()
    if c_max > c_min:
        df_result['composite_normalized'] = (composite - c_min) / (c_max - c_min)
    else:
        df_result['composite_normalized'] = np.zeros_like(composite)

    # Percentile rank of the raw score (0 = highest readiness, 100 = lowest).
    df_result['composite_percentile'] = (
        pd.Series(composite).rank(method='average', pct=True).values * 100.0
    )

    return df_result


# ---------------------------------------------------------------------------
# Binning helpers
# ---------------------------------------------------------------------------
def _fmt_edge(x: float) -> str:
    """Format a bin edge like 0.0, 0.2, 0.25."""
    s = f"{x:.2f}".rstrip('0').rstrip('.')
    if '.' not in s:
        s += '.0'
    return s


def compute_bin_edges(
    values: np.ndarray,
    bin_mode: str,
    bin_size: float,
    n_bins: int,
    quantile: bool,
    vmin: float = 0.0,
    vmax: float = 1.0,
) -> List[float]:
    """
    Return bin edges over the value range [vmin, vmax].

    Score-based bins are used by default. If ``quantile`` is True, edges follow the
    empirical quantiles of ``values`` (clamped to the [vmin, vmax] display range).
    """
    span = vmax - vmin
    if span <= 0:
        return [vmin, vmin + 1e-9]

    if bin_mode == "Fixed bin size":
        n = max(1, int(round(span / bin_size)))
    else:
        n = int(n_bins)

    if quantile and values.size > 0:
        qs = np.linspace(0.0, 1.0, n + 1)
        edges = np.quantile(values, qs)
        edges[0], edges[-1] = vmin, vmax
        edges = np.maximum.accumulate(edges)
        edges = np.unique(edges)
        if len(edges) < 2:
            edges = np.array([vmin, vmax])
        return [float(e) for e in edges]

    if bin_mode == "Fixed bin size":
        edges = list(np.arange(vmin, vmax + 1e-9, bin_size))
        if edges[-1] < vmax - 1e-9:
            edges.append(vmax)
        edges[-1] = vmax
    else:
        edges = list(np.linspace(vmin, vmax, n + 1))

    return [float(e) for e in edges]


def make_bin_labels(edges: List[float]) -> List[str]:
    """Human-readable numeric bin labels (first = highest readiness, last = lowest)."""
    n = len(edges) - 1
    labels = []
    for i in range(n):
        labels.append(f"{_fmt_edge(edges[i])}\u2013{_fmt_edge(edges[i + 1])}")
    return labels


def _resolve_scale(name: str) -> List:
    """Resolve a named color scale to a Plotly [pos, color] list, honoring '_r'."""
    if name.endswith("_r"):
        base = get_colorscale(name[:-2])
        return sorted([[1.0 - pos, color] for pos, color in base], key=lambda x: x[0])
    return get_colorscale(name)


def build_discrete_colorscale(n: int, scale: List) -> List:
    """Build a stepped discrete colorscale for ``n`` bins from a resolved scale."""
    positions = [(i + 0.5) / n for i in range(n)] if n > 1 else [0.5]
    colors = sample_colorscale(scale, positions)
    discrete = []
    for i, color in enumerate(colors):
        discrete.append([i / n, color])
        discrete.append([(i + 1) / n, color])
    return discrete


def assign_bins(values: np.ndarray, edges: List[float]) -> np.ndarray:
    """Assign each value to a bin index in [0, n-1]."""
    n = len(edges) - 1
    interior = np.array(edges[1:-1]) if n > 1 else np.array([])
    idx = np.digitize(values, interior, right=False)
    return np.clip(idx, 0, n - 1)


# ---------------------------------------------------------------------------
# Map rendering (binned, colorblind-friendly)
# ---------------------------------------------------------------------------
def make_choropleth_map(
    geojson_dict: dict,
    counties_meta: pd.DataFrame,
    scores_df: pd.DataFrame,
    edges: List[float],
    palette: str,
    title: str,
    height: int = 700,
    show_colorbar: bool = True,
    binned: bool = True,
    value_col: str = 'composite_normalized',
    value_min: float = 0.0,
    value_max: float = 1.0,
    value_tickformat: str = ".2f",
    value_hover_label: str = "Readiness Index",
    use_mask: bool = False,
    bg_geojson: dict = None,
    bg_meta: pd.DataFrame = None,
    bg_whiteness: float = 0.7,
) -> go.Figure:
    """
    Create an interactive choropleth of the County Siting Readiness Index.

    ``binned=True`` renders discrete score-based bins; ``binned=False`` renders the
    continuous min-max normalized score. Low = higher readiness; high = lower
    readiness / greater deployment constraint. When ``use_mask`` is True, the clipped
    counties are drawn on top and the full county set is drawn faintly below.
    """
    scale = _resolve_scale(palette)
    land_color = "rgb(255, 255, 255)" if use_mask else "rgb(243, 243, 243)"

    n = len(edges) - 1
    labels = make_bin_labels(edges) if binned else []
    discrete = build_discrete_colorscale(n, scale) if binned else None

    def prep(meta: pd.DataFrame) -> pd.DataFrame:
        metric_cols = ['GEOID', 'composite_score', 'composite_normalized']
        if 'composite_percentile' in scores_df.columns:
            metric_cols.append('composite_percentile')
        merged = meta.merge(scores_df[metric_cols], on='GEOID', how='left')
        merged['_display'] = merged[value_col].astype(float)
        vals = merged['_display'].fillna(value_min).values
        if binned:
            merged['bin_idx'] = assign_bins(vals, edges)
            merged['bin_label'] = [labels[i] for i in merged['bin_idx']]
        return merged

    def z_kwargs(df: pd.DataFrame, showscale: bool) -> dict:
        if binned:
            return dict(
                z=df['bin_idx'].tolist(), colorscale=discrete,
                zmin=-0.5, zmax=n - 0.5, showscale=showscale,
            )
        return dict(
            z=df['_display'].fillna(value_min).tolist(), colorscale=scale,
            zmin=value_min, zmax=value_max, showscale=showscale,
        )

    if not show_colorbar:
        colorbar = None
    elif binned:
        # Ticks sit on the bin boundaries (edges), each showing a single value
        # (0.0, 0.2, 0.4 …) rather than a range label like "0.0–0.2".
        colorbar = dict(
            title=READINESS_LEGEND_TITLE,
            tickmode="array",
            tickvals=[i - 0.5 for i in range(n + 1)],
            ticktext=[_fmt_edge(e) for e in edges],
            thicknessmode="pixels", thickness=15,
            lenmode="pixels", len=min(height - 100, 320),
        )
    else:
        colorbar = dict(
            title=READINESS_LEGEND_TITLE,
            thicknessmode="pixels", thickness=15,
            lenmode="pixels", len=min(height - 100, 320),
            tickformat=value_tickformat,
        )

    fig = go.Figure()

    if use_mask and bg_geojson is not None and bg_meta is not None:
        bg_df = prep(bg_meta)
        fig.add_trace(go.Choropleth(
            geojson=bg_geojson,
            locations=bg_df['GEOID'].tolist(),
            featureidkey="properties.GEOID",
            marker_opacity=max(0.0, 1.0 - bg_whiteness),
            marker_line_width=0.2,
            marker_line_color="white",
            hoverinfo="skip",
            name="Outside mask",
            **z_kwargs(bg_df, False),
        ))

    data_df = prep(counties_meta)
    if binned:
        customdata = np.stack([
            data_df['NAME'].astype(str).values,
            data_df['_display'].fillna(value_min).values,
            data_df['bin_label'].values,
        ], axis=-1)
        hovertemplate = (
            "<b>%{customdata[0]}</b><br>"
            + value_hover_label + ": %{customdata[1]:" + value_tickformat + "}<br>"
            "Bin: %{customdata[2]}<extra></extra>"
        )
    else:
        customdata = np.stack([
            data_df['NAME'].astype(str).values,
            data_df['_display'].fillna(value_min).values,
        ], axis=-1)
        hovertemplate = (
            "<b>%{customdata[0]}</b><br>"
            + value_hover_label + ": %{customdata[1]:" + value_tickformat + "}<extra></extra>"
        )

    fig.add_trace(go.Choropleth(
        geojson=geojson_dict,
        locations=data_df['GEOID'].tolist(),
        featureidkey="properties.GEOID",
        marker_opacity=1.0,
        marker_line_width=0.3,
        marker_line_color="white",
        colorbar=colorbar,
        customdata=customdata,
        hovertemplate=hovertemplate,
        name="County Siting Readiness Index",
        **z_kwargs(data_df, show_colorbar),
    ))

    fig.update_layout(
        title=title,
        height=height,
        margin=dict(l=0, r=0, t=80, b=0),
        geo=dict(
            scope="usa",
            projection_type="albers usa",
            resolution=50,
            showland=True,
            landcolor=land_color,
            showlakes=False,
            showcoastlines=False,
            showframe=False,
            showcountries=False,
            showsubunits=False,
        ),
    )
    return fig


# ---------------------------------------------------------------------------
# Formatting / export helpers
# ---------------------------------------------------------------------------
def format_weights_short(weights: Dict[str, float]) -> str:
    """Format active weights for a map subtitle using clean display names."""
    formatted = ", ".join(
        f"{DISPLAY_NAMES.get(k, k)}: {v:.2f}"
        for k, v in weights.items() if v > 0
    )
    return formatted if formatted else "All zeros"


def weights_as_pct(weights: Dict[str, float]) -> List:
    """Return [(clean name, percent share)] for the active (non-zero) weights."""
    active = [(DISPLAY_NAMES.get(k, k), float(v)) for k, v in weights.items() if v > 0]
    total = sum(v for _, v in active) or 1.0
    return [(name, v / total * 100.0) for name, v in active]


def weights_two_line_text(weights: Dict[str, float]) -> tuple:
    """Two balanced lines of all indicator shares using full display names."""
    total = sum(max(0.0, float(v)) for v in weights.values()) or 1.0
    parts = [
        f"<b>{DISPLAY_NAMES.get(c, c)}</b>&nbsp;"
        f"{max(0.0, float(weights.get(c, 0.0))) / total * 100:.1f}%"
        for c in INDICATOR_ORDER
    ]
    half = len(parts) // 2 + 1
    sep = "&nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp;"
    return sep.join(parts[:half]), sep.join(parts[half:])


def export_results_csv(
    df: pd.DataFrame,
    weights: Dict[str, float],
    community_mode: str,
    include_territories: bool,
) -> str:
    """Export a clean, analysis-ready county results table (sorted by readiness)."""
    out = pd.DataFrame()
    if 'County' in df.columns:
        out['County'] = df['County'].astype(str).str.strip()
    out['FIPS'] = df['GEOID'].astype(str)
    # Rank 1 = highest readiness (lowest raw score).
    out['Readiness Rank'] = df['composite_score'].rank(method='min').astype(int)
    out['Readiness Index (0-1)'] = df['composite_normalized'].round(4)
    if 'composite_percentile' in df.columns:
        out['Percentile Rank'] = df['composite_percentile'].round(1)
    out['Raw Weighted Score'] = df['composite_score'].round(4)
    for c in RAW_COLUMNS:
        if c in df.columns:
            out[DISPLAY_NAMES.get(c, c)] = df[c].round(4)

    out = out.sort_values('Readiness Rank').reset_index(drop=True)
    return out.to_csv(index=False)


def build_export_figure(
    base_fig: go.Figure,
    *,
    scenario_label: str,
    metric_name: str,
    weights: Dict[str, float],
    community_mode: str,
    include_territories: bool,
) -> go.Figure:
    """Clone the map into a clean, presentation-quality figure for static export."""
    fig = go.Figure(base_fig)

    line1, line2 = weights_two_line_text(weights)
    extent = "Alaska & Hawaii included" if include_territories else "Contiguous U.S. (48 states)"
    community_note = community_mode.replace(" perspective", "")
    meta_line = (
        f"Community Context: {community_note}"
        f"&nbsp;&nbsp;•&nbsp;&nbsp;{extent}"
        f"&nbsp;&nbsp;•&nbsp;&nbsp;Displaying: {metric_name}"
        f"&nbsp;&nbsp;•&nbsp;&nbsp;{datetime.now():%Y-%m-%d}"
    )

    # Colorbar position (fractional paper coords) so we can label its two ends.
    cbar_x = 0.9
    cbar_len = 0.55
    cbar_y = 0.5

    fig.update_traces(hovertemplate=None, hoverinfo="skip")
    # Clean, readable discrete legend that matches the paper style.
    fig.update_traces(
        selector=dict(type="choropleth"),
        colorbar_title_text="<i>Lower = higher readiness</i><br> <br> ",
        colorbar_title_side="top",
        colorbar_title_font_size=16,
        colorbar_title_font_family="Arial, sans-serif",
        colorbar_tickfont_size=14,
        colorbar_outlinewidth=0,
        colorbar_thickness=20,
        colorbar_lenmode="fraction",
        colorbar_len=cbar_len,
        colorbar_xanchor="center",
        colorbar_x=cbar_x,
        colorbar_yanchor="middle",
        colorbar_y=cbar_y,
        marker_line_color="white",
    )
    fig.update_layout(
        title=dict(
            text="<b>County Siting Readiness Index</b>",
            x=0.5, xanchor="center", y=0.965, yanchor="top",
            font=dict(size=34, family="Arial, sans-serif", color="#111111"),
        ),
        font=dict(family="Arial, sans-serif", color="#222"),
        paper_bgcolor="white",
        plot_bgcolor="white",
        margin=dict(l=20, r=20, t=120, b=250),
        annotations=[
            # Weight description (two spaced lines + metadata) at the bottom.
            dict(
                text=f"<span style='font-size:16px;color:#1a1a1a'>{line1}</span>",
                showarrow=False, xref="paper", yref="paper",
                x=0.5, y=-0.10, xanchor="center", yanchor="top", align="center",
            ),
            dict(
                text=f"<span style='font-size:16px;color:#1a1a1a'>{line2}</span>",
                showarrow=False, xref="paper", yref="paper",
                x=0.5, y=-0.155, xanchor="center", yanchor="top", align="center",
            ),
            dict(
                text=f"<span style='font-size:13px;color:#888'>{meta_line}</span>",
                showarrow=False, xref="paper", yref="paper",
                x=0.5, y=-0.225, xanchor="center", yanchor="top", align="center",
            ),
        ],
        geo=dict(
            scope="usa",
            projection_type="albers usa",
            resolution=50,
            domain=dict(x=[0.0, 0.84], y=[0.0, 1.0]),
            bgcolor="white",
            landcolor="white",
            showland=True,
            showframe=False,
            showcoastlines=False,
            showlakes=False,
            showcountries=False,
            showsubunits=False,
        ),
    )
    return fig


def figure_to_png_bytes(fig: go.Figure, width: int = 1800, height: int = 1250,
                        scale: int = 3) -> bytes:
    """Render a Plotly figure to a high-resolution PNG (requires kaleido)."""
    return fig.to_image(format="png", width=width, height=height, scale=scale)


# ---------------------------------------------------------------------------
# Session-state / weight controls
# ---------------------------------------------------------------------------
def initialize_weight_state() -> None:
    """Initialize weight/toggle/session state from the default scenario."""
    defaults = SCENARIOS[DEFAULT_SCENARIO]
    for col in INDICATOR_ORDER:
        value = int(defaults[col])
        for key in (f"weight_{col}", f"input_{col}"):
            if key not in st.session_state:
                st.session_state[key] = value
        if f"toggle_{col}" not in st.session_state:
            st.session_state[f"toggle_{col}"] = False


def sync_weight_from_input(col: str) -> None:
    """Store the number the user typed as the canonical weight for ``col``."""
    st.session_state[f"weight_{col}"] = st.session_state[f"input_{col}"]
    refresh_scenario_choice()


def handle_solo_toggle(col: str) -> None:
    """When a Solo toggle turns on, set that weight to 1 point and all others to 0."""
    if not st.session_state.get(f"toggle_{col}"):
        return
    for other in INDICATOR_ORDER:
        value = 1 if other == col else 0
        st.session_state[f"weight_{other}"] = value
        st.session_state[f"input_{other}"] = value
        st.session_state[f"toggle_{other}"] = (other == col)
    refresh_scenario_choice()


def apply_scenario() -> None:
    """Load a scenario's weights into the number inputs (no-op for 'Custom')."""
    name = st.session_state.get("scenario_choice")
    if name not in SCENARIOS:
        return
    for col in INDICATOR_ORDER:
        value = int(SCENARIOS[name][col])
        st.session_state[f"weight_{col}"] = value
        st.session_state[f"input_{col}"] = value
        st.session_state[f"toggle_{col}"] = False


def weights_match_scenario(name: str) -> bool:
    """True if the current weights equal the given scenario's weights."""
    scenario = SCENARIOS.get(name)
    if not scenario:
        return False
    return all(
        int(st.session_state.get(f"weight_{col}", 0)) == int(scenario[col])
        for col in INDICATOR_ORDER
    )


def refresh_scenario_choice() -> None:
    """Keep section 2 in sync: switch to 'Custom' when weights leave a named scenario,
    or snap back to a named scenario when the weights happen to match one."""
    current = st.session_state.get("scenario_choice")
    if current in SCENARIOS and weights_match_scenario(current):
        return
    for name in SCENARIO_ORDER:
        if weights_match_scenario(name):
            st.session_state["scenario_choice"] = name
            return
    st.session_state["scenario_choice"] = "Custom"


def current_weights() -> Dict[str, float]:
    """Read the current indicator weights (all nine) from session state."""
    return {col: st.session_state[f"weight_{col}"] for col in INDICATOR_ORDER}


def short_label(col: str) -> str:
    return DISPLAY_NAMES.get(col, col)


def indicator_label(col: str, community_mode: str) -> str:
    """UI label for an indicator; the Community column follows the chosen perspective."""
    if col == COMMUNITY_COLUMN:
        return "Social Vulnerability" if community_mode == COMMUNITY_SOCIAL else "Economic Development Need"
    return DISPLAY_NAMES.get(col, col)


def render_weight_bar(label: str, pct: float, max_pct: float) -> None:
    """Render a read-only bar whose length and color encode an indicator's weight share."""
    frac = 0.0 if max_pct <= 0 else max(0.0, min(1.0, pct / max_pct))
    fill = frac * 100.0
    # Color encodes magnitude: light blue for a small share, deep blue for the largest.
    r = int(round(198 + (8 - 198) * frac))
    g = int(round(219 + (81 - 219) * frac))
    b = int(round(239 + (156 - 239) * frac))
    bar_color = f"rgb({r},{g},{b})"
    st.markdown(
        f"""
        <div style="margin:2px 0 12px 0;">
          <div style="display:flex;justify-content:space-between;align-items:baseline;font-size:14px;margin-bottom:4px;">
            <span style="font-weight:600;">{label}</span>
            <span style="font-weight:700;color:#1f3b57;">{pct:.1f}%</span>
          </div>
          <div style="background:#eef1f4;border-radius:5px;height:14px;width:100%;">
            <div style="background:{bar_color};width:{fill:.2f}%;height:14px;border-radius:5px;"></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def make_distribution_figure(
    values,
    palette: str,
    vmin: float = 0.0,
    vmax: float = 1.0,
    xtitle: str = "County Siting Readiness Index (lower = higher readiness)",
    tickformat: str = ".2f",
    height: int = 260,
) -> go.Figure:
    """Histogram of the displayed metric, with bars colored along the map's palette."""
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    fig = go.Figure()
    if vals.size == 0:
        fig.update_layout(height=height)
        return fig
    span = vmax - vmin if vmax > vmin else 1.0
    nbins = 40
    counts, edges = np.histogram(vals, bins=nbins, range=(vmin, vmax))
    centers = (edges[:-1] + edges[1:]) / 2.0
    positions = [float((c - vmin) / span) for c in centers]
    colors = sample_colorscale(get_colorscale(palette), positions)
    fig.add_trace(
        go.Bar(
            x=centers,
            y=counts,
            marker=dict(color=colors, line=dict(width=0)),
            width=(span / nbins) * 0.9,
            hovertemplate="%{x:" + tickformat + "}<br>%{y} counties<extra></extra>",
        )
    )
    fig.update_layout(
        height=height,
        margin=dict(l=50, r=20, t=20, b=45),
        bargap=0.05,
        plot_bgcolor="white",
        showlegend=False,
    )
    fig.update_xaxes(
        range=[vmin, vmax],
        title=xtitle,
        showgrid=False,
        tickformat=tickformat,
    )
    fig.update_yaxes(title="Number of counties", showgrid=True, gridcolor="#eee")
    return fig


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
def main():
    st.set_page_config(
        page_title="County Siting Readiness Index",
        page_icon="🗺️",
        layout="wide",
    )

    st.title("🗺️ County Siting Readiness Index")
    st.markdown(
        "The app computes a **County Siting Readiness Index** using user-selected "
        "indicator weights. The three default scenarios correspond to **Storage-First**, "
        "**Grid-Speed**, and **Policy-and-Permitting** readiness. The nine indicators "
        "are grouped into three categories: **Execution and Community Risk**, "
        "**Speed-to-Deploy Enablers**, and **Operational Risk and Resource Resilience**. "
        "The Community Context Layer is one of the indicators and can be interpreted "
        "either as a need for additional safeguards or as an indicator of economic "
        "development need.\n\n"
        "_Interpretation: **lower score = higher readiness**; "
        "**higher score = lower readiness / greater deployment constraint**._"
    )

    with st.expander("ℹ️ About the indicator categories"):
        for cat, desc in CATEGORY_DESCRIPTIONS.items():
            st.markdown(f"**{cat}.** {desc}")

    # --- Sidebar: 1. Data ---
    st.sidebar.header("⚙️ Configuration")
    st.sidebar.subheader("1. Load Data")

    default_csv = Path(__file__).parent / "county_column_scores.csv"
    upload_option = st.sidebar.radio("Data source:", ["Use default file", "Upload CSV"])

    csv_file = None
    if upload_option == "Upload CSV":
        csv_file = st.sidebar.file_uploader("Upload county scores CSV", type=['csv'])
    elif default_csv.exists():
        csv_file = str(default_csv)
    else:
        st.error(f"Default file not found: {default_csv}")
        st.stop()

    if csv_file is None:
        st.info("Please upload a CSV file to begin.")
        st.stop()

    try:
        with st.spinner("Loading county scores..."):
            scores_df = load_scores_csv(csv_file)
        st.sidebar.success(f"✓ Loaded {len(scores_df)} counties with scores")
    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.stop()

    initialize_weight_state()

    # --- Sidebar: 2. Scenario ---
    st.sidebar.subheader("2. Scenario")
    st.sidebar.radio(
        "Default scenario (sets indicator weights):",
        ["Custom"] + SCENARIO_ORDER,
        index=1 + SCENARIO_ORDER.index(DEFAULT_SCENARIO),
        key="scenario_choice",
        on_change=apply_scenario,
    )

    # --- Sidebar: 3. Community Context interpretation ---
    st.sidebar.subheader("3. Community Context Layer")
    st.sidebar.caption(COMMUNITY_DESCRIPTION)
    community_mode = st.sidebar.radio(
        "Interpret the Community Context Layer as:",
        COMMUNITY_OPTIONS,
        index=0,
        key="community_mode",
        help="Social Vulnerability: higher vulnerability lowers readiness. "
             "Economic Development Need: reversed (1 - score) so higher vulnerability = higher priority. "
             "Set its weight to zero (in section 4) to ignore this layer.",
    )

    # --- Sidebar: 4. Weights ---
    st.sidebar.subheader("4. Weight Configuration")
    # Weights are relative points and are always normalized into percentage shares.
    normalize_weights = True
    st.sidebar.caption(
        "Set an integer **weight** for each indicator with the − / + arrows (or by typing). "
        "Percentages are computed from the sum of all weights (e.g. every indicator = 1 → "
        "11.11% each). The bars show the resulting share. Use **Solo** to isolate one indicator."
    )
    st.sidebar.markdown(
        """
        <style>
        div[data-testid="stNumberInput"] input { max-width: 46px; padding: 2px 4px; }
        div[data-testid="stNumberInput"] button { padding: 0 4px; }
        /* Keep the −/+ stepper buttons neutral after being clicked/focused
           (Streamlit's default primary red would otherwise stick). */
        div[data-testid="stNumberInput"] button:hover,
        div[data-testid="stNumberInput"] button:active,
        div[data-testid="stNumberInput"] button:focus,
        div[data-testid="stNumberInput"] button:focus-visible {
            color: #31333F !important;
            border-color: #d5d7e0 !important;
            background-color: #f0f2f6 !important;
            box-shadow: none !important;
            outline: none !important;
        }
        div[data-testid="stNumberInput"] button:hover svg,
        div[data-testid="stNumberInput"] button:active svg,
        div[data-testid="stNumberInput"] button:focus svg {
            fill: #31333F !important;
            color: #31333F !important;
        }
        /* Neutralize the red focus ring on the number field itself. */
        div[data-testid="stNumberInput"] [data-baseweb="input"]:focus-within {
            border-color: #d5d7e0 !important;
            box-shadow: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Percentages come from the sum of all typed weights.
    pre_weights = current_weights()
    weight_total = sum(pre_weights.values())
    pct_by_col = {
        c: (pre_weights[c] / weight_total * 100.0 if weight_total > 0 else 0.0)
        for c in INDICATOR_ORDER
    }
    max_pct = max(pct_by_col.values()) if pct_by_col else 0.0

    for category, cols in CATEGORIES.items():
        st.sidebar.markdown(f"**{category}**")
        for col in cols:
            label = indicator_label(col, community_mode)
            solo_col, num_col, bar_col = st.sidebar.columns([0.7, 1.1, 1.9], gap="small")
            with solo_col:
                st.markdown("<div style='padding-top:2px'></div>", unsafe_allow_html=True)
                st.toggle(
                    "Solo",
                    key=f"toggle_{col}",
                    label_visibility="collapsed",
                    help=f"Set {label} = 1 and all others = 0",
                    on_change=handle_solo_toggle,
                    args=(col,),
                )
                st.markdown(
                    "<div style='text-align:left;font-size:11px;color:#555;"
                    "margin-top:-6px;'>Solo</div>",
                    unsafe_allow_html=True,
                )
            with num_col:
                st.markdown("<div style='padding-top:2px'></div>", unsafe_allow_html=True)
                st.number_input(
                    label,
                    min_value=0, step=1, format="%d",
                    key=f"input_{col}",
                    label_visibility="collapsed",
                    help=f"Weight (integer) for {label}",
                    on_change=sync_weight_from_input,
                    args=(col,),
                )
            with bar_col:
                render_weight_bar(label, pct_by_col[col], max_pct)

    weights = current_weights()

    active_sum = sum(v for v in weights.values() if v > 0)
    st.sidebar.info(f"**Sum of weights:** {active_sum:.0f} → normalized to 100%")

    # --- Sidebar: 5. Score display ---
    st.sidebar.subheader("5. Score display")
    view_mode = st.sidebar.radio(
        "What to display:",
        ["Scores", "Ranking (percentiles)"],
        index=0,
        help="Scores shows the readiness index value; Ranking shows each county's "
             "percentile rank (0 = highest readiness, 100 = lowest).",
    )

    score_type = "Min-max normalized"
    display_mode = "Binned"
    binned = False
    bin_mode = "Fixed bin size"
    bin_size = DEFAULT_BIN_SIZE
    n_bins = DEFAULT_BIN_COUNT
    quantile_bins = False

    if view_mode == "Scores":
        score_type = st.sidebar.radio(
            "Score type:",
            ["Min-max normalized", "Raw scores"],
            index=0,
            help="Min-max normalized rescales the index to 0–1. Raw scores keep the "
                 "weighted-average value as computed.",
        )
        display_mode = st.sidebar.radio(
            "Show the scores as:",
            DISPLAY_MODES,
            index=0,
            help="Continuous shows a smooth color gradient; Binned groups scores into "
                 "discrete color bands.",
        )
        binned = (display_mode == "Binned")
        if binned:
            st.sidebar.markdown("**Map binning options**")
            bin_mode = st.sidebar.radio("Binning mode:", ["Fixed bin size", "Number of bins"], index=0)
            if bin_mode == "Fixed bin size":
                bin_size = st.sidebar.selectbox(
                    "Bin size:", BIN_SIZE_OPTIONS, index=BIN_SIZE_OPTIONS.index(DEFAULT_BIN_SIZE)
                )
            else:
                n_bins = st.sidebar.selectbox(
                    "Number of bins:", BIN_COUNT_OPTIONS, index=BIN_COUNT_OPTIONS.index(DEFAULT_BIN_COUNT)
                )
            bin_spacing = st.sidebar.radio(
                "Bin edges:",
                ["Equal-width (by score range)", "Equal-count (quantiles)"],
                index=0,
                help="Equal-width splits the score range into equal intervals. "
                     "Equal-count puts roughly the same number of counties in each bin.",
            )
            quantile_bins = bin_spacing.startswith("Equal-count")
    else:
        st.sidebar.caption("Ranking shows a continuous percentile map — no further options.")

    # Resolve which metric column drives the map and the distribution chart.
    if view_mode == "Ranking (percentiles)":
        value_col = "composite_percentile"
        value_tickformat = ".0f"
        value_hover_label = "Percentile"
        value_xtitle = "Percentile rank (lower = higher readiness)"
    elif score_type == "Raw scores":
        value_col = "composite_score"
        value_tickformat = ".3f"
        value_hover_label = "Raw index"
        value_xtitle = "Raw weighted index (lower = higher readiness)"
    else:
        value_col = "composite_normalized"
        value_tickformat = ".2f"
        value_hover_label = "Readiness Index"
        value_xtitle = "County Siting Readiness Index (lower = higher readiness)"


    # --- Sidebar: 6. Color scale ---
    st.sidebar.subheader("6. Color scale")
    palette_name = st.sidebar.selectbox(
        "Color scale:",
        list(COLOR_SCALES.keys()),
        index=list(COLOR_SCALES.keys()).index(DEFAULT_PALETTE),
        help="Default green→red: green = higher readiness, red = lower readiness. "
             "Colorblind-friendly alternatives are also available.",
    )
    palette = COLOR_SCALES[palette_name]

    # --- Sidebar: 7. Map extent / mask ---
    st.sidebar.subheader("7. Map Options")
    include_territories = st.sidebar.checkbox(
        "Include AK/HI",
        value=False,
        help="Include Alaska and Hawaii (Puerto Rico excluded due to lack of data).",
    )

    st.sidebar.markdown("**CO₂ Underground Storage Mask:**")
    mask_zip_path = Path(__file__).parent / "storage_mask.zip"
    mask_source_options = []
    if mask_zip_path.exists():
        mask_source_options.append("Use default file")
    mask_source_options.append("Upload custom mask")
    mask_source = st.sidebar.radio("Mask source:", mask_source_options)

    mask_zip_file = None
    if mask_source == "Upload custom mask":
        mask_zip_file = st.sidebar.file_uploader(
            "Upload CO₂ storage mask (ZIP with shapefile)", type=['zip']
        )
    elif mask_source == "Use default file" and mask_zip_path.exists():
        mask_zip_file = str(mask_zip_path)

    apply_mask = False
    bg_whiteness = 0.7
    if mask_zip_file is not None:
        apply_mask = st.sidebar.checkbox(
            "Apply CO₂ storage mask overlay",
            value=False,
            help="Clips county polygons to confirmed CO₂ storage sites.",
        )
        if apply_mask:
            bg_whiteness = st.sidebar.slider(
                "Background counties (outside mask)",
                min_value=0.0, max_value=1.0, value=0.7, step=0.05,
                help="0 = full colors visible, 1 = totally white/hidden",
            )
    else:
        st.sidebar.info("📦 No mask available. Upload a ZIP shapefile to enable masking.")

    # Resolve mask bytes + identity for the config signature.
    mask_bytes = None
    mask_id = None
    if apply_mask and mask_zip_file is not None:
        if isinstance(mask_zip_file, str):
            with open(mask_zip_file, "rb") as f:
                mask_bytes = f.read()
        else:
            mask_bytes = mask_zip_file.getvalue()
        mask_id = hashlib.md5(mask_bytes).hexdigest()

    # --- Sidebar: 8. Update / export ---
    st.sidebar.subheader("8. Update & Export")
    update_clicked = st.sidebar.button("🔄 Update map", type="primary")

    # Configuration signature: any change requires clicking "Update map".
    current_config = {
        "weights": {k: round(float(v), 4) for k, v in weights.items()},
        "community_mode": community_mode,
        "normalize": normalize_weights,
        "include_territories": include_territories,
        "apply_mask": apply_mask,
        "mask_id": mask_id,
        "bin_mode": bin_mode,
        "bin_size": float(bin_size),
        "n_bins": int(n_bins),
        "quantile": quantile_bins,
        "view_mode": view_mode,
        "score_type": score_type,
        "display_mode": display_mode,
        "palette": palette_name,
    }

    need_first_render = "current_fig" not in st.session_state

    if update_clicked or need_first_render:
        with st.spinner("Computing County Siting Readiness Index and rendering map..."):
            results = compute_index(scores_df, weights, community_mode, normalize_weights)

            if value_col == "composite_percentile":
                value_min, value_max = 0.0, 100.0
            elif value_col == "composite_score":
                _v = results['composite_score'].astype(float)
                value_min, value_max = float(_v.min()), float(_v.max())
                if value_max <= value_min:
                    value_max = value_min + 1e-9
            else:
                value_min, value_max = 0.0, 1.0

            if binned:
                edges = compute_bin_edges(
                    results[value_col].values,
                    bin_mode, bin_size, n_bins, quantile_bins,
                    vmin=value_min, vmax=value_max,
                )
            else:
                edges = [value_min, value_max]

            geojson_dict, counties_meta = build_geojson(include_territories)
            bg_geojson, bg_meta = geojson_dict, counties_meta

            use_mask = False
            if apply_mask and mask_bytes is not None:
                try:
                    clipped_geojson, clipped_meta = build_clipped_geojson(mask_bytes, include_territories)
                    geojson_dict, counties_meta = clipped_geojson, clipped_meta
                    use_mask = True
                except Exception as e:
                    st.sidebar.error(f"Could not load mask: {e}")

            title = (
                "County Siting Readiness Index (Lower = higher readiness)"
                f"<br><sub>Weights: {format_weights_short(weights)}</sub>"
            )
            fig = make_choropleth_map(
                geojson_dict, counties_meta, results, edges, palette, title,
                binned=binned,
                value_col=value_col, value_min=value_min, value_max=value_max,
                value_tickformat=value_tickformat, value_hover_label=value_hover_label,
                use_mask=use_mask,
                bg_geojson=bg_geojson if use_mask else None,
                bg_meta=bg_meta if use_mask else None,
                bg_whiteness=bg_whiteness,
            )

        st.session_state["current_fig"] = fig
        st.session_state["current_results"] = results
        st.session_state["current_weights"] = weights
        st.session_state["current_display"] = dict(
            value_col=value_col, value_min=value_min, value_max=value_max,
            value_tickformat=value_tickformat, value_hover_label=value_hover_label,
            value_xtitle=value_xtitle,
        )
        st.session_state["export_meta"] = dict(
            scenario_label=st.session_state.get("scenario_choice", "Custom"),
            metric_name=value_hover_label,
            community_mode=community_mode,
            include_territories=include_territories,
        )
        st.session_state.pop("export_png", None)
        st.session_state["committed_config"] = current_config
    elif st.session_state.get("committed_config") != current_config:
        st.warning("Slider values changed. Click **Update map** to refresh results.")

    results = st.session_state["current_results"]
    committed_weights = st.session_state["current_weights"]
    disp = st.session_state["current_display"]

    # --- Statistics ---
    metric = results[disp["value_col"]]
    stat_fmt = "{:.1f}" if disp["value_col"] == "composite_percentile" else "{:.3f}"
    metric_name = disp["value_hover_label"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Counties", len(results))
    c2.metric(f"Min {metric_name}", stat_fmt.format(metric.min()))
    c3.metric(f"Mean {metric_name}", stat_fmt.format(metric.mean()))
    c4.metric(f"Max {metric_name}", stat_fmt.format(metric.max()))
    c5.metric("Std Dev", stat_fmt.format(metric.std()))

    # --- Map ---
    st.plotly_chart(st.session_state["current_fig"], use_container_width=True)

    # --- Score distribution ---
    st.markdown(f"**Distribution of {metric_name.lower()} across counties**")
    st.plotly_chart(
        make_distribution_figure(
            metric.values, palette,
            vmin=disp["value_min"], vmax=disp["value_max"],
            xtitle=disp["value_xtitle"], tickformat=disp["value_tickformat"],
        ),
        use_container_width=True,
    )

    # --- Exports ---
    st.markdown("### Downloads")
    exp1, exp2 = st.columns(2)

    with exp1:
        st.markdown("**High-resolution map (PNG)**")
        meta = st.session_state.get("export_meta", {})
        if st.button("🖼️ Generate map image"):
            with st.spinner("Rendering high-resolution image..."):
                try:
                    export_fig = build_export_figure(
                        st.session_state["current_fig"],
                        scenario_label=meta.get("scenario_label", "Custom"),
                        metric_name=meta.get("metric_name", metric_name),
                        weights=committed_weights,
                        community_mode=meta.get("community_mode", community_mode),
                        include_territories=meta.get("include_territories", include_territories),
                    )
                    st.session_state["export_png"] = figure_to_png_bytes(export_fig)
                except Exception as e:
                    st.error(f"Image export failed: {e}")
        if st.session_state.get("export_png"):
            st.download_button(
                "📥 Download PNG",
                data=st.session_state["export_png"],
                file_name=f"siting_readiness_map_{datetime.now():%Y%m%d}.png",
                mime="image/png",
            )

    with exp2:
        st.markdown("**County results (CSV)**")
        st.caption("Clean table: county, FIPS, readiness rank, index, percentile and indicators.")
        st.download_button(
            "📥 Download CSV",
            data=export_results_csv(
                results, committed_weights, community_mode, include_territories
            ),
            file_name=f"county_siting_readiness_{datetime.now():%Y%m%d}.csv",
            mime="text/csv",
        )


    # --- Scenario comparison ---
    st.markdown("---")
    st.subheader("Scenario comparison")
    st.caption(
        "Compares the three default scenarios (Storage-First, Grid-Speed, "
        "Policy-and-Permitting) using consistent extent, binning, palette and legend. "
        "Each scenario uses its paper weights (Community Context Layer = 0)."
    )
    if st.button("📊 Compare 3 scenarios"):
        with st.spinner("Computing scenario comparison..."):
            geojson_dict, counties_meta = build_geojson(include_territories)
            sc_results_map = {
                name: compute_index(scores_df, dict(SCENARIOS[name]), community_mode, normalize_weights)
                for name in SCENARIO_ORDER
            }
            if value_col == "composite_percentile":
                sc_vmin, sc_vmax = 0.0, 100.0
            elif value_col == "composite_score":
                allv = np.concatenate([r['composite_score'].values for r in sc_results_map.values()])
                sc_vmin, sc_vmax = float(np.min(allv)), float(np.max(allv))
                if sc_vmax <= sc_vmin:
                    sc_vmax = sc_vmin + 1e-9
            else:
                sc_vmin, sc_vmax = 0.0, 1.0
            figs = {}
            for name in SCENARIO_ORDER:
                sc_results = sc_results_map[name]
                if binned:
                    sc_edges = compute_bin_edges(
                        sc_results[value_col].values,
                        bin_mode, bin_size, n_bins, quantile_bins,
                        vmin=sc_vmin, vmax=sc_vmax,
                    )
                else:
                    sc_edges = [sc_vmin, sc_vmax]
                figs[name] = make_choropleth_map(
                    geojson_dict, counties_meta, sc_results, sc_edges, palette,
                    title=name, height=450, show_colorbar=True, binned=binned,
                    value_col=value_col, value_min=sc_vmin, value_max=sc_vmax,
                    value_tickformat=value_tickformat, value_hover_label=value_hover_label,
                )
        st.session_state["scenario_figs"] = figs

    if "scenario_figs" in st.session_state:
        figs = st.session_state["scenario_figs"]
        top_left, top_right = st.columns(2)
        with top_left:
            st.plotly_chart(figs[SCENARIO_ORDER[0]], use_container_width=True)
        with top_right:
            st.plotly_chart(figs[SCENARIO_ORDER[1]], use_container_width=True)
        _, bottom_center, _ = st.columns([1, 2, 1])
        with bottom_center:
            st.plotly_chart(figs[SCENARIO_ORDER[2]], use_container_width=True)

    # --- Sample data ---
    with st.expander("📊 View sample data"):
        display_cols = ['GEOID']
        if 'County' in results.columns:
            display_cols.append('County')
        display_cols += ['composite_score', 'composite_normalized']
        display_cols += [c for c in RAW_COLUMNS if c in results.columns]

        st.markdown("**Top 10 highest-readiness counties (lowest index):**")
        st.dataframe(
            results[display_cols].nsmallest(10, 'composite_normalized').reset_index(drop=True),
            hide_index=True,
        )
        st.markdown("**Top 10 lowest-readiness counties (highest index):**")
        st.dataframe(
            results[display_cols].nlargest(10, 'composite_normalized').reset_index(drop=True),
            hide_index=True,
        )

    # --- Footer ---
    st.markdown("---")
    st.markdown(
        "**About:** This tool combines multiple indicators into a single **County "
        "Siting Readiness Index**. Lower scores indicate higher readiness; higher "
        "scores indicate lower readiness / greater deployment constraint.\n\n"
        "**Scenarios:** The three default scenarios correspond to Storage-First, "
        "Grid-Speed, and Policy-and-Permitting readiness. The Community Context Layer "
        "is optional and can be explored as either a Social Vulnerability perspective "
        "(greater need for safeguards) or an Economic Development Need perspective "
        "(greater development priority)."
    )


if __name__ == "__main__":
    main()
