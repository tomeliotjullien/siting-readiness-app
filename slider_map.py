"""
Interactive County Siting Score Visualization Tool

This Streamlit app loads county-level siting scores and displays an interactive
choropleth map of US counties with adjustable weights for composite scoring.

Usage:
    streamlit run slider_map.py

Requirements:
    pip install streamlit pandas geopandas plotly requests shapely
"""

import streamlit as st
import pandas as pd
import geopandas as gpd
import plotly.express as px
import plotly.graph_objects as go
import requests
import json
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
from typing import Dict, Tuple
import numpy as np
import os
import tempfile

# Configuration: Column directionality
# All columns: higher value = higher risk (no inversion applied — raw CSV values used as-is)
DIRECTION_CONFIG = {
    "Social Vulnerability PCT": "higher_worse",
    "Extreme Events (Wildfires, Floodings, Storms) PCT": "higher_worse",
    "Labor Availability PCT": "higher_worse",
    "Water Availability PCT": "higher_worse",
    "Sequestration Access (EOR/Pipeline/Primacy)": "higher_worse",
    "Interconnection Queue": "higher_worse",
    "Land Cost": "higher_worse",
    "State Project Enablement Index PCT": "higher_worse",
    "Long-Haul Fiber Optics Presence": "higher_worse"
}

SCORE_COLUMNS = list(DIRECTION_CONFIG.keys())

# Color scales available
COLOR_SCALES = {
    "Red-Yellow-Green (Reversed)": "RdYlGn_r",
    "Red-Green (Reversed)": "RdGn_r",
    "Reds": "Reds",
    "Viridis": "Viridis",
    "Plasma": "Plasma",
    "Turbo": "Turbo"
}


@st.cache_data
def load_counties_geometry() -> gpd.GeoDataFrame:
    """
    Download and cache US Census county boundaries.
    Returns GeoDataFrame with GEOID as string (5-digit).
    """
    url = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip"
    
    st.info("Downloading US Census county boundaries...")
    response = requests.get(url)
    response.raise_for_status()
    
    # Extract shapefile from zip
    with ZipFile(BytesIO(response.content)) as zip_file:
        # Find the .shp file
        shp_files = [f for f in zip_file.namelist() if f.endswith('.shp')]
        if not shp_files:
            raise ValueError("No shapefile found in downloaded zip")
        
        # Extract all files to temporary location
        temp_dir = Path("temp_counties")
        temp_dir.mkdir(exist_ok=True)
        zip_file.extractall(temp_dir)
        
        # Read shapefile
        shp_path = temp_dir / shp_files[0]
        counties = gpd.read_file(shp_path)
    
    # Ensure GEOID is 5-digit string
    counties['GEOID'] = counties['GEOID'].astype(str).str.zfill(5)
    
    # Reproject to EPSG:5070 (Albers Equal Area for CONUS)
    counties = counties.to_crs("EPSG:5070")
    
    st.success(f"Loaded {len(counties)} counties")
    return counties


@st.cache_data
def build_geojson(include_territories: bool) -> tuple:
    """
    Build and cache county GeoJSON + metadata for Plotly.
    Expensive geometry ops run once per territory setting, then cached.
    """
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
    """
    True geometric intersection: clip county polygons to the CO₂ storage mask.
    Counties outside the mask disappear entirely; counties that cross the boundary
    are trimmed to their actual overlap area.
    Returns (geojson_dict, counties_meta) in the same format as build_geojson().
    Cached per file content + territory setting.
    """
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
    # Always exclude Puerto Rico (no data for that area)
    counties_gdf = counties_gdf[~counties_gdf['STATEFP'].isin(['72'])]
    if not include_territories:
        counties_gdf = counties_gdf[~counties_gdf['STATEFP'].isin(['02', '15'])]

    # True geometric intersection (clips county polygons to mask boundary)
    clipped = gpd.overlay(
        counties_gdf[['GEOID', 'NAME', 'geometry']],
        mask_gdf[['geometry']].dissolve(),  # dissolve mask to a single polygon first
        how='intersection'
    )
    # Dissolve by GEOID in case one county intersects multiple mask polygons
    clipped = clipped.dissolve(by='GEOID').reset_index()
    # NAME may be lost after dissolve — rejoin it
    name_map = counties_gdf.set_index('GEOID')['NAME']
    clipped['NAME'] = clipped['GEOID'].map(name_map)

    clipped_latlon = clipped.to_crs("EPSG:4326")
    geojson_dict = json.loads(clipped_latlon.to_json())
    counties_meta = clipped_latlon[['GEOID', 'NAME']].copy()
    return geojson_dict, counties_meta


