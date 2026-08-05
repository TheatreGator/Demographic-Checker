import streamlit as st
import pandas as pd
import requests
import plotly.express as px
import math
import re

# ---------------------------------
# DATA FETCHING & MATH FUNCTIONS
# ---------------------------------

@st.cache_data
def get_postcode_data(postcodes):
    url = "https://api.postcodes.io/postcodes"
    results = {}
    
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
                        "Longitude": item["result"].get("longitude"),
                        "Match_Type": "Full Postcode"
                    }
    return results

@st.cache_data
def get_outcode_data(outcodes):
    results = {}
    for outcode in outcodes:
        clean_outcode = str(outcode).replace(' ', '').upper()
        if not clean_outcode:
            continue
            
        url = f"https://api.postcodes.io/outcodes/{clean_outcode}"
        response = requests.get(url)
        
        if response.status_code == 200:
            data = response.json().get("result", {})
            if data:
                results[clean_outcode] = {
                    "Ward": "Unknown (District Level)",
                    "LSOA": "Unknown (District Level)",
                    "Country": "Unknown",
                    "IMD_Rank": "Unknown",
                    "IMD_Decile": "Unknown",
                    "Latitude": data.get("latitude"),
                    "Longitude": data.get("longitude"),
                    "Match_Type": "District Fallback"
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
    R = 3958.8 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def extract_outcode(postcode_str):
    pc = str(postcode_str).strip().upper()
    if ' ' in pc:
        return pc.split(' ')[0]
    match = re.match(r'^([A-Z]{1,2}\d[A-Z\d]?)', pc)
    if match:
        return match.group(1)
    return pc

# ---------------------------------
# APP UI & CONFIGURATION
# ---------------------------------

st.set_page_config(page_title="Sales Demographics Analyzer", layout="wide")
st.title("📊 UK Sales Demographics & Geo-Analyzer")
st.write("Upload your sales data to map postcodes, track multi-show loyalty, and analyze box office trends.")

uploaded_file = st.file_uploader("Upload Sales Data (CSV)", type="csv")

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file)
    
    st.sidebar.header("1. Standard Configuration")
    postcode_col = st.sidebar.selectbox("Select Primary Postcode Column", df.columns)
    
    numeric_columns = df.select_dtypes(include=['number']).columns.tolist()
    if not numeric_columns:
        st.error("Your dataset must contain at least one numeric column for sales/value.")
        st.stop()
    sales_col = st.sidebar.selectbox("Select Sales/Revenue Column", numeric_columns)

    st.sidebar.markdown("---")
    
    st.sidebar.header("2. Advanced Settings (Optional)")
    optional_cols = ["None"] + df.columns.tolist()
    optional_num_cols = ["None"] + numeric_columns
    
    show_name_col = st.sidebar.selectbox("Show/Event Name Column", optional_cols, help="Allows filtering and cross-show analysis.")
    customer_id_col = st.sidebar.selectbox("Customer/Owner ID Column", optional_cols, help="Unlocks the Retention & Loyalty tab.")
    fallback_col = st.sidebar.selectbox("Fallback District/Sector Column", optional_cols, help="Used only if the primary postcode fails.")
    venue_postcode = st.sidebar.text_input("Venue Postcode (For Catchment/Distance)", placeholder="e.g. SW1A 1AA")
    transaction_date_col = st.sidebar.selectbox("Transaction Date Column", optional_cols)
    event_date_col = st.sidebar.selectbox("Event Date Column", optional_cols)
    order_id_col = st.sidebar.selectbox("Order/Transaction ID Column", optional_cols)
    qty_col = st.sidebar.selectbox("Ticket Quantity Column", optional_num_cols)

    # ---------------------------------
    # DATA PROCESSING
    # ---------------------------------
    if 'processed_data' not in st.session_state:
        st.session_state.processed_data = None

    if st.button("Analyze Data"):
        with st.spinner("Cleaning data and fetching locations from Postcodes.io (Including Fallbacks)..."):
            original_row_count = len(df)
            
            clean_series = df[postcode_col].dropna().astype(str).str.replace(r'\s+', '', regex=True).str.upper()
            valid_postcodes_series = clean_series[~clean_series.isin(['NAN', 'NULL', 'NONE', ''])]
            unique_postcodes = [str(x) for x in valid_postcodes_series.unique()]
            
            geo_data = get_postcode_data(unique_postcodes)
            
            outcodes_to_fetch = set()
            for idx, row in df.iterrows():
                pc_val = row[postcode_col]
                clean_pc = str(pc_val).replace(' ', '').upper() if pd.notna(pc_val) else ""
                
                if clean_pc not in geo_data:
                    fallback_val = ""
                    if fallback_col != "None" and pd.notna(row[fallback_col]):
                        fallback_val = extract_outcode(row[fallback_col])
                    elif clean_pc:
                        fallback_val = extract_outcode(pc_val)
                    
                    if fallback_val:
                        outcodes_to_fetch.add(fallback_val)
            
            outcode_data = get_outcode_data(list(outcodes_to_fetch))
            
            mapped_rows = []
            for idx, row in df.iterrows():
                pc_val = row[postcode_col]
                clean_pc = str(pc_val).replace(' ', '').upper() if pd.notna(pc_val) else ""
                
                if clean_pc in geo_data:
                    mapped_rows.append(geo_data[clean_pc])
                else:
                    fallback_val = ""
                    if fallback_col != "None" and pd.notna(row[fallback_col]):
                        fallback_val = extract_outcode(row[fallback_col]).replace(' ', '').upper()
                    elif pc_val:
                        fallback_val = extract_outcode(pc_val).replace(' ', '').upper()
                        
                    if fallback_val in outcode_data:
                        mapped_rows.append(outcode_data[fallback_val])
                    else:
                        mapped_rows.append({
                            "Ward": "Unknown", "LSOA": "Unknown", "Country": "Unknown",
                            "IMD_Rank": "Unknown", "IMD_Decile": "Unknown", 
                            "Latitude": None, "Longitude": None, "Match_Type": "Unmatched"
                        })
                
            geo_df = pd.DataFrame(mapped_rows, index=df.index)
            processed_df = pd.concat([df, geo_df], axis=1)
            
            processed_df = processed_df[processed_df['Match_Type'] != "Unmatched"].copy()
            
            # Ensure dates are parsed properly once during processing
            if transaction_date_col != "None":
                processed_df[transaction_date_col] = pd.to_datetime(processed_df[transaction_date_col], errors='coerce')
            if event_date_col != "None":
                processed_df[event_date_col] = pd.to_datetime(processed_df[event_date_col], errors='coerce')
                
            st.session_state.processed_data = processed_df
            st.session_state.original_row_count = original_row_count
            st.success("Analysis complete!")

    # ---------------------------------
    # RESULTS DASHBOARD
    # ---------------------------------
    if st.session_state.processed_data is not None:
        main_df = st.session_state.processed_data
        
        # Mapping summary
        full_matches = len(main_df[main_df['Match_Type'] == "Full Postcode"])
        fallback_matches = len(main_df[main_df['Match_Type'] == "District Fallback"])
        rows_omitted = st.session_state.original_row_count - len(main_df)
        
        st.info(f"📍 **Mapping Summary:** Mapped **{full_matches}** exact postcodes. Saved **{fallback_matches}** rows using district-level fallbacks. ")
        if rows_omitted > 0:
            st.warning(f"⚠️ Omitted {rows_omitted} row(s) that contained unmappable data.")
        
        # Multi-show Global Filter
        st.markdown("---")
        filtered_df = main_df.copy()
        if show_name_col != "None":
            st.write("### 🎛️ Filter Analysis by Show")
            all_shows = main_df[show_name_col].dropna().unique().tolist()
            selected_shows = st.multiselect("Select Show(s) to Include:", all_shows, default=all_shows)
            filtered_df = main_df[main_df[show_name_col].isin(selected_shows)].copy()
            
            if filtered_df.empty:
                st.error("No data available for the selected shows. Please select at least one show.")
                st.stop()

        # ---------------------------------
        # TABS LAYOUT
        # ---------------------------------
        tab1, tab2, tab3 = st.tabs(["🌍 Geo & Demographics", "🎭 Advanced Analytics", "🔄 Retention & Loyalty"])
        
        # ==========================================
        # TAB 1: STANDARD GEO & DEMOGRAPHICS
        # ==========================================
        with tab1:
            st.write("### Interactive Sales Hotspots")
            df_map = filtered_df.dropna(subset=['Latitude', 'Longitude']).copy()
            
            if not df_map.empty:
                map_grouped = df_map.groupby([postcode_col, 'Latitude', 'Longitude', 'Match_Type', 'IMD_Decile'])[sales_col].sum().reset_index()
                fig_map = px.scatter_map(
                    map_grouped, lat="Latitude", lon="Longitude", size=sales_col,
                    hover_name=postcode_col, hover_data={"Match_Type": True, "IMD_Decile": True, "Latitude": False, "Longitude": False},
                    color_discrete_sequence=["#FF4B4B"], zoom=5, height=550
                )
                fig_map.update_layout(map_style="carto-positron", margin={"r":0,"t":0,"l":0,"b":0})
                st.plotly_chart(fig_map, width="stretch")
            
            col1, col2 = st.columns(2)
            with col1:
                st.write("### Sales by Ward")
                df_wards = filtered_df[~filtered_df['Ward'].str.contains("Unknown")].copy()
                if not df_wards.empty:
                    ward_sales = df_wards.groupby('Ward')[sales_col].sum().reset_index().sort_values(by=sales_col, ascending=False)
                    st.plotly_chart(px.bar(ward_sales.head(10), x='Ward', y=sales_col), width="stretch")
                
            with col2:
                st.write("### Sales by LSOA")
                df_lsoa = filtered_df[~filtered_df['LSOA'].str.contains("Unknown")].copy()
                if not df_lsoa.empty:
                    lsoa_sales = df_lsoa.groupby('LSOA')[sales_col].sum().reset_index().sort_values(by=sales_col, ascending=False)
                    st.plotly_chart(px.bar(lsoa_sales.head(10), x='LSOA', y=sales_col), width="stretch")
                
            st.write("### Deprivation (IMD) Analysis")
            df_imd = filtered_df[filtered_df['IMD_Decile'] != "Unknown"].copy()
            if not df_imd.empty:
                df_imd['IMD_Decile'] = df_imd['IMD_Decile'].astype(int)
                imd_sales = df_imd.groupby('IMD_Decile')[sales_col].sum().reset_index()
                all_deciles = pd.DataFrame({'IMD_Decile': range(1, 11)})
                imd_sales = pd.merge(all_deciles, imd_sales, on='IMD_Decile', how='left').fillna(0)
                
                fig_imd = px.bar(imd_sales, x='IMD_Decile', y=sales_col, labels={'IMD_Decile': 'Decile (1 = Most Deprived)'})
                fig_imd.update_xaxes(tickmode='linear')
                st.plotly_chart(fig_imd, width="stretch")

        # ==========================================
        # TAB 2: ADVANCED THEATRE ANALYTICS
        # ==========================================
        with tab2:
            st.write("## 🎭 Post-Show Analytics")
            
            # Catchment Area
            st.markdown("---")
            st.write("### 📍 Catchment Area (Distance Travelled)")
            if venue_postcode:
                v_lat, v_lon = get_venue_coordinates(venue_postcode)
                if v_lat and v_lon:
                    filtered_df['Distance_Miles'] = filtered_df.apply(lambda row: calculate_distance(row['Latitude'], row['Longitude'], v_lat, v_lon), axis=1)
                    df_dist = filtered_df.dropna(subset=['Distance_Miles'])
                    if not df_dist.empty:
                        fig_dist = px.histogram(df_dist, x='Distance_Miles', nbins=30, 
                                                title=f"Distance Travelled to Venue ({venue_postcode})",
                                                labels={'Distance_Miles': 'Distance (Miles)', 'count': 'Number of Bookings'},
                                                color_discrete_sequence=["#3366CC"])
                        st.plotly_chart(fig_dist, width="stretch")
                else:
                    st.warning("Could not find coordinates for the provided Venue Postcode.")
            else:
                st.info("Enter a Venue Postcode in the sidebar to unlock Catchment Area analysis.")

            # Booking Curve
            st.markdown("---")
            st.write("### 📈 Booking Curve (Lead Time)")
            if transaction_date_col != "None" and event_date_col != "None":
                df_dates = filtered_df.dropna(subset=[transaction_date_col, event_date_col]).copy()
                if not df_dates.empty:
                    df_dates['Days_Out'] = (df_dates[event_date_col] - df_dates[transaction_date_col]).dt.days
                    df_dates = df_dates[(df_dates['Days_Out'] >= 0) & (df_dates['Days_Out'] <= 365)]
                    
                    metric_col = qty_col if qty_col != "None" else sales_col
                    curve_data = df_dates.groupby('Days_Out')[metric_col].sum().reset_index()
                    curve_data = curve_data.sort_values('Days_Out', ascending=False)
                    curve_data['Cumulative_Volume'] = curve_data[metric_col].cumsum()
                    
                    fig_curve = px.line(curve_data, x='Days_Out', y='Cumulative_Volume', 
                                        title="Cumulative Sales Curve Prior to Event",
                                        labels={'Days_Out': 'Days Before Event', 'Cumulative_Volume': 'Total Volume'})
                    fig_curve.update_xaxes(autorange="reversed")
                    st.plotly_chart(fig_curve, width="stretch")
            else:
                st.info("Map the Transaction Date and Event Date columns in the sidebar to unlock Booking Curve analysis.")

            # Transaction Day-of-Week Trends
            col_a, col_b = st.columns(2)
            with col_a:
                st.write("### 📅 Day-of-Week Booking Trends")
                if transaction_date_col != "None":
                    df_trans = filtered_df.dropna(subset=[transaction_date_col]).copy()
                    if not df_trans.empty:
                        df_trans['Booking_Day'] = df_trans[transaction_date_col].dt.day_name()
                        dow_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                        dow_sales = df_trans.groupby('Booking_Day')[sales_col].sum().reindex(dow_order).reset_index()
                        
                        fig_dow = px.bar(dow_sales, x='Booking_Day', y=sales_col, 
                                         title="Total Sales by Transaction Day",
                                         labels={'Booking_Day': 'Day of Week', sales_col: 'Revenue'})
                        st.plotly_chart(fig_dow, width="stretch")
                else:
                    st.info("Map the Transaction Date to unlock.")

            # Performance Day Yield
            with col_b:
                st.write("### 💷 Performance Day Yield")
                if event_date_col != "None" and qty_col != "None":
                    df_perf = filtered_df.dropna(subset=[event_date_col]).copy()
                    if not df_perf.empty:
                        df_perf['Perf_Day'] = df_perf[event_date_col].dt.day_name()
                        dow_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                        yield_perf = df_perf.groupby('Perf_Day').agg({sales_col: 'sum', qty_col: 'sum'}).reindex(dow_order).reset_index()
                        
                        # Avoid division by zero
                        yield_perf = yield_perf[yield_perf[qty_col] > 0]
                        yield_perf['Avg_Price'] = yield_perf[sales_col] / yield_perf[qty_col]
                        
                        fig_perf = px.bar(yield_perf, x='Perf_Day', y='Avg_Price', 
                                          title="Avg Ticket Price by Event Day",
                                          labels={'Perf_Day': 'Performance Day', 'Avg_Price': 'Avg Price Paid (£)'})
                        st.plotly_chart(fig_perf, width="stretch")
                else:
                    st.info("Map Event Date and Ticket Quantity to unlock.")

            # Party Size
            st.markdown("---")
            st.write("### 🎟️ Party Size Insights")
            if order_id_col != "None" and qty_col != "None":
                party_data = filtered_df.groupby(order_id_col)[qty_col].sum().reset_index()
                def categorize_party(size):
                    if size == 1: return "Solo (1)"
                    elif size == 2: return "Couples (2)"
                    elif 3 <= size <= 4: return "Families/Small Groups (3-4)"
                    else: return "Large Groups (5+)"
                    
                party_data['Party_Category'] =
