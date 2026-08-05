import streamlit as st
import pandas as pd
import requests
import plotly.express as px
import math

# ---------------------------------
# DATA FETCHING & MATH FUNCTIONS
# ---------------------------------

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
                    
                    country = item["result"].get("country", "")
                    rank = item["result"].get("index_of_multiple_deprivation")
                    
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

@st.cache_data
def get_venue_coordinates(postcode):
    clean_pc = str(postcode).replace(' ', '').upper()
    response = requests.get(f"https://api.postcodes.io/postcodes/{clean_pc}")
    if response.status_code == 200:
        data = response.json().get("result", {})
        return data.get("latitude"), data.get("longitude")
    return None, None

def calculate_distance(lat1, lon1, lat2, lon2):
    if pd.isna(lat1) or pd.isna(lon1) or pd.isna(lat2) or pd.isna(lon2):
        return None
    # Haversine formula to calculate straight-line distance in miles
    R = 3958.8 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# ---------------------------------
# APP UI & CONFIGURATION
# ---------------------------------

st.set_page_config(page_title="Sales Demographics Analyzer", layout="wide")
st.title("📊 UK Sales Demographics & Geo-Analyzer")
st.write("Upload your sales data to map postcodes to LSOAs, Wards, IMD Deprivation Deciles, and Advanced Theatre Analytics.")