@st.cache_data
def load_scores_csv(file_path: str, fill_na_value: float = 0.5) -> pd.DataFrame:
    """
    Load scores CSV and prepare data.
    
    Args:
        file_path: Path to CSV file
        fill_na_value: Value to fill missing scores (default 0.5 = neutral)
    
    Returns:
        DataFrame with GEOID (5-digit string) and score columns
    """
    df = pd.read_csv(file_path)
    
    # Ensure FIPS is 5-digit GEOID
    if 'FIPS' in df.columns:
        df['GEOID'] = df['FIPS'].astype(str).str.zfill(5)
    elif 'GEOID' in df.columns:
        df['GEOID'] = df['GEOID'].astype(str).str.zfill(5)
    else:
        raise ValueError("CSV must have 'FIPS' or 'GEOID' column")
    
    # Check for missing score columns
    missing_cols = [col for col in SCORE_COLUMNS if col not in df.columns]
    if missing_cols:
        st.warning(f"Missing columns in CSV: {missing_cols}")
    
    # Fill missing values
    for col in SCORE_COLUMNS:
        if col in df.columns:
            missing_count = df[col].isna().sum()
            if missing_count > 0:
                st.info(f"Filled {missing_count} missing values in '{col}' with {fill_na_value}")
                df[col] = df[col].fillna(fill_na_value)
    
    return df


@st.cache_data
def apply_directionality(df: pd.DataFrame) -> pd.DataFrame:
    """
    Copy score columns to _risk columns.
    Raw values used as-is: higher value = higher risk, no inversion.
    """
    df_risk = df.copy()
    for col in SCORE_COLUMNS:
        if col in df.columns:
            df_risk[f"{col}_risk"] = df[col]
    return df_risk


def compute_composite(
    df: pd.DataFrame,
    weights: Dict[str, float],
    normalize_weights: bool = True,
    rescale_output: bool = False
) -> pd.DataFrame:
    """
    Compute weighted composite siting risk score.
    
    Args:
        df: DataFrame with _risk columns
        weights: Dictionary of column: weight
        normalize_weights: If True, normalize weights to sum to 1
        rescale_output: If True, rescale final scores to [0,1] by min-max
    
    Returns:
        DataFrame with 'composite_score' column added
    """
    df_result = df.copy()
    
    # Get risk column names
    risk_cols = [f"{col}_risk" for col in SCORE_COLUMNS if f"{col}_risk" in df.columns]
    
    # Prepare weights array
    w = np.array([weights.get(col.replace("_risk", ""), 0.0) for col in risk_cols])
    
    # Normalize weights if requested
    if normalize_weights and w.sum() > 0:
        w = w / w.sum()
    
    # Store normalized weights for display
    df_result['weights_used'] = str({col.replace("_risk", ""): round(wt, 3) 
                                      for col, wt in zip(risk_cols, w)})
    
    # Compute composite score
    scores_array = df_result[risk_cols].values
    composite = np.dot(scores_array, w)
    
    # Rescale if requested
    if rescale_output and composite.max() > composite.min():
        composite = (composite - composite.min()) / (composite.max() - composite.min())
    
    df_result['composite_score'] = composite
    
    # Convert to percentile rank (0-1, where higher = worse)
    # rank(pct=True) automatically handles NaN and ties appropriately
    composite_percentile = pd.Series(composite).rank(pct=True).values
    df_result['composite_percentile'] = composite_percentile
    
    return df_result


