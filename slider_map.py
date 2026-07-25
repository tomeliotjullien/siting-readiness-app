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
from plotly.colors import sample_colorscale
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

# Three default paper scenarios. Weights are percentages that sum to 100 per
# scenario. The Community Context Layer is 0 in every default scenario — it is
# controlled separately by the user.
SCENARIOS: Dict[str, Dict[str, float]] = {
    "Storage-First Readiness": {
        "Community Context PCT": 0.00,
        "State Project Enablement Index PCT": 16.67,
        "Interconnection Queue": 19.44,
        "Relevant Workforce Availability": 5.56,
        "Land Cost": 8.33,
        "Long-Haul Fiber Optics Presence": 2.78,
        "Extreme Events (Wildfires, Floodings, Storms) PCT": 11.11,
        "Water Availability": 13.89,
        "Sequestration Access (EOR/Pipeline/Primacy)": 22.22,
    },
    "Grid-Speed Readiness": {
        "Community Context PCT": 0.00,
        "State Project Enablement Index PCT": 19.44,
        "Interconnection Queue": 22.22,
        "Relevant Workforce Availability": 13.89,
        "Land Cost": 11.11,
        "Long-Haul Fiber Optics Presence": 16.67,
        "Extreme Events (Wildfires, Floodings, Storms) PCT": 2.78,
        "Water Availability": 5.56,
        "Sequestration Access (EOR/Pipeline/Primacy)": 8.33,
    },
    "Policy-and-Permitting Readiness": {
        "Community Context PCT": 0.00,
        "State Project Enablement Index PCT": 22.22,
        "Interconnection Queue": 19.44,
        "Relevant Workforce Availability": 11.11,
        "Land Cost": 8.33,
        "Long-Haul Fiber Optics Presence": 13.89,
        "Extreme Events (Wildfires, Floodings, Storms) PCT": 2.78,
        "Water Availability": 5.56,
        "Sequestration Access (EOR/Pipeline/Primacy)": 16.67,
    },
}

DEFAULT_SCENARIO = "Storage-First Readiness"
SCENARIO_ORDER = list(SCENARIOS.keys())

# Community Context Layer perspectives.
COMMUNITY_NOT_INCLUDED = "Not included"
COMMUNITY_SOCIAL = "Social Vulnerability perspective"
COMMUNITY_ECONOMIC = "Economic Development Need perspective"
COMMUNITY_OPTIONS = [COMMUNITY_NOT_INCLUDED, COMMUNITY_SOCIAL, COMMUNITY_ECONOMIC]

COMMUNITY_DESCRIPTION = (
    "Social vulnerability can be interpreted in two directions. It may identify "
    "communities requiring additional safeguards and engagement, or communities with "
    "greater economic development need. For transparency, this layer is optional and "
    "can be explored in either direction."
)

# Colorblind-friendly sequential palettes (no red-green scales).
# Low index = higher readiness; high index = lower readiness.
COLORBLIND_SCALES: Dict[str, str] = {
    "Cividis": "Cividis",
    "Viridis": "Viridis",
    "Blues": "Blues",
    "YlOrBr": "YlOrBr",
    "Plasma": "Plasma",
}
DEFAULT_PALETTE = "Cividis"

# Fixed bin-size options.
BIN_SIZE_OPTIONS = [0.1, 0.2, 0.25, 0.5]
DEFAULT_BIN_SIZE = 0.2
BIN_COUNT_OPTIONS = [4, 5, 10]
DEFAULT_BIN_COUNT = 5

