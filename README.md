# Interactive County Siting Score Visualization

A Streamlit-based interactive tool for visualizing county-level siting risk scores across the United States.

## Features

- **9 Adjustable Weight Sliders**: Control the importance of each siting factor
- **Live-Updating Choropleth Map**: See changes instantly as you adjust weights
- **Smart Directionality**: Automatically handles "higher=better" vs "higher=worse" conventions
- **Interactive Hover**: View detailed scores for each county
- **Export Capabilities**: Save your weight configurations and results
- **Territory Toggle**: Include/exclude Alaska, Hawaii, and Puerto Rico
- **Multiple Color Scales**: Choose your preferred visualization style

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements_slider_map.txt
```

Or install individually:
```bash
pip install streamlit pandas geopandas plotly requests shapely
```

### 2. Run the App

```bash
streamlit run slider_map.py
```

The app will open in your default web browser at `http://localhost:8501`

### 3. Load Your Data

The app expects a CSV file with the following columns:
- `County` - County name (e.g., "Autauga County, AL")
- `FIPS` or `GEOID` - 4 or 5-digit FIPS code (will be zero-padded to 5 digits)
- 9 score columns (values 0-1):
  1. Social Vulnerability
  2. Climate Vulnerability
  3. Labor Availability
  4. Water Availability
  5. Sequestration Access (EOR/Pipeline/Primacy)
  6. Interconnection Queue
  7. Land Cost
  8. State Project Enablement Index
  9. Fiber Gigabit Availability (1 - %)

Place your CSV as `county_column_scores.csv` in the same directory, or use the upload feature in the app.

## How It Works

### Directionality Handling

The app converts all metrics to a "higher = worse" (risk) convention:
- **Higher = Worse** (kept as-is): Social Vulnerability, Climate Vulnerability, Sequestration Access, Interconnection Queue, Land Cost, State Project Enablement Index, Fiber Gigabit Availability
- **Higher = Better** (inverted): Labor Availability, Water Availability

When "higher=better" columns are inverted using `risk = 1 - value`.

### Composite Score Calculation

```
Composite Score = Σ(weight_i × risk_i)
```

When "Normalize weights to sum = 1" is enabled (default), weights are automatically scaled so they sum to 1.

### Map Visualization

- **Red** = Higher siting risk (worse)
- **Green** = Lower siting risk (better)
- Color scale is customizable
- Hover over counties to see:
  - County name and state
  - Composite risk score
  - Individual component scores
  - GEOID

## Controls

### Weight Sliders
- Range: 0.0 to 1.0
- Step: 0.01
- Adjust to emphasize different factors in your siting analysis

### Quick Presets
- **Equal Weights**: Set all weights to equal values (1/9 each)
- **Reset to 0.5**: Set all weights to 0.5

### Map Options
- **Color Scale**: Choose from 6 different color schemes
- **Include AK/HI/PR**: Toggle visibility of Alaska, Hawaii, and Puerto Rico
- **Rescale scores to [0,1]**: Apply min-max normalization to final scores

### Export Functions
- **Export Weights (JSON)**: Save your current weight configuration
- **Export County Results (CSV)**: Download composite scores for all counties

## Data Requirements

### County Boundaries
The app automatically downloads US Census cartographic boundary files from:
```
https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip
```

This happens once and is cached for subsequent runs.

### Missing Data Handling
- Missing values in score columns are filled with 0.5 (neutral risk)
- The app reports how many values were filled for each column

## Performance Optimization

- County geometry is downloaded once and cached
- Only composite scores are recalculated when sliders change
- Map uses EPSG:5070 (Albers Equal Area) for accurate CONUS visualization
- Plotly enables interactive zooming and panning

## Troubleshooting

### Map doesn't appear
- Check that you have a stable internet connection (for initial geometry download)
- Ensure all required packages are installed
- Check the browser console for JavaScript errors

### CSV loading errors
- Verify your CSV has a `FIPS` or `GEOID` column
- Ensure score column names match exactly (case-sensitive)
- Check that score values are numeric and in the range [0, 1]

### Slow performance
- Disable "Include AK/HI/PR" if not needed
- Use a simpler color scale
- Close other browser tabs running Streamlit apps

## Technical Details

- **Projection**: EPSG:5070 (Albers Equal Area) for accurate area representation
- **Map Library**: Plotly for interactive visualization
- **Framework**: Streamlit for reactive UI
- **Geometry**: US Census 2023 cartographic boundaries (1:500k scale)

## Advanced Usage

### Custom CSV Path
Modify the `default_csv` variable in the code or use the upload feature.

### Modifying Directionality
Edit the `DIRECTION_CONFIG` dictionary in the code to change which columns are inverted.

### Adding New Score Columns
1. Add the column name to `DIRECTION_CONFIG` with appropriate direction
2. Ensure your CSV includes the new column
3. The app will automatically create a slider for it

## License & Citation

If you use this tool in your research, please cite appropriately and acknowledge the data sources:
- US Census Bureau for county boundaries
- Your own siting score data source

---

**Created**: February 2026  
**Framework**: Streamlit + Plotly + GeoPandas