def make_choropleth_map(
    geojson_dict: dict,
    counties_meta: pd.DataFrame,
    scores_df: pd.DataFrame,
    weights: Dict[str, float],
    color_scale: str = "RdYlGn_r",
    use_mask: bool = False,
    bg_geojson: dict = None,
    bg_meta: pd.DataFrame = None,
    bg_whiteness: float = 0.7,
) -> go.Figure:
    """
    Create interactive Plotly choropleth map of county composite scores.
    When use_mask=True: clipped polygons drawn on top; full county set drawn below
    with marker_opacity = 1 - bg_whiteness (0=fully colored, 1=totally white).
    """
    # Join scores to pre-built county metadata (plain DataFrame merge, very fast)
    risk_cols = [f"{col}_risk" for col in SCORE_COLUMNS if f"{col}_risk" in scores_df.columns]
    merge_cols = ['GEOID', 'composite_score', 'composite_percentile'] + risk_cols
    data_df = counties_meta.merge(scores_df[merge_cols], on='GEOID', how='left')

    land_color = "rgb(255, 255, 255)" if use_mask else "rgb(243, 243, 243)"

    # Background trace: full county outlines at low opacity when mask is active
    if use_mask and bg_geojson is not None and bg_meta is not None:
        # Merge scores onto full county list for real colors in background
        risk_cols_bg = [f"{col}_risk" for col in SCORE_COLUMNS if f"{col}_risk" in scores_df.columns]
        merge_cols_bg = ['GEOID', 'composite_score', 'composite_percentile'] + risk_cols_bg
        bg_df = bg_meta.merge(scores_df[merge_cols_bg], on='GEOID', how='left')

        fig = go.Figure()
        fig.add_trace(go.Choropleth(
            geojson=bg_geojson,
            locations=bg_df['GEOID'].tolist(),
            featureidkey="properties.GEOID",
            z=bg_df['composite_percentile'].tolist(),
            colorscale=color_scale,
            zmin=0, zmax=1,
            showscale=False,
            marker_opacity=max(0.0, 1.0 - bg_whiteness),
            marker_line_width=0.2,
            marker_line_color="white",
            hoverinfo="skip",
            name="Outside mask",
        ))
        # Main clipped choropleth on top (always fully opaque)
        risk_cols = [f"{col}_risk" for col in SCORE_COLUMNS if f"{col}_risk" in scores_df.columns]
        merge_cols = ['GEOID', 'composite_score', 'composite_percentile'] + risk_cols
        data_df = counties_meta.merge(scores_df[merge_cols], on='GEOID', how='left')
        fig.add_trace(go.Choropleth(
            geojson=geojson_dict,
            locations=data_df['GEOID'].tolist(),
            featureidkey="properties.GEOID",
            z=data_df['composite_percentile'].tolist(),
            colorscale=color_scale,
            zmin=0, zmax=1,
            marker_opacity=1.0,
            marker_line_width=0.3,
            marker_line_color="white",
            colorbar=dict(
                title="Risk Percentile<br>(0-100%)",
                thicknessmode="pixels", thickness=15,
                lenmode="pixels", len=300,
                tickformat=".0%"
            ),
            hovertext=data_df['NAME'],
            hovertemplate="<b>%{hovertext}</b><br>Risk Percentile: %{z:.1%}<extra></extra>",
            name="CO₂ storage counties",
        ))
        fig.update_layout(
            title=f"US County Siting Risk Score (Higher = Worse)<br><sub>Weights: {format_weights_short(weights)}</sub>",
            height=700,
            margin=dict(l=0, r=0, t=80, b=0),
            geo=dict(
                scope="usa",
                projection_type="albers usa",
                showland=True,
                landcolor=land_color,
                showlakes=True,
                lakecolor="rgb(240, 248, 255)",
            )
        )
        return fig

    # --- No mask: standard single-trace choropleth ---
    risk_cols = [f"{col}_risk" for col in SCORE_COLUMNS if f"{col}_risk" in scores_df.columns]
    merge_cols = ['GEOID', 'composite_score', 'composite_percentile'] + risk_cols
    data_df = counties_meta.merge(scores_df[merge_cols], on='GEOID', how='left')

    fig = px.choropleth(
        data_df,
        geojson=geojson_dict,
        featureidkey="properties.GEOID",
        locations='GEOID',
        color='composite_percentile',
        color_continuous_scale=color_scale,
        range_color=[0, 1],
        hover_name='NAME',
        hover_data={col: ':.3f' for col in data_df.columns if col not in ('GEOID', 'NAME')},
        labels={'composite_percentile': 'Risk Percentile'},
        title=f"US County Siting Risk Score (Higher = Worse)<br><sub>Weights: {format_weights_short(weights)}</sub>"
    )

    fig.update_geos(
        scope="usa",
        projection_type="albers usa"
    )

    fig.update_layout(
        height=700,
        margin=dict(l=0, r=0, t=80, b=0),
        geo=dict(
            showland=True,
            landcolor=land_color,
            showlakes=True,
            lakecolor="rgb(240, 248, 255)"
        ),
        coloraxis_colorbar=dict(
            title="Risk Percentile<br>(0-100%)",
            thicknessmode="pixels",
            thickness=15,
            lenmode="pixels",
            len=300,
            tickformat=".0%"
        )
    )

    return fig


