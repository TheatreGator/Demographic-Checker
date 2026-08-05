import streamlit as st
import pandas as pd
import requests
import plotly.express as px
import math

# Function to get LSOA, Ward, IMD, and Coordinates from Postcodes.io API
@st.cache_data
def get_postcode_data(postcodes):
    url = "https://api.postcodes.io/postcodes"
    results = {}
    
    # API only accepts batches of 100
    for i in range(0, len(postcodes), 100):
        batch = postcodes[i:i+100]
        response = requests.post(url, json={"postcodes": batch}).json()
        
        if response["status"] == 200:
            for item in response["result"]:
                if item["result"]:
                    pc = item["query"]
                    
                    # Extract Geo and Demographic Data
                    country = item["result"].get("country", "")
                    rank = item["result"].get("index_of_multiple_deprivation")
                    
                    # Calculate Decile based on the specific country's total LSOAs/Data Zones
                    decile = "Unknown"
                    if rank:
                        try:
                            if country == "England":
                                decile = math.ceil(rank / 3284.4) 
                            elif country == "Scotland":
                                decile = math.ceil(rank / 697.6)   
                            elif country == "Wales":
                                decile = math.ceil(rank / 190.9)   
                            elif country == "Northern Ireland":
                                decile = math.ceil(rank / 89.0)    
                        except TypeError:
                            pass

                    results[pc] = {
                        "Ward": item["result"].get("admin_ward", "Unknown"),
                        "LSOA": item["result"].get("lsoa", "Unknown"),
                        "Country": country,
                        "IMD_Rank": rank if rank else "Unknown",
                        "IMD_Decile": decile,
                        "Latitude": item["result"].get("latitude"),
                        "Longitude": item["result"].get("longitude")
                    }
    return results

st.set_page_config(page_title="Sales Demographics Analyzer", layout="wide")
st.title("📊 UK Sales Demographics & Geo-Analyzer")
st.write("Upload your sales data to map postcodes to LSOAs, Wards, IMD Deprivation Deciles, and Geographic Hotspots.")

