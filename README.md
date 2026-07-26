# County Siting Readiness Index — Interactive Visualization

A Streamlit-based interactive tool for visualizing the **County Siting Readiness Index**
across United States counties.

The index is interpreted as:

- **lower score = higher readiness**
- **higher score = lower readiness / greater deployment constraint**

## Features

- **Three default scenarios**: Storage-First, Grid-Speed, and Policy-and-Permitting readiness
- **Nine indicators grouped into three categories**: Execution and Community Risk, Speed-to-Deploy Enablers, and Operational Risk and Resource Resilience
- **Community Context Layer as a normal weighted column**: set its weight to zero to ignore it, and interpret it as either a Social Vulnerability or an Economic Development Need perspective
- **Solo toggles**: isolate a single indicator (weight = 100, all others = 0) for a clear single-variable view
- **Adjustable indicator weights**: active weights are normalized to sum to 1
- **Deferred rendering**: sliders update session state only; the map recomputes when you click **Update map**
- **Score display choice**: continuous min-max normalized scores, or a binned view of those same scores
- **Flexible binning**: fixed bin size or number of bins, score-based (default) or quantile
- **Green→red default color scale** with colorblind-friendly alternatives
- **Scenario comparison**: view all three default scenarios side by side
- **Export**: save weight configurations (JSON) and county results (CSV)

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

Or install individually:
```bash
pip install streamlit pandas geopandas plotly requests shapely
```

### 2. Run the App

```bash
streamlit run slider_map.py
```

The app opens at `http://localhost:8501`.

### 3. Load Your Data

The app expects a CSV with:
- `County` — County name
- `FIPS` or `GEOID` — FIPS code (zero-padded to 5 digits)
- Indicator columns (values 0–1), using the raw CSV column names:
  1. `Community Context PCT` (optional Community Context Layer)
  2. `State Project Enablement Index PCT`
  3. `Interconnection Queue`
  4. `Relevant Workforce Availability`
  5. `Land Cost`
  6. `Long-Haul Fiber Optics Presence`
  7. `Extreme Events (Wildfires, Floodings, Storms) PCT`
  8. `Water Availability`
  9. `Sequestration Access (EOR/Pipeline/Primacy)`

Place your CSV as `county_column_scores.csv` in the same directory, or use the upload feature.

The backend keeps the raw CSV column names; the UI shows clean display names via a
`DISPLAY_NAMES` mapping (for example, `Relevant Workforce Availability` → "Labor Availability").

## How It Works

### Scenarios

The three default scenarios set indicator weights (percentages that sum to 100). The
Community Context Layer weight is 0 in every default scenario; the user can raise it and
choose how to interpret it.

| Indicator | Storage-First | Grid-Speed | Policy-and-Permitting |
|---|---|---|---|
| Community Context Layer | 0.00% | 0.00% | 0.00% |
| State Project Enablement | 16.67% | 19.44% | 22.22% |
| Interconnection Queue | 19.44% | 22.22% | 19.44% |
| Labor Availability | 5.56% | 13.89% | 11.11% |
| Land Cost | 8.33% | 11.11% | 8.33% |
| Long-Haul Fiber Optic | 2.78% | 16.67% | 13.89% |
| Extreme Events | 11.11% | 2.78% | 2.78% |
| Water Availability | 13.89% | 5.56% | 5.56% |
| Sequestration Access | 22.22% | 8.33% | 16.67% |

### Indicator categories

The nine indicators are grouped into three categories:

- **Execution and Community Risk** — Community Context Layer, State Project Enablement.
  Captures the likelihood of siting friction and permitting delay and the broader policy
  and institutional climate for project execution.
- **Speed-to-Deploy Enablers** — Interconnection Queue, Labor Availability, Land Cost,
  Long-Haul Fiber Optic. Captures whether required inputs and build capabilities are
  already present.
- **Operational Risk and Resource Resilience** — Extreme Events, Water Availability,
  Sequestration Access. Captures the ability to operate reliably over time.

### Community Context Layer

The Community Context Layer is a normal weighted column. Set its weight to zero to ignore
it, or give it a weight and choose the interpretation:

- **Social Vulnerability perspective**: higher social vulnerability increases the index
  (lower readiness / greater need for safeguards). Raw value used as-is.
- **Economic Development Need perspective**: the value is reversed with `1 - score`, so
  higher social vulnerability is interpreted as greater economic development need / higher priority.

### Index Calculation

```
Readiness Index = Σ(weight_i × value_i)
```

Active weights are normalized to sum to 1 before scoring, then the composite is
min-max normalized to `[0, 1]`.

### Map Visualization

- Choose **Continuous** (min-max normalized scores) or **Binned** display of those same scores
- Binned mode uses **score-based bins** by default (fixed bin size = 0.2), with optional
  quantile bins and a choice of number of bins
- Default **green→red** color scale (green = higher readiness, red = lower readiness), with
  colorblind-friendly alternatives (Cividis, Viridis, Blues, YlOrBr, Plasma)
- Legend: **County Siting Readiness Index — Lower = higher readiness**
- Hover shows county name, readiness index, and (in binned mode) the bin label

## Controls

- **Scenario**: pick a default scenario (sets indicator weights) or Custom
- **Community Context Layer**: interpret as Social Vulnerability or Economic Development Need
- **Weight sliders** (grouped by category) with **Solo** toggles to isolate one indicator
- **Score display**: Continuous or Binned
- **Map binning options**: fixed bin size or number of bins; optional quantile bins
- **Color scale**: green→red default plus colorblind-friendly options
- **Update map**: recompute the map and rankings (sliders alone do not refresh the map)
- **Compare 3 scenarios**: render all three default scenarios together

## Data Requirements

### County Boundaries

The app downloads US Census cartographic boundary files once and caches them:

```
https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip
```

### Missing Data Handling

- Missing indicator values are filled with 0.5
- The app reports how many values were filled per column

## Performance

- County geometry is downloaded once and cached
- The map recomputes only when **Update map** is clicked, keeping slider interaction fast
- Projection: EPSG:5070 (Albers Equal Area) for accurate CONUS area representation

## Technical Details

- **Map Library**: Plotly for interactive visualization
- **Framework**: Streamlit for reactive UI
- **Geometry**: US Census 2023 cartographic boundaries (1:500k scale)

## License & Citation

If you use this tool in your research, please cite appropriately and acknowledge:
- US Census Bureau for county boundaries
- Your own indicator data source

---

**Framework**: Streamlit + Plotly + GeoPandas