def format_weights_short(weights: Dict[str, float]) -> str:
    """Format weights for display in title (shortened column names)."""
    short_names = {
        "Social Vulnerability PCT": "SocVuln",
        "Extreme Events (Wildfires, Floodings, Storms) PCT": "ClimEvents",
        "Labor Availability PCT": "Labor",
        "Water Availability PCT": "Water",
        "Sequestration Access (EOR/Pipeline/Primacy)": "Sequest",
        "Interconnection Queue": "Intercon",
        "Land Cost": "LandCost",
        "State Project Enablement Index PCT": "StateIdx",
        "Long-Haul Fiber Optics Presence": "Fiber"
    }
    
    formatted = ", ".join([f"{short_names.get(k, k)}: {v:.2f}".replace(',', '.') 
                          for k, v in weights.items() if v > 0])
    return formatted if formatted else "All zeros"


def export_weights_json(weights: Dict[str, float], normalize: bool) -> str:
    """Export current weights configuration to JSON string."""
    config = {
        "weights": weights,
        "normalized": normalize,
        "direction_config": DIRECTION_CONFIG
    }
    return json.dumps(config, indent=2)


def export_results_csv(df: pd.DataFrame, weights: Dict[str, float]) -> str:
    """Export county results with composite scores and percentile rankings to CSV string."""
    # Select relevant columns
    export_cols = ['GEOID', 'County', 'composite_score', 'composite_percentile']
    
    # Add risk columns
    for col in SCORE_COLUMNS:
        risk_col = f"{col}_risk"
        if risk_col in df.columns:
            export_cols.append(risk_col)
    
    df_export = df[export_cols].copy()
    
    # Add weights as metadata in header comment
    weights_str = ", ".join([f"{k}={v:.3f}" for k, v in weights.items()])
    
    # Convert to CSV
    csv_str = df_export.to_csv(index=False)
    csv_with_header = f"# Weights: {weights_str}\n{csv_str}"
    
    return csv_with_header


def initialize_weight_state() -> None:
    """Initialize widget/session state for all weight controls once."""
    default_weight = 100.0 / len(SCORE_COLUMNS)

    for col in SCORE_COLUMNS:
        weight_key = f"weight_{col}"
        slider_key = f"slider_{col}"
        input_key = f"input_{col}"
        toggle_key = f"toggle_{col}"

        if weight_key not in st.session_state:
            st.session_state[weight_key] = default_weight
        if slider_key not in st.session_state:
            st.session_state[slider_key] = st.session_state[weight_key]
        if input_key not in st.session_state:
            st.session_state[input_key] = st.session_state[weight_key]
        if toggle_key not in st.session_state:
            st.session_state[toggle_key] = False


def sync_weight_from_slider(col: str) -> None:
    """Keep the number input and stored weight aligned with the slider."""
    value = st.session_state[f"slider_{col}"]
    st.session_state[f"weight_{col}"] = value
    st.session_state[f"input_{col}"] = value


