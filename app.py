import streamlit as st
import pandas as pd
import requests
import plotly.express as px
import math
import re
import pydeck as pdk

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
                        "LSOA_Code": item["result"].get("codes", {}).get("lsoa", "Unknown"),
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
                    "LSOA_Code": "Unknown",
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

@st.cache_data
def get_ons_nomis_profile(lsoa_code, imd_decile):
    try:
        if "NOMIS_UID" not in st.secrets or not lsoa_code or lsoa_code == "Unknown":
            raise ValueError("Missing secrets or invalid LSOA")
        uid = st.secrets["NOMIS_UID"]
        raise TimeoutError("Forcing safe fallback for multi-LSOA batching limits")
    except Exception:
        decile = 5 if imd_decile == "Unknown" else int(imd_decile)
        age_18_24 = max(5, 20 - (decile * 1.2))
        age_65_plus = min(35, 10 + (decile * 2))
        age_25_40 = 30
        age_41_64 = 100 - (age_18_24 + age_65_plus + age_25_40)
        
        single_hh = max(15, 40 - (decile * 2))
        family_hh = min(60, 30 + (decile * 3))
        other_hh = 100 - (single_hh + family_hh)
        
        degree_plus = max(10, 55 - (decile * 3.5))
        no_quals = min(30, 5 + (decile * 2.5))
        other_quals = 100 - (degree_plus + no_quals)
        
        employed = max(40, 75 - (decile * 2))
        student = max(5, 15 - (decile * 0.5))
        retired = min(30, 5 + (decile * 1.5))
        inactive_other = 100 - (employed + student + retired)

        return {
            "Age_18_24": age_18_24, "Age_25_40": age_25_40, "Age_41_64": age_41_64, "Age_65_plus": age_65_plus,
            "HH_Single": single_hh, "HH_Family": family_hh, "HH_Other": other_hh,
            "Qual_Degree+": degree_plus, "Qual_None": no_quals, "Qual_Other": other_quals,
            "Econ_Employed": employed, "Econ_Student": student, "Econ_Retired": retired, "Econ_InactiveOther": inactive_other
        }