# File uploader
uploaded_file = st.file_uploader("Upload Sales Data (CSV)", type="csv")

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file)
    
    st.write("### Preview of Uploaded Data")
    st.dataframe(df.head())
    
    # Select columns
    st.sidebar.header("Configuration")
    postcode_col = st.sidebar.selectbox("Select Postcode Column", df.columns)
    
    # Ensure only numeric columns are selected for sales data to prevent mapping errors
    numeric_columns = df.select_dtypes(include=['number']).columns.tolist()
    if not numeric_columns:
        st.error("Your dataset must contain at least one numeric column for sales/value.")
        st.stop()
    sales_col = st.sidebar.selectbox("Select Sales/Value Column", numeric_columns)
    
    if st.button("Analyze Geographies & Demographics"):
        with st.spinner("Cleaning data and fetching locations from Postcodes.io..."):
            
            original_row_count = len(df)
            
            # 1. STRICT PRE-CLEANING: Drop actual NA floats first, then convert to string
            clean_series = df[postcode_col].dropna().astype(str)
            clean_series = clean_series.str.replace(r'\s+', '', regex=True).str.upper()
            
            # Filter out string representations of nulls
            valid_postcodes_series = clean_series[~clean_series.isin(['NAN', 'NULL', 'NONE', ''])]
            
            # Convert to a strict list of strings to prevent JSON errors
            unique_postcodes = [str(x) for x in valid_postcodes_series.unique()]
            
            # Fetch data from API
            geo_data = get_postcode_data(unique_postcodes)
            
            # 2. MAP DATA (Using a batch approach to prevent pandas fragmentation warnings)
            mapped_rows = []
            for pc in df[postcode_col]:
                # Re-clean the individual row's postcode to ensure it matches the API lookup key
                clean_pc = str(pc).replace(' ', '').upper() if pd.notna(pc) else ""
                
                # Fetch dictionary of results or fallback to defaults
                mapped_rows.append(geo_data.get(clean_pc, {
                    "Ward": "Unknown", 
                    "LSOA": "Unknown", 
                    "Country": "Unknown",
                    "IMD_Rank": "Unknown", 
                    "IMD_Decile": "Unknown", 
                    "Latitude": None, 
                    "Longitude": None
                }))
                
            # Concatenate new data to original dataframe efficiently
            geo_df = pd.DataFrame(mapped_rows, index=df.index)
            df = pd.concat([df, geo_df], axis=1)
            
            # 3. POST-FILTERING: Omit rows where Ward is "Unknown"
            df = df[df['Ward'] != "Unknown"].copy()
            
            st.success("Geographic and Demographic mapping complete!")
            
            # Notify user of how many bad postcodes were removed
            rows_omitted = original_row_count - len(df)
            if rows_omitted > 0:
                st.warning(f"⚠️ Cleaned up data: Omitted {rows_omitted} row(s) that contained invalid or unmappable postcodes.")
            
            # ---------------------
            # INTERACTIVE MAP
            # ---------------------
            st.write("### Interactive Sales Hotspots")
            
            # Drop rows where coordinates are missing before mapping
            df_map = df.dropna(subset=['Latitude', 'Longitude']).copy()
            
            if not df_map.empty:
                # Group by Postcode to aggregate sales for the map bubbles
                map_grouped = df_map.groupby([postcode_col, 'Latitude', 'Longitude', 'Ward', 'IMD_Decile'])[sales_col].sum().reset_index()
                
                # Create the map using the updated scatter_map function
                fig_map = px.scatter_map(
                    map_grouped, 
                    lat="Latitude", 
                    lon="Longitude", 
                    size=sales_col,
                    hover_name=postcode_col,
                    hover_data={"Ward": True, "IMD_Decile": True, "Latitude": False, "Longitude": False},
                    color_discrete_sequence=["#FF4B4B"],
                    zoom=5, 
                    height=550
                )
                fig_map.update_layout(map_style="carto-positron", margin={"r":0,"t":0,"l":0,"b":0})
                st.plotly_chart(fig_map, use_container_width=True)
            else:
                st.warning("Could not map locations. No valid coordinates found for the provided postcodes.")
                
            st.markdown("---")
            
            # Layout for basic metrics
            col1, col2 = st.columns(2)
            
            # Ward Analysis
            with col1:
                st.write("### Sales by Ward")
                ward_sales = df.groupby('Ward')[sales_col].sum().reset_index().sort_values(by=sales_col, ascending=False)
                fig_ward = px.bar(ward_sales.head(10), x='Ward', y=sales_col, title="Top 10 Wards by Sales")
                st.plotly_chart(fig_ward, use_container_width=True)
                
            # LSOA Analysis
            with col2:
                st.write("### Sales by LSOA")
                lsoa_sales = df.groupby('LSOA')[sales_col].sum().reset_index().sort_values(by=sales_col, ascending=False)
                fig_lsoa = px.bar(lsoa_sales.head(10), x='LSOA', y=sales_col, title="Top 10 LSOAs by Sales")
                st.plotly_chart(fig_lsoa, use_container_width=True)
                
            st.markdown("---")
            
            # IMD Demographic Analysis
            st.write("### Deprivation (IMD) Analysis")
            st.info("IMD Deciles group areas into 10 bands. **Decile 1** represents the 10% most deprived areas nationally, while **Decile 10** represents the 10% least deprived.")
            
            df_imd = df[df['IMD_Decile'] != "Unknown"].copy()
            if not df_imd.empty:
                df_imd['IMD_Decile'] = df_imd['IMD_Decile'].astype(int)
                imd_sales = df_imd.groupby('IMD_Decile')[sales_col].sum().reset_index()
                
                all_deciles = pd.DataFrame({'IMD_Decile': range(1, 11)})
                imd_sales = pd.merge(all_deciles, imd_sales, on='IMD_Decile', how='left').fillna(0)
                
                fig_imd = px.bar(imd_sales, x='IMD_Decile', y=sales_col, 
                               title="Sales Volume Distribution by Deprivation Decile",
                               labels={'IMD_Decile': 'Deprivation Decile (1 = Most Deprived)', sales_col: 'Total Sales'})
                fig_imd.update_xaxes(tickmode='linear')
                st.plotly_chart(fig_imd, use_container_width=True)
            else:
                st.warning("No valid IMD data found for the provided postcodes.")
                
            # Download processed data
            st.write("### Export Processed Data")
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="Download Cleaned & Enriched Data",
                data=csv,
                file_name='cleaned_processed_sales_demographics.csv',
                mime='text/csv',
            )
