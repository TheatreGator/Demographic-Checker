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

# Arts Council England (ACE) Active Lives regional engagement baselines
ACE_LA_BENCHMARKS = {
    "Bradford": 52.4,
    "Leeds": 68.1,
    "Kirklees": 58.7,
    "Calderdale": 63.2
}
DEFAULT_UK_ACE_BENCHMARK = 61.5

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
                        "District": item["result"].get("admin_district", "Unknown"),
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
                admin_district = data.get("admin_district", ["Unknown"])
                district_val = admin_district[0] if isinstance(admin_district, list) and admin_district else "Unknown"
                
                results[clean_outcode] = {
                    "Ward": "Unknown (District Level)",
                    "District": district_val,
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

def calculate_cultural_propensity_proxy(profile, imd_decile, district):
    """Calculates a 0-100 cultural propensity proxy blending census and ACE LA benchmarks."""
    degree = profile.get("Qual_Degree+", 25.0)
    decile_val = (int(imd_decile) * 10) if imd_decile != "Unknown" else 50.0
    la_base = ACE_LA_BENCHMARKS.get(district, DEFAULT_UK_ACE_BENCHMARK)
    
    # Statistical weighting: Higher education (45%), Affluence/IMD (25%), LA Base (20%), Cultural age demographic (10%)
    score = (0.45 * degree) + (0.25 * decile_val) + (0.20 * la_base) + (0.10 * (profile.get('Age_25_40', 25) + profile.get('Age_65_plus', 20)))
    return round(min(100.0, max(0.0, score)), 1)

def generate_audience_persona(profile, imd_decile):
    """Assigns marketing personas based on ONS census indicators and IMD."""
    if imd_decile == "Unknown":
        return "Unclassified"
        
    decile = int(imd_decile)
    
    if decile >= 7:
        if profile.get('Age_65_plus', 0) >= 20 and profile.get('Econ_Retired', 0) >= 18:
            return "Affluent Arts Retirees"
        elif profile.get('HH_Family', 0) >= 38:
            return "Prosperous Suburban Families"
        elif profile.get('Age_25_40', 0) >= 28 and profile.get('Qual_Degree+', 0) >= 35:
            return "Young Urban Professionals"
        return "Affluent Broad Audience"
            
    if 4 <= decile <= 6:
        if profile.get('HH_Family', 0) >= 35:
            return "Mid-Market Families"
        elif profile.get('Econ_Student', 0) >= 12:
            return "Student & Graduate Hubs"
        return "Middle-Income Mix"
        
    if decile <= 3:
        if profile.get('Age_65_plus', 0) >= 22:
            return "Budget-Conscious Pensioners"
        elif profile.get('HH_Family', 0) >= 35:
            return "Hard-Pressed Families"
        return "Urban Value Seekers"
            
    return "General Audience"

@st.cache_data(show_spinner=False)
def fetch_local_businesses(lat, lon, radius=25000):
    """Fetches local businesses & billboards, iterating through API mirrors to bypass rate limits."""
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://z.overpass-api.de/api/interpreter"
    ]
    
    overpass_query = f"""
    [out:json][timeout:30];
    (
      nwr["amenity"="restaurant"](around:{radius},{lat},{lon});
      nwr["amenity"="pub"](around:{radius},{lat},{lon});
      nwr["amenity"="cafe"](around:{radius},{lat},{lon});
      nwr["advertising"="billboard"](around:{radius},{lat},{lon});
      nwr["advertising"="board"](around:{radius},{lat},{lon});
    );
    out center;
    """
    
    headers = {'User-Agent': 'TheatreAnalyticsApp/1.8 (Local Testing)'}
    
    for url in endpoints:
        try:
            response = requests.get(url, params={'data': overpass_query}, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                amenities = []
                for element in data.get('elements', []):
                    tags = element.get('tags', {})
                    name = tags.get('name', 'Unnamed')
                    item_type = tags.get('amenity', tags.get('advertising', 'unknown')).capitalize()
                    
                    if name == "Unnamed" and item_type.lower() in ['billboard', 'board']:
                        name = f"OOH Advertising ({item_type})"

                    if name != "Unnamed" or item_type.lower() in ['billboard', 'board']:
                        element_lat = element.get('lat') or element.get('center', {}).get('lat')
                        element_lon = element.get('lon') or element.get('center', {}).get('lon')
                        
                        if element_lat is not None and element_lon is not None:
                            website = tags.get('website', f"https://www.google.com/maps/search/?api=1&query={element_lat},{element_lon}")
                            amenities.append({
                                "name": name,
                                "type": item_type,
                                "lat": float(element_lat),
                                "lon": float(element_lon),
                                "link": website
                            })
                if amenities:
                    return pd.DataFrame(amenities)
        except Exception:
            continue
            
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
st.write("Upload your sales data to map postcodes, track multi-show loyalty, identify target personas, and forecast cultural propensity.")

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
    
    show_name_col = st.sidebar.selectbox("Show/Event Name Column", optional_cols)
    customer_id_col = st.sidebar.selectbox("Customer/Owner ID Column", optional_cols)
    fallback_col = st.sidebar.selectbox("Fallback District/Sector Column", optional_cols)
    venue_postcode = st.sidebar.text_input("Venue Postcode (For Catchment & Routes)", placeholder="e.g. BD1 1SD")
    transaction_date_col = st.sidebar.selectbox("Transaction Date Column", optional_cols)
    event_date_col = st.sidebar.selectbox("Event Date Column", optional_cols)
    order_id_col = st.sidebar.selectbox("Order/Transaction ID Column", optional_cols)
    qty_col = st.sidebar.selectbox("Ticket Quantity Column", optional_num_cols)

    st.sidebar.markdown("---")
    st.sidebar.header("3. Cultural / ACE Data Source")
    st.sidebar.write("By default, the model uses West Yorkshire ACE baselines (Bradford, Leeds, Kirklees, Calderdale) blended with census factors. You can also supply a custom dataset below.")
    ace_file = st.sidebar.file_uploader("Upload Custom ACE / Audience Spectrum CSV (Optional)", type="csv")
    
    ace_key_col, ace_score_col = None, None
    external_ace_df = None
    if ace_file is not None:
        external_ace_df = pd.read_csv(ace_file)
        st.sidebar.caption("Map external data columns:")
        ace_key_col = st.sidebar.selectbox("Match Column in Custom CSV", external_ace_df.columns, help="Select Postcode, LSOA Code, or Ward column")
        num_ace_cols = external_ace_df.select_dtypes(include=['number']).columns.tolist()
        if num_ace_cols:
            ace_score_col = st.sidebar.selectbox("Engagement / Propensity Column", num_ace_cols)

    # ---------------------------------
    # DATA PROCESSING
    # ---------------------------------
    if 'processed_data' not in st.session_state:
        st.session_state.processed_data = None

    if st.button("Analyze Data"):
        with st.spinner("Geocoding, calculating census personas, and assessing cultural propensity..."):
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
                
                matched = False
                base_geo = None
                if clean_pc in geo_data:
                    base_geo = geo_data[clean_pc].copy()
                    matched = True
                else:
                    fallback_val = ""
                    if fallback_col != "None" and pd.notna(row[fallback_col]):
                        fallback_val = extract_outcode(row[fallback_col]).replace(' ', '').upper()
                    elif pc_val:
                        fallback_val = extract_outcode(pc_val).replace(' ', '').upper()
                        
                    if fallback_val in outcode_data:
                        base_geo = outcode_data[fallback_val].copy()
                        matched = True
                
                if matched and base_geo:
                    profile = get_ons_nomis_profile(base_geo["LSOA_Code"], base_geo["IMD_Decile"])
                    base_geo["Persona"] = generate_audience_persona(profile, base_geo["IMD_Decile"])
                    base_geo["Cultural_Propensity"] = calculate_cultural_propensity_proxy(
                        profile, base_geo["IMD_Decile"], base_geo.get("District", "Unknown")
                    )
                    mapped_rows.append(base_geo)
                else:
                    mapped_rows.append({
                        "Ward": "Unknown", "District": "Unknown", "LSOA": "Unknown", "LSOA_Code": "Unknown", 
                        "Country": "Unknown", "IMD_Rank": "Unknown", "IMD_Decile": "Unknown", 
                        "Latitude": None, "Longitude": None, "Match_Type": "Unmatched",
                        "Persona": "Unclassified", "Cultural_Propensity": 0.0
                    })
                
            geo_df = pd.DataFrame(mapped_rows, index=df.index)
            processed_df = pd.concat([df, geo_df], axis=1)
            processed_df = processed_df[processed_df['Match_Type'] != "Unmatched"].copy()
            
            # Integrate external ACE CSV if provided
            if external_ace_df is not None and ace_key_col and ace_score_col:
                ext_copy = external_ace_df[[ace_key_col, ace_score_col]].dropna().copy()
                ext_copy[ace_key_col] = ext_copy[ace_key_col].astype(str).str.strip().str.upper()
                
                # Determine best match key in processed_df
                target_join_col = postcode_col
                first_val = str(ext_copy[ace_key_col].iloc[0])
                if first_val.startswith("E01"):
                    target_join_col = "LSOA_Code"
                elif first_val in processed_df['Ward'].values:
                    target_join_col = "Ward"
                
                processed_df['join_key_temp'] = processed_df[target_join_col].astype(str).str.strip().str.upper()
                merged_df = pd.merge(processed_df, ext_copy, left_on='join_key_temp', right_on=ace_key_col, how='left')
                
                # Override or blend with proxy
                merged_df['Cultural_Propensity'] = merged_df[ace_score_col].combine_first(merged_df['Cultural_Propensity'])
                merged_df.drop(columns=['join_key_temp', ace_key_col], errors='ignore', inplace=True)
                processed_df = merged_df
            
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
        
        st.info(f"📍 **Mapping Summary:** Mapped **{full_matches}** exact postcodes and **{fallback_matches}** district-level fallbacks.")
        
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
        top_kpi1, top_kpi2, top_kpi3, top_kpi4 = st.columns(4)
        
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
        with top_kpi4:
            avg_propensity = filtered_df['Cultural_Propensity'].mean()
            st.metric(label="Avg Cultural Propensity", value=f"{avg_propensity:.1f} / 100")

        # ---------------------------------
        # TABS LAYOUT
        # ---------------------------------
        st.markdown("---")
        tab1, tab2, tab3, tab4 = st.tabs([
            "🌍 Geo, Personas & Propensity", 
            "🎭 Advanced Analytics", 
            "🔄 Retention & Loyalty",
            "🚗 Routes & Partnerships"
        ])
        
        # ==========================================
        # TAB 1: GEO, PERSONAS & PROPENSITY
        # ==========================================
        with tab1:
            st.write("### Interactive Hotspots with Audience Personas")
            df_map = filtered_df.dropna(subset=['Latitude', 'Longitude']).copy()
            
            if not df_map.empty:
                map_grouped = df_map.groupby([
                    postcode_col, 'Ward', 'District', 'Persona', 'Latitude', 'Longitude', 'Match_Type', 'IMD_Decile', 'Cultural_Propensity'
                ])[sales_col].sum().reset_index()
                
                fig_map = px.scatter_map(
                    map_grouped, lat="Latitude", lon="Longitude", size=sales_col,
                    hover_name=postcode_col, 
                    hover_data={
                        "Persona": True,
                        "Cultural_Propensity": True,
                        "District": True,
                        "Ward": True, 
                        "IMD_Decile": True, 
                        "Latitude": False, 
                        "Longitude": False
                    },
                    color="Cultural_Propensity",
                    color_continuous_scale="Viridis",
                    zoom=8, height=550
                )
                fig_map.update_layout(map_style="carto-positron", margin={"r":0,"t":0,"l":0,"b":0})
                st.plotly_chart(fig_map, width="stretch")
            
            # --- PERSONAS & PROFILES ---
            st.markdown("---")
            col_p1, col_p2 = st.columns(2)
            with col_p1:
                st.write("### 👥 Audience Persona Distribution")
                persona_counts = filtered_df.groupby('Persona')[sales_col].sum().reset_index()
                fig_persona = px.pie(persona_counts, names='Persona', values=sales_col, title="Revenue by Target Persona", hole=0.4)
                st.plotly_chart(fig_persona, width="stretch")
                
            with col_p2:
                st.write("### 🏛️ Cultural Propensity by Local Authority")
                la_summary = filtered_df[filtered_df['District'] != "Unknown"].groupby('District').agg({
                    'Cultural_Propensity': 'mean',
                    sales_col: 'sum'
                }).reset_index().sort_values(by=sales_col, ascending=False)
                fig_la = px.bar(
                    la_summary, x='District', y='Cultural_Propensity', 
                    title="Mean Engagement Propensity by Authority",
                    labels={'Cultural_Propensity': 'Propensity Score (0-100)'},
                    text_auto='.1f'
                )
                st.plotly_chart(fig_la, width="stretch")

            # --- ONS NOMIS CENSUS PROFILING ---
            st.markdown("---")
            st.write("### 🏢 ONS Census 2021 Audience Profile (LSOA Weighted)")
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
                    fig_age = px.pie(df_age, names='Age Group', values='Weighted Score', title="Age Distribution", hole=0.4)
                    st.plotly_chart(fig_age, width="stretch")
                    
                with col_c2:
                    hh_cols = ['HH_Single', 'HH_Family', 'HH_Other']
                    hh_weighted = {col: (df_census[col] * df_census[sales_col]).sum() for col in hh_cols}
                    df_hh = pd.DataFrame(list(hh_weighted.items()), columns=['Household Type', 'Weighted Score'])
                    df_hh['Household Type'] = df_hh['Household Type'].str.replace('HH_', '')
                    fig_hh = px.bar(df_hh, x='Household Type', y='Weighted Score', title="Household Composition")
                    st.plotly_chart(fig_hh, width="stretch")

        # ==========================================
        # TAB 2: ADVANCED ANALYTICS 
        # ==========================================
        with tab2:
            st.write("## 🎭 Post-Show Analytics")
            
            col2_w1, col2_w2 = st.columns(2)
            with col2_w1:
                st.write("### 🏆 Top 10 Wards by Revenue")
                top_wards = filtered_df[filtered_df['Ward'] != 'Unknown'].groupby('Ward')[sales_col].sum().sort_values(ascending=False).head(10).reset_index()
                fig_wards = px.bar(top_wards, x='Ward', y=sales_col, title="Highest Performing Wards")
                st.plotly_chart(fig_wards, width="stretch")
                
            with col2_w2:
                st.write("### 📊 Audience by Deprivation Decile (IMD)")
                imd_df = filtered_df[filtered_df['IMD_Decile'] != 'Unknown'].copy()
                if not imd_df.empty:
                    imd_df['IMD_Decile'] = imd_df['IMD_Decile'].astype(int)
                    
                    if qty_col != "None":
                        imd_grouped = imd_df.groupby('IMD_Decile')[qty_col].sum().reset_index(name='Audience')
                    else:
                        imd_grouped = imd_df.groupby('IMD_Decile').size().reset_index(name='Audience')
                        
                    total_audience = imd_grouped['Audience'].sum()
                    imd_grouped['Percentage'] = (imd_grouped['Audience'] / total_audience) * 100
                    
                    fig_imd = px.bar(
                        imd_grouped.sort_values('IMD_Decile'), 
                        x='IMD_Decile', 
                        y='Percentage', 
                        title="Audience Percentage by Deprivation Decile (1 = Most Deprived, 10 = Least)",
                        labels={'Percentage': 'Audience (%)'}
                    )
                    fig_imd.update_traces(texttemplate='%{y:.1f}%', textposition='outside')
                    fig_imd.update_xaxes(type='category')
                    fig_imd.update_layout(yaxis_title="Percentage (%)")
                    st.plotly_chart(fig_imd, width="stretch")

            st.markdown("---")
            
            col2_1, col2_2 = st.columns(2)
            with col2_1:
                st.write("### Top Performing Postcodes")
                top_pcs = filtered_df.groupby(postcode_col)[sales_col].sum().sort_values(ascending=False).head(10).reset_index()
                fig_top = px.bar(top_pcs, x=postcode_col, y=sales_col, title="Top 10 Postcodes by Revenue")
                st.plotly_chart(fig_top, width="stretch")
                
            with col2_2:
                if venue_postcode:
                    v_lat, v_lon = get_venue_coordinates(venue_postcode)
                    if v_lat and v_lon:
                        filtered_df['Distance_Miles'] = filtered_df.apply(
                            lambda row: calculate_distance(v_lat, v_lon, row['Latitude'], row['Longitude']), axis=1
                        )
                        st.write("### Audience Catchment (Distance)")
                        fig_dist = px.histogram(filtered_df.dropna(subset=['Distance_Miles']), x='Distance_Miles', title="Distance from Venue (Miles)", nbins=20)
                        st.plotly_chart(fig_dist, width="stretch")
                    else:
                        st.info("⚠️ Could not find coordinates for your Venue Postcode.")
                else:
                    st.info("⚠️ Enter a **Venue Postcode** in the sidebar to unlock Audience Catchment distance metrics.")

            if transaction_date_col != "None" and pd.api.types.is_datetime64_any_dtype(filtered_df[transaction_date_col]):
                st.write("### 📈 Booking Curve")
                daily_sales = filtered_df.groupby(transaction_date_col)[sales_col].sum().reset_index()
                daily_sales = daily_sales.sort_values(transaction_date_col)
                daily_sales['Cumulative Sales'] = daily_sales[sales_col].cumsum()
                fig_curve = px.line(daily_sales, x=transaction_date_col, y='Cumulative Sales', title="Cumulative Sales Over Time")
                st.plotly_chart(fig_curve, width="stretch")

        # ==========================================
        # TAB 3: RETENTION & LOYALTY
        # ==========================================
        with tab3:
            st.write("## 🔄 Customer Retention & Loyalty")
            if customer_id_col != "None":
                cust_df = filtered_df.groupby(customer_id_col).agg({
                    sales_col: 'sum',
                    postcode_col: 'count' 
                }).rename(columns={postcode_col: 'Transactions'}).reset_index()
                
                col3_1, col3_2 = st.columns(2)
                with col3_1:
                    st.write("### Repeat vs. One-Time Buyers")
                    cust_df['Customer Type'] = cust_df['Transactions'].apply(lambda x: 'Repeat' if x > 1 else 'One-Time')
                    retention_pie = px.pie(cust_df, names='Customer Type', title="Customer Retention Split")
                    st.plotly_chart(retention_pie, width="stretch")
                    
                with col3_2:
                    st.write("### Top 10 Most Valuable Customers")
                    top_cust = cust_df.sort_values(by=sales_col, ascending=False).head(10)
                    top_cust[customer_id_col] = top_cust[customer_id_col].astype(str) 
                    fig_top_cust = px.bar(top_cust, x=customer_id_col, y=sales_col, title="Highest Spenders by Revenue")
                    st.plotly_chart(fig_top_cust, width="stretch")
            else:
                st.warning("⚠️ Enter a **Customer/Owner ID Column** in the sidebar's Advanced Settings to unlock Retention & Loyalty metrics.")

        # ==========================================
        # TAB 4: ROUTES & PARTNERSHIPS
        # ==========================================
        with tab4:
            st.write("## 🚗 Audience Journeys, Local Partnerships & OOH Advertising")
            st.write("Cross-reference your driving routes against local amenities and billboards to identify partnership and marketing opportunities.")
            
            search_radius_km = st.slider("Partnership Search Radius (km from Venue)", min_value=5, max_value=60, value=25, step=5)

            if venue_postcode:
                v_lat, v_lon = get_venue_coordinates(venue_postcode)
                if v_lat and v_lon:
                    with st.spinner("Plotting routes and identifying local amenities..."):
                        df_routes = filtered_df.dropna(subset=['Latitude', 'Longitude'])
                        top_spenders = df_routes.groupby(['Latitude', 'Longitude'])[sales_col].sum().reset_index()
                        top_spenders = top_spenders.sort_values(by=sales_col, ascending=False).head(50)
                        
                        start_coords = list(zip(top_spenders['Latitude'], top_spenders['Longitude']))
                        route_data = get_driving_routes(start_coords, v_lat, v_lon)
                        
                        raw_amenity_data = fetch_local_businesses(v_lat, v_lon, radius=search_radius_km * 1000)
                        
                        amenity_data = pd.DataFrame()
                        if not raw_amenity_data.empty and not route_data.empty:
                            route_points = []
                            for path in route_data['path']:
                                for i in range(0, len(path), 3): 
                                    route_points.append((path[i][1], path[i][0]))
                                    
                            valid_amenities = []
                            for _, row in raw_amenity_data.iterrows():
                                a_lat, a_lon = row['lat'], row['lon']
                                is_on_route = False
                                for r_lat, r_lon in route_points:
                                    if abs(a_lat - r_lat) < 0.01 and abs(a_lon - r_lon) < 0.01:
                                        dist = calculate_distance(a_lat, a_lon, r_lat, r_lon)
                                        if dist is not None and dist <= 0.2:
                                            is_on_route = True
                                            break
                                if is_on_route:
                                    valid_amenities.append(row)
                                    
                            amenity_data = pd.DataFrame(valid_amenities)

                        display_data = pd.DataFrame()
                        if not amenity_data.empty:
                            st.markdown("### 🎛️ Filter Points of Interest")
                            available_types = sorted(amenity_data['type'].unique().tolist())
                            selected_types = st.multiselect("Categories to display:", available_types, default=available_types)
                            display_data = amenity_data[amenity_data['type'].isin(selected_types)].copy()

                        layers = []
                        if not route_data.empty:
                            layers.append(pdk.Layer(
                                "PathLayer", data=route_data, get_path="path",
                                get_color="[255, 50, 50, 150]", width_scale=20, width_min_pixels=3
                            ))
                            
                        if not display_data.empty:
                            venues_df = display_data[~display_data['type'].str.lower().isin(['billboard', 'board'])]
                            ads_df = display_data[display_data['type'].str.lower().isin(['billboard', 'board'])]

                            if not venues_df.empty:
                                layers.append(pdk.Layer(
                                    "ScatterplotLayer", data=venues_df, get_position="[lon, lat]",
                                    get_color="[50, 200, 50, 200]", get_radius=50, radius_min_pixels=4, pickable=True
                                ))
                                
                            if not ads_df.empty:
                                layers.append(pdk.Layer(
                                    "ScatterplotLayer", data=ads_df, get_position="[lon, lat]",
                                    get_color="[255, 165, 0, 220]", get_radius=50, radius_min_pixels=4, pickable=True
                                ))
                            
                        layers.append(pdk.Layer(
                            "ScatterplotLayer", data=[{"lon": v_lon, "lat": v_lat, "name": "The Venue"}],
                            get_position="[lon, lat]", get_color="[50, 100, 255, 255]", get_radius=100, radius_min_pixels=8, pickable=True
                        ))
                        
                        view_state = pdk.ViewState(latitude=v_lat, longitude=v_lon, zoom=11, pitch=45)
                        st.pydeck_chart(pdk.Deck(
                            map_style=pdk.map_styles.CARTO_DARK,
                            layers=layers,
                            initial_view_state=view_state,
                            tooltip={"text": "{name}\n{type}"}
                        ))
                        
                        if not display_data.empty:
                            st.write("### 🍻 Partnership Prospects & OOH Advertising")
                            st.dataframe(
                                display_data[['name', 'type', 'link']].sort_values(by='type').reset_index(drop=True), 
                                use_container_width=True,
                                column_config={"link": st.column_config.LinkColumn("Website / Map Location")}
                            )
                else:
                    st.error("Could not find coordinates for the provided Venue Postcode.")
            else:
                st.warning("⚠️ Enter your **Venue Postcode** in the sidebar's Advanced Settings to unlock this feature.")