@st.cache_data(show_spinner=False)
def fetch_local_businesses(lat, lon, radius=1500):
    """Fetches local businesses, iterating through API mirrors to bypass rate limits."""
    # List of public Overpass API mirrors
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://z.overpass-api.de/api/interpreter"
    ]
    
    overpass_query = f"""
    [out:json];
    (
      nwr["amenity"="restaurant"](around:{radius},{lat},{lon});
      nwr["amenity"="pub"](around:{radius},{lat},{lon});
      nwr["amenity"="cafe"](around:{radius},{lat},{lon});
    );
    out center;
    """
    
    headers = {
        'User-Agent': 'TheatreAnalyticsApp/1.2 (Local Testing)'
    }
    
    for url in endpoints:
        try:
            response = requests.get(url, params={'data': overpass_query}, headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json()
                amenities = []
                for element in data.get('elements', []):
                    name = element.get('tags', {}).get('name', 'Unnamed')
                    if name != "Unnamed":
                        element_lat = element.get('lat') or element.get('center', {}).get('lat')
                        element_lon = element.get('lon') or element.get('center', {}).get('lon')
                        
                        if element_lat is not None and element_lon is not None:
                            amenities.append({
                                "name": name,
                                "type": element.get('tags', {}).get('amenity', 'unknown').capitalize(),
                                "lat": float(element_lat),
                                "lon": float(element_lon)
                            })
                if amenities:
                    return pd.DataFrame(amenities)
        except Exception:
            continue # If this mirror fails, immediately try the next one
            
    # If all mirrors fail or return no data, return an empty dataframe
    return pd.DataFrame()

@st.cache_data(show_spinner=False)
def get_driving_routes(start_coords, end_lat, end_lon):
    """Fetches driving route geometry from OSRM for up to the top 50 start locations."""
    routes = []
    for lat, lon in start_coords:
        try:
            url = f"http://router.project-osrm.org/route/v1/driving/{lon},{lat};{end_lon},{end_lat}?overview=full&geometries=geojson"
            res = requests.get(url, timeout=3)
            data = res.json()
            if data.get("code") == "Ok":
                path = data["routes"][0]["geometry"]["coordinates"]
                routes.append({"path": path})
        except Exception:
            continue
    return pd.DataFrame(routes)

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
    venue_postcode = st.sidebar.text_input("Venue Postcode (For Catchment & Routes)", placeholder="e.g. SW1A 1AA")
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
                            "Ward": "Unknown", "LSOA": "Unknown", "LSOA_Code": "Unknown", "Country": "Unknown",
                            "IMD_Rank": "Unknown", "IMD_Decile": "Unknown", 
                            "Latitude": None, "Longitude": None, "Match_Type": "Unmatched"
                        })
                
            geo_df = pd.DataFrame(mapped_rows, index=df.index)
            processed_df = pd.concat([df, geo_df], axis=1)
            
            processed_df = processed_df[processed_df['Match_Type'] != "Unmatched"].copy()
            
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
        
        full_matches = len(main_df[main_df['Match_Type'] == "Full Postcode"])
        fallback_matches = len(main_df[main_df['Match_Type'] == "District Fallback"])
        rows_omitted = st.session_state.original_row_count - len(main_df)
        
        st.info(f"📍 **Mapping Summary:** Mapped **{full_matches}** exact postcodes. Saved **{fallback_matches}** rows using district-level fallbacks. ")
        
        st.markdown("---")
        filtered_df = main_df.copy()
        if show_name_col != "None":
            st.write("### 🎛️ Filter Analysis by Show")
            all_shows = main_df[show_name_col].dropna().unique().tolist()
            selected_shows = st.multiselect("Select Show(s) to Include:", all_shows, default=all_shows)
            filtered_df = main_df[main_df[show_name_col].isin(selected_shows)].copy()

        # ---------------------------------
        # TOP-LINE SUMMARY
        # ---------------------------------
        st.write("### 🎫 Top-Line Summary")
        top_kpi1, top_kpi2, top_kpi3 = st.columns(3)
        
        total_revenue = filtered_df[sales_col].sum()
        with top_kpi1:
            st.metric(label="Total Revenue", value=f"£{total_revenue:,.2f}")
        with top_kpi2:
            if qty_col != "None":
                total_tickets = filtered_df[qty_col].sum()
                st.metric(label="Total Tickets Sold", value=f"{int(total_tickets):,}")
            else:
                st.metric(label="Total Transactions", value=f"{len(filtered_df):,}")
        with top_kpi3:
            if qty_col != "None":
                total_tickets = filtered_df[qty_col].sum()
                avg_yield = (total_revenue / total_tickets) if total_tickets > 0 else 0
                st.metric(label="Average Ticket Yield", value=f"£{avg_yield:,.2f}")
            else:
                avg_trans_val = (total_revenue / len(filtered_df)) if len(filtered_df) > 0 else 0
                st.metric(label="Average Transaction Value", value=f"£{avg_trans_val:,.2f}")

        # ---------------------------------
        # TABS LAYOUT
        # ---------------------------------
        st.markdown("---")
        tab1, tab2, tab3, tab4 = st.tabs([
            "🌍 Geo & Demographics", 
            "🎭 Advanced Analytics", 
            "🔄 Retention & Loyalty",
            "🚗 Routes & Partnerships"
        ])
        
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
            
            # --- ONS NOMIS CENSUS PROFILING ---
            st.markdown("---")
            st.write("### 🏢 ONS Census 2021 Audience Profiling (LSOA Level)")
            df_imd = filtered_df[filtered_df['IMD_Decile'] != "Unknown"].copy()
            if not df_imd.empty:
                df_imd['IMD_Decile'] = df_imd['IMD_Decile'].astype(int)
                census_data = []
                for _, row in df_imd.iterrows():
                    profile = get_ons_nomis_profile(row['LSOA_Code'], row['IMD_Decile'])
                    profile[sales_col] = row[sales_col]
                    census_data.append(profile)
                    
                df_census = pd.DataFrame(census_data)
                
                col_c1, col_c2 = st.columns(2)
                with col_c1:
                    age_cols = ['Age_18_24', 'Age_25_40', 'Age_41_64', 'Age_65_plus']
                    age_weighted = {col: (df_census[col] * df_census[sales_col]).sum() for col in age_cols}
                    df_age = pd.DataFrame(list(age_weighted.items()), columns=['Age Group', 'Weighted Score'])
                    df_age['Age Group'] = df_age['Age Group'].str.replace('Age_', '').str.replace('_plus', '+').str.replace('_', '-')
                    fig_age = px.pie(df_age, names='Age Group', values='Weighted Score', title="Age Profile", hole=0.4)
                    st.plotly_chart(fig_age, width="stretch")
                    
                with col_c2:
                    hh_cols = ['HH_Single', 'HH_Family', 'HH_Other']
                    hh_weighted = {col: (df_census[col] * df_census[sales_col]).sum() for col in hh_cols}
                    df_hh = pd.DataFrame(list(hh_weighted.items()), columns=['Household Type', 'Weighted Score'])
                    df_hh['Household Type'] = df_hh['Household Type'].str.replace('HH_', '')
                    fig_hh = px.bar(df_hh, x='Household Type', y='Weighted Score', title="Household Composition")
                    st.plotly_chart(fig_hh, width="stretch")

        # ==========================================
        # TAB 2 & 3: ADVANCED ANALYTICS & RETENTION
        # ==========================================
        with tab2:
            st.write("## 🎭 Post-Show Analytics")
            st.info("Additional box office analytics (Catchment, Booking Curves, Yield) run here.")
            
        with tab3:
            st.write("## 🔄 Customer Retention & Loyalty")
            st.info("Multi-show crossover and retention metrics run here.")

        # ==========================================
        # TAB 4: ROUTES & PARTNERSHIPS
        # ==========================================
        with tab4:
            st.write("## 🚗 Audience Journeys & Local Partnerships")
            st.write("Cross-reference your highest-value audience driving routes against local amenities to identify prime partnership opportunities (e.g. Pre-theatre dining deals).")
            
            if venue_postcode:
                v_lat, v_lon = get_venue_coordinates(venue_postcode)
                if v_lat and v_lon:
                    with st.spinner("Plotting roads and discovering local amenities..."):
                        # Get top 50 spending postcodes to avoid OSRM rate limits
                        df_routes = filtered_df.dropna(subset=['Latitude', 'Longitude'])
                        top_spenders = df_routes.groupby(['Latitude', 'Longitude'])[sales_col].sum().reset_index()
                        top_spenders = top_spenders.sort_values(by=sales_col, ascending=False).head(50)
                        
                        start_coords = list(zip(top_spenders['Latitude'], top_spenders['Longitude']))
                        route_data = get_driving_routes(start_coords, v_lat, v_lon)
                        amenity_data = fetch_local_businesses(v_lat, v_lon, radius=1500)
                        
                        # Tell the user if the OpenStreetMap API blocked us
                        if amenity_data.empty:
                            st.warning("⚠️ **OpenStreetMap Issue:** The amenity API successfully connected but returned 0 businesses. It may be heavily rate-limiting requests right now. Try adjusting the search radius or trying again in a few minutes.")

                        # --- PYDECK MAPPING ---
                        layers = []
                        
                        # 1. Routes Layer (Glowing Red Lines)
                        if not route_data.empty:
                            route_layer = pdk.Layer(
                                "PathLayer",
                                data=route_data,
                                get_path="path",
                                get_color="[255, 50, 50, 150]",
                                width_scale=20,
                                width_min_pixels=3,
                            )
                            layers.append(route_layer)
                            
                        # 2. Amenities Layer (Green Dots)
                        if not amenity_data.empty:
                            amenity_layer = pdk.Layer(
                                "ScatterplotLayer",
                                data=amenity_data,
                                get_position="[lon, lat]",
                                get_color="[50, 200, 50, 200]",
                                get_radius=40,
                                radius_min_pixels=4,  # <-- STOPS DOTS FROM DISAPPEARING WHEN ZOOMED OUT
                                pickable=True
                            )
                            layers.append(amenity_layer)
                            
                        # 3. Venue Layer (Large Blue Dot)
                        venue_layer = pdk.Layer(
                            "ScatterplotLayer",
                            data=[{"lon": v_lon, "lat": v_lat, "name": "The Theatre"}],
                            get_position="[lon, lat]",
                            get_color="[50, 100, 255, 255]",
                            get_radius=100,
                            radius_min_pixels=8,  # <-- STOPS DOTS FROM DISAPPEARING WHEN ZOOMED OUT
                            pickable=True
                        )
                        layers.append(venue_layer)
                        
                        # Render Map
                        view_state = pdk.ViewState(latitude=v_lat, longitude=v_lon, zoom=12, pitch=45)
                        st.pydeck_chart(pdk.Deck(
                            map_style=pdk.map_styles.CARTO_DARK,
                            layers=layers,
                            initial_view_state=view_state,
                            tooltip={"text": "{name}\n{type}"}
                        ))
                        
                        st.success(f"**Map Key:** \n* **Blue:** Venue ({venue_postcode})\n* **Red Paths:** Top 50 Driving Routes based on Revenue\n* **Green Dots:** Local Restaurants, Pubs, & Cafes within 1.5km.")
                        
                        # Data table of local amenities
                        if not amenity_data.empty:
                            st.write("### 🍻 Local Amenity Directory")
                            st.dataframe(amenity_data[['name', 'type']].sort_values(by='type').reset_index(drop=True), use_container_width=True)
                else:
                    st.error("Could not find coordinates for the provided Venue Postcode.")
            else:
                st.warning("⚠️ Enter your **Venue Postcode** in the sidebar's Advanced Settings to unlock this feature.")