READINESS_LEGEND_TITLE = "County Siting Readiness Index<br>Lower = higher readiness"


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
        - "Not included": community weight forced to 0.
        - "Social Vulnerability perspective": raw value used as-is (higher social
          vulnerability increases the index -> lower readiness / greater need for
          safeguards).
        - "Economic Development Need perspective": value reversed via 1 - score
          (higher social vulnerability interpreted as greater economic development
          need / higher priority).

    Active weights are normalized to sum to 1 before scoring.
    """
    df_result = df.copy()
    cols = [c for c in RAW_COLUMNS if c in df.columns]

    values = df_result[cols].astype(float).copy()
    if COMMUNITY_COLUMN in values.columns and community_mode == COMMUNITY_ECONOMIC:
        values[COMMUNITY_COLUMN] = 1.0 - values[COMMUNITY_COLUMN]

    w = np.array([float(weights.get(c, 0.0)) for c in cols], dtype=float)

    if community_mode == COMMUNITY_NOT_INCLUDED and COMMUNITY_COLUMN in cols:
        w[cols.index(COMMUNITY_COLUMN)] = 0.0

    if normalize_weights and w.sum() > 0:
        w = w / w.sum()

    composite = values.values.dot(w)
    df_result['composite_score'] = composite

    c_min, c_max = composite.min(), composite.max()
    if c_max > c_min:
        df_result['composite_normalized'] = (composite - c_min) / (c_max - c_min)
    else:
        df_result['composite_normalized'] = np.zeros_like(composite)

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
) -> List[float]:
    """
    Return bin edges over the score range [0, 1].

    Score-based bins are used by default. If ``quantile`` is True, edges follow the
    empirical quantiles of ``values`` (clamped to the [0, 1] display range).
    """
    if bin_mode == "Fixed bin size":
        n = max(1, int(round(1.0 / bin_size)))
    else:
        n = int(n_bins)

    if quantile and values.size > 0:
        qs = np.linspace(0.0, 1.0, n + 1)
        edges = np.quantile(values, qs)
        edges[0], edges[-1] = 0.0, 1.0
        edges = np.maximum.accumulate(edges)
        edges = np.unique(edges)
        if len(edges) < 2:
            edges = np.array([0.0, 1.0])
        return [float(e) for e in edges]

    if bin_mode == "Fixed bin size":
        edges = list(np.arange(0.0, 1.0 + 1e-9, bin_size))
        if edges[-1] < 1.0 - 1e-9:
            edges.append(1.0)
        edges[-1] = 1.0
    else:
        edges = list(np.linspace(0.0, 1.0, n + 1))

    return [float(e) for e in edges]


def make_bin_labels(edges: List[float]) -> List[str]:
    """Human-readable bin labels; first = highest readiness, last = lowest."""
    n = len(edges) - 1
    labels = []
    for i in range(n):
        base = f"{_fmt_edge(edges[i])}\u2013{_fmt_edge(edges[i + 1])}"
        if i == 0:
            base += " Highest readiness"
        elif i == n - 1:
            base += " Lowest readiness"
        labels.append(base)
    return labels


def build_discrete_colorscale(n: int, palette: str) -> List:
    """Build a stepped discrete colorscale for ``n`` bins from a named palette."""
    positions = [(i + 0.5) / n for i in range(n)] if n > 1 else [0.5]
    colors = sample_colorscale(palette, positions)
    scale = []
    for i, color in enumerate(colors):
        scale.append([i / n, color])
        scale.append([(i + 1) / n, color])
    return scale


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
    use_mask: bool = False,
    bg_geojson: dict = None,
    bg_meta: pd.DataFrame = None,
    bg_whiteness: float = 0.7,
) -> go.Figure:
    """
    Create an interactive binned choropleth of the County Siting Readiness Index.

    Low bins mean higher readiness; high bins mean lower readiness / greater
    deployment constraint. When ``use_mask`` is True, the clipped counties are drawn
    on top and the full county set is drawn faintly below.
    """
    n = len(edges) - 1
    labels = make_bin_labels(edges)
    colorscale = build_discrete_colorscale(n, palette)
    land_color = "rgb(255, 255, 255)" if use_mask else "rgb(243, 243, 243)"

    def prep(meta: pd.DataFrame) -> pd.DataFrame:
        merged = meta.merge(
            scores_df[['GEOID', 'composite_score', 'composite_normalized']],
            on='GEOID', how='left'
        )
        vals = merged['composite_normalized'].fillna(0.0).values
        merged['bin_idx'] = assign_bins(vals, edges)
        merged['bin_label'] = [labels[i] for i in merged['bin_idx']]
        return merged

    colorbar = dict(
        title=READINESS_LEGEND_TITLE,
        tickmode="array",
        tickvals=list(range(n)),
        ticktext=labels,
        thicknessmode="pixels", thickness=15,
        lenmode="pixels", len=min(height - 100, 320),
    ) if show_colorbar else None

    fig = go.Figure()

    if use_mask and bg_geojson is not None and bg_meta is not None:
        bg_df = prep(bg_meta)
        fig.add_trace(go.Choropleth(
            geojson=bg_geojson,
            locations=bg_df['GEOID'].tolist(),
            featureidkey="properties.GEOID",
            z=bg_df['bin_idx'].tolist(),
            colorscale=colorscale,
            zmin=-0.5, zmax=n - 0.5,
            showscale=False,
            marker_opacity=max(0.0, 1.0 - bg_whiteness),
            marker_line_width=0.2,
            marker_line_color="white",
            hoverinfo="skip",
            name="Outside mask",
        ))

    data_df = prep(counties_meta)
    customdata = np.stack([
        data_df['NAME'].astype(str).values,
        data_df['composite_normalized'].fillna(0.0).values,
        data_df['bin_label'].values,
    ], axis=-1)

    fig.add_trace(go.Choropleth(
        geojson=geojson_dict,
        locations=data_df['GEOID'].tolist(),
        featureidkey="properties.GEOID",
        z=data_df['bin_idx'].tolist(),
        colorscale=colorscale,
        zmin=-0.5, zmax=n - 0.5,
        showscale=show_colorbar,
        marker_opacity=1.0,
        marker_line_width=0.3,
        marker_line_color="white",
        colorbar=colorbar,
        customdata=customdata,
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "Readiness Index: %{customdata[1]:.3f}<br>"
            "Bin: %{customdata[2]}<extra></extra>"
        ),
        name="County Siting Readiness Index",
    ))

    fig.update_layout(
        title=title,
        height=height,
        margin=dict(l=0, r=0, t=80, b=0),
        geo=dict(
            scope="usa",
            projection_type="albers usa",
            showland=True,
            landcolor=land_color,
            showlakes=True,
            lakecolor="rgb(240, 248, 255)",
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


def export_weights_json(
    weights: Dict[str, float],
    community_mode: str,
    normalize: bool,
) -> str:
    """Export current weights configuration to a JSON string."""
    config = {
        "weights": {DISPLAY_NAMES.get(k, k): v for k, v in weights.items()},
        "community_context_layer": community_mode,
        "normalized": normalize,
    }
    return json.dumps(config, indent=2)


def export_results_csv(df: pd.DataFrame, weights: Dict[str, float]) -> str:
    """Export county results with the readiness index and raw indicator values."""
    export_cols = ['GEOID']
    if 'County' in df.columns:
        export_cols.append('County')
    export_cols += ['composite_score', 'composite_normalized']
    export_cols += [c for c in RAW_COLUMNS if c in df.columns]

    df_export = df[export_cols].copy()
    weights_str = ", ".join(
        f"{DISPLAY_NAMES.get(k, k)}={v:.3f}" for k, v in weights.items()
    )
    csv_str = df_export.to_csv(index=False)
    return f"# County Siting Readiness Index weights: {weights_str}\n{csv_str}"


# ---------------------------------------------------------------------------
# Session-state / weight controls
# ---------------------------------------------------------------------------
def initialize_weight_state() -> None:
    """Initialize slider/session state from the default scenario."""
    defaults = SCENARIOS[DEFAULT_SCENARIO]
    for col in MAIN_INDICATORS:
        value = defaults[col]
        for key in (f"weight_{col}", f"slider_{col}", f"input_{col}"):
            if key not in st.session_state:
                st.session_state[key] = value
    if "community_weight" not in st.session_state:
        st.session_state["community_weight"] = 10.0


def sync_weight_from_slider(col: str) -> None:
    value = st.session_state[f"slider_{col}"]
    st.session_state[f"weight_{col}"] = value
    st.session_state[f"input_{col}"] = value


def sync_weight_from_input(col: str) -> None:
    value = st.session_state[f"input_{col}"]
    st.session_state[f"weight_{col}"] = value
    st.session_state[f"slider_{col}"] = value


def apply_scenario() -> None:
    """Load a scenario's weights into the sliders (no-op for 'Custom')."""
    name = st.session_state.get("scenario_choice")
    if name not in SCENARIOS:
        return
    for col in MAIN_INDICATORS:
        value = SCENARIOS[name][col]
        st.session_state[f"weight_{col}"] = value
        st.session_state[f"slider_{col}"] = value
        st.session_state[f"input_{col}"] = value