def sync_weight_from_input(col: str) -> None:
    """Keep the slider and stored weight aligned with the number input."""
    value = st.session_state[f"input_{col}"]
    st.session_state[f"weight_{col}"] = value
    st.session_state[f"slider_{col}"] = value


def handle_solo_toggle(col: str) -> None:
    """When a solo toggle is enabled, set that weight to 100 and all others to 0."""
    if not st.session_state.get(f"toggle_{col}"):
        return

    for other in SCORE_COLUMNS:
        value = 100.0 if other == col else 0.0
        st.session_state[f"weight_{other}"] = value
        st.session_state[f"slider_{other}"] = value
        st.session_state[f"input_{other}"] = value
        st.session_state[f"toggle_{other}"] = (other == col)


def set_all_weights(value: float) -> None:
    """Set every weight control to the same percentage value."""
    for col in SCORE_COLUMNS:
        st.session_state[f"weight_{col}"] = value
        st.session_state[f"slider_{col}"] = value
        st.session_state[f"input_{col}"] = value
        st.session_state[f"toggle_{col}"] = False


def main():
    """Main Streamlit application."""
    
    st.set_page_config(
        page_title="County Siting Risk Map",
        page_icon="🗺️",
        layout="wide"
    )
    
    st.title("🗺️ Interactive County Siting Score Visualization")
    st.markdown("""
    This tool visualizes county-level siting risk scores across the US.
    Adjust the weights for each factor using the sliders to see how the composite risk changes.
    """)
    
    # Sidebar for controls
    st.sidebar.header("⚙️ Configuration")
    
    # File upload or path input
    st.sidebar.subheader("1. Load Data")
    
    # Check if default file exists
    default_csv = Path(__file__).parent / "county_column_scores.csv"
    
    upload_option = st.sidebar.radio(
        "Data source:",
        ["Use default file", "Upload CSV"]
    )
    
    csv_file = None
    if upload_option == "Upload CSV":
        csv_file = st.sidebar.file_uploader(
            "Upload county scores CSV",
            type=['csv']
        )
    elif default_csv.exists():
        csv_file = str(default_csv)
    else:
        st.error(f"Default file not found: {default_csv}")
        st.stop()
    
    if csv_file is None:
        st.info("Please upload a CSV file to begin.")
        st.stop()
    
    # Load data
    try:
        with st.spinner("Loading county scores..."):
            scores_df = load_scores_csv(csv_file)
        
        st.sidebar.success(f"✓ Loaded {len(scores_df)} counties with scores")

    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.stop()
    
    # Apply directionality (cached — only runs once per CSV)
    scores_df = apply_directionality(scores_df)
    
    # Sidebar controls
    st.sidebar.subheader("2. Weight Configuration")
    initialize_weight_state()
    
    # Normalize toggle
    normalize_weights = st.sidebar.checkbox(
        "Normalize weights to sum = 1",
        value=True,
        help="When enabled, weights are automatically normalized so their sum equals 1"
    )
    
    # Preset buttons
    st.sidebar.markdown("**Quick Presets:**")
    col1, col2 = st.sidebar.columns(2)
    
    if col1.button("Equal Weights"):
        set_all_weights(100.0 / len(SCORE_COLUMNS))

    if col2.button("Reset to 50"):
        set_all_weights(50.0)
    
    # Weight sliders
    st.sidebar.markdown("**Adjust Weights:**")
    weights = {}
    
    for col in SCORE_COLUMNS:
        # Create shorter label for slider
        label = (col
                 .replace(" (Wildfires, Floodings, Storms) PCT", "")
                 .replace(" Availability PCT", "")
                 .replace(" Vulnerability PCT", " Vuln.")
                 .replace(" PCT", ""))
        
        solo_col, slider_col, input_col = st.sidebar.columns([0.8, 2, 0.8], gap="small")

        with solo_col:
            st.markdown("<div style='padding-top:22px'></div>", unsafe_allow_html=True)
            st.toggle(
                "Solo",
                key=f"toggle_{col}",
                help=f"Toggle: set {label} = 100, all others = 0",
                on_change=handle_solo_toggle,
                args=(col,)
            )

        # Slider in middle column (0-100 percentage scale)
        with slider_col:
            st.slider(
                label,
                min_value=0.0,
                max_value=100.0,
                step=0.01,
                key=f"slider_{col}",
                help=f"Direction: {DIRECTION_CONFIG[col]}",
                on_change=sync_weight_from_slider,
                args=(col,)
            )
        
        # Number input in right column (0-100 percentage scale)
        with input_col:
            # Display the current weight value as percentage, sync with slider
            # Add custom CSS to make the input field smaller
            st.markdown("""
                <style>
                    input[type="number"] { max-width: 70px; }
                </style>
            """, unsafe_allow_html=True)
            st.number_input(
                " ",
                min_value=0.0,
                max_value=100.0,
                step=0.01,
                key=f"input_{col}",
                label_visibility="collapsed",
                help="Manual weight input (0-100)",
                on_change=sync_weight_from_input,
                args=(col,)
            )
        
        weights[col] = st.session_state[f"weight_{col}"]
    
    # Display weight sum (as percentage 0-900 for 9 columns)
    weight_sum = sum(weights.values())
    if normalize_weights:
        st.sidebar.info(f"**Weight sum:** {weight_sum:.0f} → normalized to 100")
    else:
        st.sidebar.info(f"**Weight sum:** {weight_sum:.0f}")
    
    # Calculate and display contribution percentages
    if weight_sum > 0:
        if normalize_weights:
            contributions = {col: (w / weight_sum) * 100 for col, w in weights.items()}
        else:
            contributions = {col: (w / weight_sum) * 100 for col, w in weights.items()}
        
        st.sidebar.subheader("📊 Column Contributions")
        
        # Create a nicer display of contributions
        for col in SCORE_COLUMNS:
            contrib_pct = contributions[col]
            # Only show columns with >0.1% contribution
            if contrib_pct > 0.1:
                short_name = (col
                              .replace(" (Wildfires, Floodings, Storms) PCT", "")
                              .replace(" Availability PCT", "")
                              .replace(" Vulnerability PCT", " Vuln.")
                              .replace(" PCT", ""))
                st.sidebar.write(f"**{short_name}:** {contrib_pct:.1f}%".replace(',', '.'))
    
    # Map options
    st.sidebar.subheader("3. Map Options")
    
    color_scale = st.sidebar.selectbox(
        "Color scale:",
        options=list(COLOR_SCALES.keys()),
        index=0
    )
    
    include_territories = st.sidebar.checkbox(
        "Include AK/HI",
        value=False,
        help="Include Alaska and Hawaii in the map (Puerto Rico excluded due to lack of data)"
    )
    
    rescale_output = st.sidebar.checkbox(
        "Rescale scores to [0,1]",
        value=False,
        help="Apply min-max rescaling to final composite scores"
    )

    # CO₂ storage mask
    st.sidebar.markdown("**CO₂ Underground Storage Mask:**")
    mask_zip_path = Path(__file__).parent / "storage_mask.zip"
    use_mask = False
    bg_whiteness = 0.7
    clipped_geojson = None
    clipped_meta = None
    
    if mask_zip_path.exists():
        apply_mask = st.sidebar.checkbox(
            "Apply CO₂ storage mask overlay",
            value=False,
            help="Clips county polygons to confirmed CO₂ storage sites. Disable this while adjusting weights for faster re-renders."
        )
        
        if apply_mask:
            try:
                with open(mask_zip_path, "rb") as f:
                    mask_bytes = f.read()
                with st.spinner("Clipping counties to CO₂ storage mask..."):
                    clipped_geojson, clipped_meta = build_clipped_geojson(mask_bytes, include_territories)
                n_clipped = len(clipped_meta)
                st.sidebar.success(f"✓ {n_clipped} county fragments inside CO₂ storage zones")
                use_mask = True
                bg_whiteness = st.sidebar.slider(
                    "Background counties (outside mask)",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.7,
                    step=0.05,
                    help="0 = full colors visible, 1 = totally white/hidden"
                )
            except Exception as e:
                st.sidebar.error(f"Could not load mask: {e}")
                use_mask = False
    else:
        st.sidebar.info("📦 storage_mask.zip not found in app directory")
    
    # Export options
    st.sidebar.subheader("4. Export")
    
    if st.sidebar.button("📥 Export Weights (JSON)"):
        json_str = export_weights_json(weights, normalize_weights)
        st.sidebar.download_button(
            label="Download weights.json",
            data=json_str,
            file_name="siting_weights.json",
            mime="application/json"
        )
    
    # Main panel - compute and display
    with st.spinner("Computing composite scores..."):
        # Convert weights from 0-100 to 0-1 for compute_composite
        weights_normalized = {col: w / 100.0 for col, w in weights.items()}
        scores_with_composite = compute_composite(
            scores_df,
            weights_normalized,
            normalize_weights=normalize_weights,
            rescale_output=rescale_output
        )
    
    # Statistics
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Counties", len(scores_with_composite))
    with col2:
        st.metric("Min Percentile", f"{scores_with_composite['composite_percentile'].min():.1%}")
    with col3:
        st.metric("Mean Percentile", f"{scores_with_composite['composite_percentile'].mean():.1%}")
    with col4:
        st.metric("Max Percentile", f"{scores_with_composite['composite_percentile'].max():.1%}")
    with col5:
        st.metric("Std Dev", f"{scores_with_composite['composite_percentile'].std():.3f}")
    
    # Build GeoJSON (cached per territory setting — skips geometry work on re-runs)
    with st.spinner("Preparing map geometry..."):
        geojson_dict, counties_meta = build_geojson(include_territories)
    bg_geojson, bg_meta = geojson_dict, counties_meta
    if use_mask and clipped_geojson is not None and clipped_meta is not None:
        geojson_dict, counties_meta = clipped_geojson, clipped_meta

    # Create and display map
    with st.spinner("Rendering map..."):
        fig = make_choropleth_map(
            geojson_dict,
            counties_meta,
            scores_with_composite,
            weights,
            color_scale=COLOR_SCALES[color_scale],
            use_mask=use_mask,
            bg_geojson=bg_geojson if use_mask else None,
            bg_meta=bg_meta if use_mask else None,
            bg_whiteness=bg_whiteness,
        )
    
    st.plotly_chart(fig, use_container_width=True)
    
    # Export results
    if st.button("📥 Export County Results (CSV)"):
        csv_str = export_results_csv(scores_with_composite, weights)
        st.download_button(
            label="Download results.csv",
            data=csv_str,
            file_name="county_siting_results.csv",
            mime="text/csv"
        )
    
    # Display sample data
    with st.expander("📊 View Sample Data"):
        display_cols = ['GEOID', 'County', 'composite_score', 'composite_percentile'] + [
            f"{col}_risk" for col in SCORE_COLUMNS if f"{col}_risk" in scores_with_composite.columns
        ]
        
        # Show top 10 and bottom 10 by composite score
        st.markdown("**Top 10 Highest Risk Counties:**")
        st.dataframe(
            scores_with_composite[display_cols]
            .nlargest(10, 'composite_score')
            .reset_index(drop=True),
            hide_index=True
        )
        
        st.markdown("**Top 10 Lowest Risk Counties:**")
        st.dataframe(
            scores_with_composite[display_cols]
            .nsmallest(10, 'composite_score')
            .reset_index(drop=True),
            hide_index=True
        )
    
    # Footer
    st.markdown("---")
    st.markdown("""
    **About:** This tool helps identify optimal county-level sites by combining multiple risk factors.
    Higher composite scores (red) indicate higher overall siting risk.
    
    **Scoring:** Counties are ranked as percentiles (0-100%), where a higher percentile means worse siting conditions.
    This ensures an even distribution of scores across all counties regardless of the weighting scheme.
    
    **Directionality:** All factors use raw CSV values as-is (higher = higher risk, no inversion applied).
    """)


if __name__ == "__main__":
    main()