uploaded_file = st.file_uploader("Upload Sales Data (CSV)", type="csv")

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file)
    
    st.write("### Preview of Uploaded Data")
    st.dataframe(df.head())
    
    # ---------------------------------
    # SIDEBAR MAPPING
    # ---------------------------------
    st.sidebar.header("1. Standard Configuration")
    postcode_col = st.sidebar.selectbox("Select Postcode Column", df.columns)
    
    numeric_columns = df.select_dtypes(include=['number']).columns.tolist()
    if not numeric_columns:
        st.error("Your dataset must contain at least one numeric column for sales/value.")
        st.stop()
    sales_col = st.sidebar.selectbox("Select Sales/Revenue Column", numeric_columns)

    st.sidebar.markdown("---")
    
    st.sidebar.header("2. Advanced Settings (Optional)")
    st.sidebar.write("Map these columns to unlock the Advanced Theatre Analytics tab.")
    
    optional_cols = ["None"] + df.columns.tolist()
    optional_num_cols = ["None"] + numeric_columns
    
    venue_postcode = st.sidebar.text_input("Venue Postcode (For Catchment/Distance)", placeholder="e.g. SW1A 1AA")
    transaction_date_col = st.sidebar.selectbox("Transaction Date Column", optional_cols)
    event_date_col = st.sidebar.selectbox("Event Date Column", optional_cols)
    order_id_col = st.sidebar.selectbox("Order/Transaction ID Column", optional_cols)
    qty_col = st.sidebar.selectbox("Ticket Quantity Column", optional_num_cols)

    # ---------------------------------
    # DATA PROCESSING
    # ---------------------------------
    if st.button("Analyze Data"):
        with st.spinner("Cleaning data and fetching locations from Postcodes.io..."):
            original_row_count = len(df)
            
            # Postcode pre-cleaning
            clean_series = df[postcode_col].dropna().astype(str)
            clean_series = clean_series.str.replace(r'\s+', '', regex=True).str.upper()
            valid_postcodes_series = clean_series[~clean_series.isin(['NAN', 'NULL', 'NONE', ''])]
            unique_postcodes = [str(x) for x in valid_postcodes_series.unique()]
            
            geo_data = get_postcode_data(unique_postcodes)
            
            # Map data
            mapped_rows = []
            for pc in df[postcode_col]:
                clean_pc = str(pc).replace(' ', '').upper() if pd.notna(pc) else ""
                mapped_rows.append(geo_data.get(clean_pc, {
                    "Ward": "Unknown", "LSOA": "Unknown", "Country": "Unknown",
                    "IMD_Rank": "Unknown", "IMD_Decile": "Unknown", 
                    "Latitude": None, "Longitude": None
                }))
                
            geo_df = pd.DataFrame(mapped_rows, index=df.index)
            df = pd.concat([df, geo_df], axis=1)
            df = df[df['Ward'] != "Unknown"].copy()
            
            st.success("Analysis complete!")
            rows_omitted = original_row_count - len(df)
            if rows_omitted > 0:
                st.warning(f"⚠️ Omitted {rows_omitted} row(s) that contained invalid or unmappable postcodes.")
            
            # ---------------------------------
            # TABS LAYOUT
            # ---------------------------------
            tab1, tab2 = st.tabs(["🌍 Geo & Demographics", "🎭 Advanced Theatre Analytics"])
            
            # ==========================================
            # TAB 1: STANDARD GEO & DEMOGRAPHICS
            # ==========================================
            with tab1:
                st.write("### Interactive Sales Hotspots")
                df_map = df.dropna(subset=['Latitude', 'Longitude']).copy()
                
                if not df_map.empty:
                    map_grouped = df_map.groupby([postcode_col, 'Latitude', 'Longitude', 'Ward', 'IMD_Decile'])[sales_col].sum().reset_index()
                    fig_map = px.scatter_map(
                        map_grouped, lat="Latitude", lon="Longitude", size=sales_col,
                        hover_name=postcode_col, hover_data={"Ward": True, "IMD_Decile": True, "Latitude": False, "Longitude": False},
                        color_discrete_sequence=["#FF4B4B"], zoom=5, height=550
                    )
                    fig_map.update_layout(map_style="carto-positron", margin={"r":0,"t":0,"l":0,"b":0})
                    st.plotly_chart(fig_map, use_container_width=True)
                
                col1, col2 = st.columns(2)
                with col1:
                    st.write("### Sales by Ward")
                    ward_sales = df.groupby('Ward')[sales_col].sum().reset_index().sort_values(by=sales_col, ascending=False)
                    st.plotly_chart(px.bar(ward_sales.head(10), x='Ward', y=sales_col), use_container_width=True)
                    
                with col2:
                    st.write("### Sales by LSOA")
                    lsoa_sales = df.groupby('LSOA')[sales_col].sum().reset_index().sort_values(by=sales_col, ascending=False)
                    st.plotly_chart(px.bar(lsoa_sales.head(10), x='LSOA', y=sales_col), use_container_width=True)
                    
                st.write("### Deprivation (IMD) Analysis")
                df_imd = df[df['IMD_Decile'] != "Unknown"].copy()
                if not df_imd.empty:
                    df_imd['IMD_Decile'] = df_imd['IMD_Decile'].astype(int)
                    imd_sales = df_imd.groupby('IMD_Decile')[sales_col].sum().reset_index()
                    all_deciles = pd.DataFrame({'IMD_Decile': range(1, 11)})
                    imd_sales = pd.merge(all_deciles, imd_sales, on='IMD_Decile', how='left').fillna(0)
                    
                    fig_imd = px.bar(imd_sales, x='IMD_Decile', y=sales_col, labels={'IMD_Decile': 'Decile (1 = Most Deprived)'})
                    fig_imd.update_xaxes(tickmode='linear')
                    st.plotly_chart(fig_imd, use_container_width=True)

            # ==========================================
            # TAB 2: ADVANCED THEATRE ANALYTICS
            # ==========================================
            with tab2:
                st.write("## 🎭 Post-Show Analytics")
                
                # 1. Distance Travelled (Catchment)
                st.markdown("---")
                st.write("### 📍 Catchment Area (Distance Travelled)")
                if venue_postcode:
                    v_lat, v_lon = get_venue_coordinates(venue_postcode)
                    if v_lat and v_lon:
                        df['Distance_Miles'] = df.apply(lambda row: calculate_distance(row['Latitude'], row['Longitude'], v_lat, v_lon), axis=1)
                        df_dist = df.dropna(subset=['Distance_Miles'])
                        
                        fig_dist = px.histogram(df_dist, x='Distance_Miles', nbins=30, 
                                                title=f"Distance Travelled to Venue ({venue_postcode})",
                                                labels={'Distance_Miles': 'Distance (Miles)', 'count': 'Number of Bookings'},
                                                color_discrete_sequence=["#3366CC"])
                        st.plotly_chart(fig_dist, use_container_width=True)
                    else:
                        st.warning("Could not find coordinates for the provided Venue Postcode.")
                else:
                    st.info("Enter a Venue Postcode in the sidebar to unlock Catchment Area analysis.")

                # 2. Booking Curve (Lead Time)
                st.markdown("---")
                st.write("### 📈 Booking Curve (Lead Time)")
                if transaction_date_col != "None" and event_date_col != "None":
                    df_dates = df.copy()
                    df_dates[transaction_date_col] = pd.to_datetime(df_dates[transaction_date_col], errors='coerce')
                    df_dates[event_date_col] = pd.to_datetime(df_dates[event_date_col], errors='coerce')
                    df_dates = df_dates.dropna(subset=[transaction_date_col, event_date_col])
                    
                    if not df_dates.empty:
                        df_dates['Days_Out'] = (df_dates[event_date_col] - df_dates[transaction_date_col]).dt.days
                        # Filter out negative days out (post-event processing) and extreme outliers (> 365 days)
                        df_dates = df_dates[(df_dates['Days_Out'] >= 0) & (df_dates['Days_Out'] <= 365)]
                        
                        metric_col = qty_col if qty_col != "None" else sales_col
                        curve_data = df_dates.groupby('Days_Out')[metric_col].sum().reset_index()
                        curve_data = curve_data.sort_values('Days_Out', ascending=False)
                        curve_data['Cumulative_Volume'] = curve_data[metric_col].cumsum()
                        
                        fig_curve = px.line(curve_data, x='Days_Out', y='Cumulative_Volume', 
                                            title="Cumulative Sales Curve Prior to Event",
                                            labels={'Days_Out': 'Days Before Event', 'Cumulative_Volume': 'Total Volume'})
                        fig_curve.update_xaxes(autorange="reversed") # Reverse X axis so it approaches 0
                        st.plotly_chart(fig_curve, use_container_width=True)
                    else:
                        st.warning("Could not parse dates properly. Please ensure columns contain valid date formats.")
                else:
                    st.info("Map the Transaction Date and Event Date columns in the sidebar to unlock Booking Curve analysis.")

                # 3. Party Size Insights
                st.markdown("---")
                st.write("### 🎟️ Party Size Insights")
                if order_id_col != "None" and qty_col != "None":
                    party_data = df.groupby(order_id_col)[qty_col].sum().reset_index()
                    
                    def categorize_party(size):
                        if size == 1: return "Solo (1)"
                        elif size == 2: return "Couples (2)"
                        elif 3 <= size <= 4: return "Families/Small Groups (3-4)"
                        else: return "Large Groups (5+)"
                        
                    party_data['Party_Category'] = party_data[qty_col].apply(categorize_party)
                    category_counts = party_data['Party_Category'].value_counts().reset_index()
                    category_counts.columns = ['Party_Category', 'Count']
                    
                    fig_party = px.pie(category_counts, names='Party_Category', values='Count', 
                                       title="Audience Breakdown by Party Size", hole=0.4)
                    st.plotly_chart(fig_party, use_container_width=True)
                else:
                    st.info("Map the Order ID and Ticket Quantity columns in the sidebar to unlock Party Size Insights.")

                # 4. Yield & Average Price
                st.markdown("---")
                st.write("### 💷 Yield & Average Ticket Price by Demographic")
                if qty_col != "None":
                    df_yield = df[df['IMD_Decile'] != "Unknown"].copy()
                    df_yield['IMD_Decile'] = df_yield['IMD_Decile'].astype(int)
                    
                    yield_grouped = df_yield.groupby('IMD_Decile').agg({sales_col: 'sum', qty_col: 'sum'}).reset_index()
                    yield_grouped = yield_grouped[yield_grouped[qty_col] > 0]
                    yield_grouped['Average_Price'] = yield_grouped[sales_col] / yield_grouped[qty_col]
                    
                    all_deciles_y = pd.DataFrame({'IMD_Decile': range(1, 11)})
                    yield_grouped = pd.merge(all_deciles_y, yield_grouped, on='IMD_Decile', how='left').fillna(0)
                    
                    fig_yield = px.bar(yield_grouped, x='IMD_Decile', y='Average_Price',
                                       title="Average Ticket Price Paid per Deprivation Decile",
                                       labels={'IMD_Decile': 'Decile (1 = Most Deprived)', 'Average_Price': 'Avg Price Paid (£)'})
                    fig_yield.update_xaxes(tickmode='linear')
                    st.plotly_chart(fig_yield, use_container_width=True)
                else:
                    st.info("Map the Ticket Quantity column in the sidebar to unlock Yield Analysis.")

            # ---------------------------------
            # EXPORT
            # ---------------------------------
            st.markdown("---")
            st.write("### Export Processed Data")
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="Download Cleaned & Enriched Data", data=csv,
                file_name='cleaned_processed_sales_demographics.csv', mime='text/csv',
            )