def current_weights() -> Dict[str, float]:
    """Read the current main-indicator weights from session state."""
    return {col: st.session_state[f"weight_{col}"] for col in MAIN_INDICATORS}


def short_label(col: str) -> str:
    return DISPLAY_NAMES.get(col, col)


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
        "**Grid-Speed**, and **Policy-and-Permitting** readiness. The Community Context "
        "Layer is treated separately because social vulnerability can be interpreted "
        "either as a need for additional safeguards or as an indicator of economic "
        "development need.\n\n"
        "_Interpretation: **lower score = higher readiness**; "
        "**higher score = lower readiness / greater deployment constraint**._"
    )

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

    # --- Sidebar: 3. Community Context Layer ---
    st.sidebar.subheader("3. Community Context Layer")
    st.sidebar.caption(COMMUNITY_DESCRIPTION)
    community_mode = st.sidebar.radio(
        "Community Context Layer:",
        COMMUNITY_OPTIONS,
        index=0,
        key="community_mode",
    )
    community_weight = 0.0
    if community_mode != COMMUNITY_NOT_INCLUDED:
        community_weight = st.sidebar.slider(
            "Community Context Layer weight (%)",
            min_value=0.0, max_value=100.0, step=0.01,
            key="community_weight",
        )

    # --- Sidebar: 4. Weights ---
    st.sidebar.subheader("4. Weight Configuration")
    normalize_weights = st.sidebar.checkbox(
        "Normalize active weights to sum = 1",
        value=True,
        help="Active weights are normalized so they sum to 1 before computing the index.",
    )

    st.sidebar.markdown("**Adjust indicator weights:**")
    st.sidebar.markdown(
        "<style>input[type=\"number\"] { max-width: 80px; }</style>",
        unsafe_allow_html=True,
    )
    for col in MAIN_INDICATORS:
        slider_col, input_col = st.sidebar.columns([2.4, 0.9], gap="small")
        with slider_col:
            st.slider(
                short_label(col),
                min_value=0.0, max_value=100.0, step=0.01,
                key=f"slider_{col}",
                on_change=sync_weight_from_slider,
                args=(col,),
            )
        with input_col:
            st.markdown("<div style='padding-top:24px'></div>", unsafe_allow_html=True)
            st.number_input(
                " ",
                min_value=0.0, max_value=100.0, step=0.01,
                key=f"input_{col}",
                label_visibility="collapsed",
                on_change=sync_weight_from_input,
                args=(col,),
            )

    weights_main = current_weights()
    weights = dict(weights_main)
    weights[COMMUNITY_COLUMN] = community_weight if community_mode != COMMUNITY_NOT_INCLUDED else 0.0

    active_sum = sum(v for v in weights.values() if v > 0)
    if normalize_weights:
        st.sidebar.info(f"**Active weight sum:** {active_sum:.2f} → normalized to 1")
    else:
        st.sidebar.info(f"**Active weight sum:** {active_sum:.2f}")

    # --- Sidebar: 5. Map binning options ---
    st.sidebar.subheader("5. Map binning options")
    bin_mode = st.sidebar.radio("Binning mode:", ["Fixed bin size", "Number of bins"], index=0)
    bin_size = DEFAULT_BIN_SIZE
    n_bins = DEFAULT_BIN_COUNT
    if bin_mode == "Fixed bin size":
        bin_size = st.sidebar.selectbox(
            "Bin size:", BIN_SIZE_OPTIONS, index=BIN_SIZE_OPTIONS.index(DEFAULT_BIN_SIZE)
        )
    else:
        n_bins = st.sidebar.selectbox(
            "Number of bins:", BIN_COUNT_OPTIONS, index=BIN_COUNT_OPTIONS.index(DEFAULT_BIN_COUNT)
        )
    quantile_bins = st.sidebar.checkbox(
        "Use quantile bins instead of score-based",
        value=False,
        help="Default is score-based (equal-width) bins over [0, 1].",
    )

    # --- Sidebar: 6. Color palette ---
    st.sidebar.subheader("6. Color palette")
    palette_name = st.sidebar.selectbox(
        "Colorblind-friendly palette:",
        list(COLORBLIND_SCALES.keys()),
        index=list(COLORBLIND_SCALES.keys()).index(DEFAULT_PALETTE),
    )
    palette = COLORBLIND_SCALES[palette_name]

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
        "palette": palette_name,
    }

    need_first_render = "current_fig" not in st.session_state

    if update_clicked or need_first_render:
        with st.spinner("Computing County Siting Readiness Index and rendering map..."):
            results = compute_index(scores_df, weights, community_mode, normalize_weights)

            edges = compute_bin_edges(
                results['composite_normalized'].values,
                bin_mode, bin_size, n_bins, quantile_bins,
            )

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
                use_mask=use_mask,
                bg_geojson=bg_geojson if use_mask else None,
                bg_meta=bg_meta if use_mask else None,
                bg_whiteness=bg_whiteness,
            )

        st.session_state["current_fig"] = fig
        st.session_state["current_results"] = results
        st.session_state["current_weights"] = weights
        st.session_state["committed_config"] = current_config
    elif st.session_state.get("committed_config") != current_config:
        st.warning("Slider values changed. Click **Update map** to refresh results.")

    results = st.session_state["current_results"]
    committed_weights = st.session_state["current_weights"]

    # --- Statistics ---
    norm = results['composite_normalized']
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Counties", len(results))
    c2.metric("Min Index", f"{norm.min():.3f}")
    c3.metric("Mean Index", f"{norm.mean():.3f}")
    c4.metric("Max Index", f"{norm.max():.3f}")
    c5.metric("Std Dev", f"{norm.std():.3f}")

    # --- Map ---
    st.plotly_chart(st.session_state["current_fig"], use_container_width=True)

    # --- Exports ---
    exp1, exp2 = st.columns(2)
    with exp1:
        st.download_button(
            "📥 Download weights (JSON)",
            data=export_weights_json(committed_weights, community_mode, normalize_weights),
            file_name="siting_readiness_weights.json",
            mime="application/json",
        )
    with exp2:
        st.download_button(
            "📥 Download county results (CSV)",
            data=export_results_csv(results, committed_weights),
            file_name="county_siting_readiness_results.csv",
            mime="text/csv",
        )

    # --- Scenario comparison ---
    st.markdown("---")
    st.subheader("Scenario comparison")
    st.caption(
        "Compares the three default scenarios (Storage-First, Grid-Speed, "
        "Policy-and-Permitting) using consistent extent, binning, palette and legend. "
        "The current Community Context Layer setting is applied to each scenario."
    )
    if st.button("📊 Compare 3 scenarios"):
        with st.spinner("Computing scenario comparison..."):
            geojson_dict, counties_meta = build_geojson(include_territories)
            figs = {}
            for name in SCENARIO_ORDER:
                sc_weights = dict(SCENARIOS[name])
                sc_weights[COMMUNITY_COLUMN] = (
                    community_weight if community_mode != COMMUNITY_NOT_INCLUDED else 0.0
                )
                sc_results = compute_index(scores_df, sc_weights, community_mode, normalize_weights)
                sc_edges = compute_bin_edges(
                    sc_results['composite_normalized'].values,
                    bin_mode, bin_size, n_bins, quantile_bins,
                )
                figs[name] = make_choropleth_map(
                    geojson_dict, counties_meta, sc_results, sc_edges, palette,
                    title=name, height=450, show_colorbar=True,
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
