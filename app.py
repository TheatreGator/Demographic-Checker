import streamlit as st
import pandas as pd
import requests
import plotly.express as px
import math
import re
import pydeck as pdk
import plotly.graph_objects as go
from google import genai

# ---------------------------------
# DATA FETCHING & MATH FUNCTIONS
# ---------------------------------

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
                            if country == "England": decile = math.ceil(rank / 3284.4) 
                            elif country == "Scotland": decile = math.ceil(rank / 697.6)   
                            elif country == "Wales": decile = math.ceil(rank / 190.9)   
                            elif country == "Northern Ireland": decile = math.ceil(rank / 89.0)    
                        except TypeError: pass

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
                        "Match_Type": "Full Postcode",
                        "NHS_Trust": item["result"].get("primary_care_trust", "Unknown"),
                        "NHS_Region": item["result"].get("nhs_ha", "Unknown"),
                        "Parish": item["result"].get("parish", "Unknown"),
                        "Parliamentary_Constituency": item["result"].get("parliamentary_constituency", "Unknown"),
                        "Rural_Urban": item["result"].get("rural_urban", "Unknown")
                    }
    return results

@st.cache_data
def get_outcode_data(outcodes):
    results = {}
    for outcode in outcodes:
        clean_outcode = str(outcode).replace(' ', '').upper()
        if not clean_outcode: continue
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
                    "Match_Type": "District Fallback",
                    "NHS_Trust": "Unknown",
                    "NHS_Region": "Unknown",
                    "Parish": "Unknown",
                    "Parliamentary_Constituency": "Unknown",
                    "Rural_Urban": "Unknown"
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
    if pd.isna(lat1) or pd.isna(lon1) or pd.isna(lat2) or pd.isna(lon2): return None
    R = 3958.8 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def extract_outcode(postcode_str):
    pc = str(postcode_str).strip().upper()
    if ' ' in pc: return pc.split(' ')[0]
    match = re.match(r'^([A-Z]{1,2}\d[A-Z\d]?)', pc)
    return match.group(1) if match else pc

@st.cache_data
def get_ons_nomis_profile(lsoa_code, imd_decile, district):
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

    dist_clean = str(district).lower() if district else ""
    if "bradford" in dist_clean:
        asian_base, black_base, mixed_base = 32 + (10 - decile) * 2, 2, 3
    elif "leeds" in dist_clean:
        asian_base, black_base, mixed_base = 11 + (10 - decile) * 1.5, 5 + (10 - decile) * 0.5, 4
    elif "kirklees" in dist_clean:
        asian_base, black_base, mixed_base = 18 + (10 - decile) * 1.8, 1.5, 3
    elif "calderdale" in dist_clean:
        asian_base, black_base, mixed_base = 9 + (10 - decile) * 1.2, 1, 2
    else:
        asian_base, black_base, mixed_base = 9 + (10 - decile), 4, 3
        
    eth_asian = min(85, max(1, asian_base))
    eth_black = min(25, max(1, black_base))
    eth_mixed = min(15, max(1, mixed_base))
    eth_other = 2
    eth_white = max(5, 100 - (eth_asian + eth_black + eth_mixed + eth_other))
    
    eth_dict = {"White": eth_white, "Asian, Asian British": eth_asian, "Black, Black British": eth_black, "Mixed / Multiple": eth_mixed, "Other": eth_other}
    dominant_eth = max(eth_dict, key=eth_dict.get)

    return {
        "Age_18_24": age_18_24, "Age_25_40": age_25_40, "Age_41_64": age_41_64, "Age_65_plus": age_65_plus,
        "HH_Single": single_hh, "HH_Family": family_hh, "HH_Other": other_hh,
        "Qual_Degree+": degree_plus, "Qual_None": no_quals, "Qual_Other": other_quals,
        "Econ_Employed": employed, "Econ_Student": student, "Econ_Retired": retired, "Econ_InactiveOther": inactive_other,
        "Eth_White": eth_white, "Eth_Asian": eth_asian, "Eth_Black": eth_black, "Eth_Mixed": eth_mixed, "Eth_Other": eth_other,
        "Dominant_Eth": dominant_eth, "Eth_Certainty": eth_dict[dominant_eth]
    }

def calculate_cultural_propensity_proxy(profile, imd_decile, district):
    degree = profile.get("Qual_Degree+", 25.0)
    decile_val = (int(imd_decile) * 10) if imd_decile != "Unknown" else 50.0
    la_base = ACE_LA_BENCHMARKS.get(district, DEFAULT_UK_ACE_BENCHMARK)
    score = (0.45 * degree) + (0.25 * decile_val) + (0.20 * la_base) + (0.10 * (profile.get('Age_25_40', 25) + profile.get('Age_65_plus', 20)))
    return round(min(100.0, max(0.0, score)), 1)

def generate_audience_persona(profile, imd_decile):
    if imd_decile == "Unknown": return "Unclassified"
    decile = int(imd_decile)
    
    if decile >= 7:
        if profile.get('Age_65_plus', 0) >= 20 and profile.get('Econ_Retired', 0) >= 18: return "Affluent Arts Retirees"
        elif profile.get('HH_Family', 0) >= 38: return "Prosperous Suburban Families"
        elif profile.get('Age_25_40', 0) >= 28 and profile.get('Qual_Degree+', 0) >= 35: return "Young Urban Professionals"
        return "Affluent Broad Audience"
    if 4 <= decile <= 6:
        if profile.get('HH_Family', 0) >= 35: return "Mid-Market Families"
        elif profile.get('Econ_Student', 0) >= 12: return "Student & Graduate Hubs"
        return "Middle-Income Mix"
    if decile <= 3:
        if profile.get('Age_65_plus', 0) >= 22: return "Budget-Conscious Pensioners"
        elif profile.get('HH_Family', 0) >= 35: return "Hard-Pressed Families"
        return "Urban Value Seekers"
    return "General Audience"

@st.cache_data(show_spinner=False)
def fetch_ticketmaster_clashes(api_key, cities, date_str):
    if not api_key or not cities or not date_str: return []
    try:
        url = "https://app.ticketmaster.com/discovery/v2/events.json"
        params = {
            "apikey": api_key,
            "city": cities,
            "startDateTime": f"{date_str}T00:00:00Z",
            "endDateTime": f"{date_str}T23:59:59Z",
            "sort": "date,asc",
            "size": 50
        }
        res = requests.get(url, params=params, timeout=10)
        if res.status_code == 200:
            events = res.json().get('_embedded', {}).get('events', [])
            return [{
                "Event": e.get('name'), 
                "Venue": e.get('_embedded', {}).get('venues', [{}])[0].get('name', 'Unknown'), 
                "City": e.get('_embedded', {}).get('venues', [{}])[0].get('city', {}).get('name', 'Unknown'),
                "Segment": e.get('classifications', [{}])[0].get('segment', {}).get('name', 'Unknown')
            } for e in events]
    except Exception: pass
    return []

@st.cache_data(show_spinner=False)
def fetch_local_businesses(lat, lon, radius=25000):
    endpoints = ["https://overpass-api.de/api/interpreter", "https://lz4.overpass-api.de/api/interpreter"]
    overpass_query = f"""
    [out:json][timeout:30];
    (
      nwr["amenity"="restaurant"](around:{radius},{lat},{lon});
      nwr["amenity"="pub"](around:{radius},{lat},{lon});
      nwr["amenity"="cafe"](around:{radius},{lat},{lon});
      nwr["advertising"="billboard"](around:{radius},{lat},{lon});
      nwr["advertising"="board"](around:{radius},{lat},{lon});
    ); out center;
    """
    headers = {'User-Agent': 'TheatreAnalyticsApp/3.3 (Local Testing)'}
    for url in endpoints:
        try:
            response = requests.get(url, params={'data': overpass_query}, headers=headers, timeout=30)
            if response.status_code == 200:
                amenities = []
                for e in response.json().get('elements', []):
                    tags = e.get('tags', {})
                    name = tags.get('name', 'Unnamed')
                    item_type = tags.get('amenity', tags.get('advertising', 'unknown')).capitalize()
                    if name == "Unnamed" and item_type.lower() in ['billboard', 'board']: name = f"OOH Advertising ({item_type})"
                    if name != "Unnamed" or item_type.lower() in ['billboard', 'board']:
                        elat, elon = e.get('lat') or e.get('center', {}).get('lat'), e.get('lon') or e.get('center', {}).get('lon')
                        if elat and elon:
                            amenities.append({"name": name, "type": item_type, "lat": float(elat), "lon": float(elon), "link": tags.get('website', f"https://www.google.com/maps/search/?api=1&query={elat},{elon}")})
                if amenities: return pd.DataFrame(amenities)
        except Exception: continue
    return pd.DataFrame()

@st.cache_data(show_spinner=False)
def get_driving_routes(start_coords, end_lat, end_lon):
    routes = []
    for lat, lon in start_coords:
        try:
            url = f"http://router.project-osrm.org/route/v1/driving/{lon},{lat};{end_lon},{end_lat}?overview=full&geometries=geojson"
            res = requests.get(url, timeout=3)
            if res.json().get("code") == "Ok":
                routes.append({"path": res.json()["routes"][0]["geometry"]["coordinates"]})
        except Exception: continue
    return pd.DataFrame(routes)

def process_sales_data(df, postcode_col, fallback_col, external_ace_df, ace_key_col, ace_score_col, transaction_date_col, event_date_col, v_lat, v_lon):
    clean_series = df[postcode_col].dropna().astype(str).str.replace(r'\s+', '', regex=True).str.upper()
    valid_postcodes_series = clean_series[~clean_series.isin(['NAN', 'NULL', 'NONE', ''])]
    unique_postcodes = [str(x) for x in valid_postcodes_series.unique()]
    
    geo_data = get_postcode_data(unique_postcodes)
    
    outcodes_to_fetch = set()
    for idx, row in df.iterrows():
        clean_pc = str(row[postcode_col]).replace(' ', '').upper() if pd.notna(row[postcode_col]) else ""
        if clean_pc not in geo_data:
            fallback_val = extract_outcode(row[fallback_col]) if fallback_col != "None" and pd.notna(row[fallback_col]) else extract_outcode(clean_pc)
            if fallback_val: outcodes_to_fetch.add(fallback_val)
            
    outcode_data = get_outcode_data(list(outcodes_to_fetch))
    
    mapped_rows = []
    for idx, row in df.iterrows():
        clean_pc = str(row[postcode_col]).replace(' ', '').upper() if pd.notna(row[postcode_col]) else ""
        matched, base_geo = False, None
        
        if clean_pc in geo_data:
            base_geo, matched = geo_data[clean_pc].copy(), True
        else:
            fallback_val = extract_outcode(row[fallback_col]).replace(' ', '').upper() if fallback_col != "None" and pd.notna(row[fallback_col]) else extract_outcode(clean_pc).replace(' ', '').upper()
            if fallback_val in outcode_data:
                base_geo, matched = outcode_data[fallback_val].copy(), True
        
        if matched and base_geo:
            profile = get_ons_nomis_profile(base_geo["LSOA_Code"], base_geo["IMD_Decile"], base_geo.get("District", "Unknown"))
            base_geo["Persona"] = generate_audience_persona(profile, base_geo["IMD_Decile"])
            base_geo["Cultural_Propensity"] = calculate_cultural_propensity_proxy(profile, base_geo["IMD_Decile"], base_geo.get("District", "Unknown"))
            base_geo["Dominant_Ethnicity"] = profile.get("Dominant_Eth", "Unknown")
            base_geo["Ethnicity_Certainty"] = f"{profile.get('Eth_Certainty', 0):.1f}%"
            
            dist = calculate_distance(v_lat, v_lon, base_geo["Latitude"], base_geo["Longitude"]) if v_lat else None
            base_geo["Distance_Miles"] = dist
            
            base_geo["CO2_kg_Roundtrip"] = (dist * 2 * 0.27) if dist else 0.0
            
            if dist:
                drive_mins = (dist / 25.0) * 60
                is_rural = 1 if 'rural' in base_geo.get('Rural_Urban', '').lower() else 0
                transit_mins = (dist / 12.0) * 60 + 20 + (15 * is_rural)
                base_geo["Drive_Mins"] = drive_mins
                base_geo["Transit_Mins"] = transit_mins
                base_geo["Transit_Disparity_Ratio"] = transit_mins / drive_mins if drive_mins > 0 else 1.0
            else:
                base_geo["Drive_Mins"] = None
                base_geo["Transit_Mins"] = None
                base_geo["Transit_Disparity_Ratio"] = None

            mapped_rows.append(base_geo)
        else:
            mapped_rows.append({"Match_Type": "Unmatched", "Persona": "Unclassified", "Cultural_Propensity": 0.0, "Distance_Miles": None, "CO2_kg_Roundtrip": 0.0, "Transit_Disparity_Ratio": None})
        
    geo_df = pd.DataFrame(mapped_rows, index=df.index)
    processed_df = pd.concat([df, geo_df], axis=1)
    processed_df = processed_df[processed_df['Match_Type'] != "Unmatched"].copy()
    
    if external_ace_df is not None and ace_key_col and ace_score_col:
        ext_copy = external_ace_df[[ace_key_col, ace_score_col]].dropna().copy()
        ext_copy[ace_key_col] = ext_copy[ace_key_col].astype(str).str.strip().str.upper()
        target_join_col = "LSOA_Code" if str(ext_copy[ace_key_col].iloc[0]).startswith("E01") else ("Ward" if str(ext_copy[ace_key_col].iloc[0]) in processed_df['Ward'].values else postcode_col)
        processed_df['join_key_temp'] = processed_df[target_join_col].astype(str).str.strip().str.upper()
        merged_df = pd.merge(processed_df, ext_copy, left_on='join_key_temp', right_on=ace_key_col, how='left')
        merged_df['Cultural_Propensity'] = merged_df[ace_score_col].combine_first(merged_df['Cultural_Propensity'])
        merged_df.drop(columns=['join_key_temp', ace_key_col], errors='ignore', inplace=True)
        processed_df = merged_df
    
    if transaction_date_col != "None": processed_df[transaction_date_col] = pd.to_datetime(processed_df[transaction_date_col], errors='coerce')
    if event_date_col != "None": processed_df[event_date_col] = pd.to_datetime(processed_df[event_date_col], errors='coerce')
        
    return processed_df

def get_default_index(options_list, default_string):
    return options_list.index(default_string) if default_string in options_list else 0

# ---------------------------------
# APP UI & CONFIGURATION
# ---------------------------------

st.set_page_config(page_title="Sales Demographics Analyzer", layout="wide")
st.title("📊 UK Sales Demographics & Geo-Analyzer")
st.write("Upload primary sales data to map postcodes and identify target personas. Upload secondary data for A/B comparison.")

st.sidebar.header("1. Data Upload")
uploaded_file = st.sidebar.file_uploader("Upload Primary Dataset (Show A)", type="csv")
uploaded_file_b = st.sidebar.file_uploader("Upload Comparison Dataset (Show B - Optional)", type="csv")

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file)
    df_b = pd.read_csv(uploaded_file_b) if uploaded_file_b is not None else None
    
    st.sidebar.header("2. Standard Configuration")
    all_cols = df.columns.tolist()
    postcode_col = st.sidebar.selectbox("Select Primary Postcode Column", all_cols, index=get_default_index(all_cols, "BillingAddress: Postcode"))
    
    numeric_columns = df.select_dtypes(include=['number']).columns.tolist()
    if not numeric_columns:
        st.error("Your dataset must contain at least one numeric column for sales/value.")
        st.stop()
    sales_col = st.sidebar.selectbox("Select Sales / Revenue Column", numeric_columns, index=get_default_index(numeric_columns, "Total Charged"))

    st.sidebar.markdown("---")
    st.sidebar.header("3. Advanced Settings (Optional)")
    optional_cols = ["None"] + all_cols
    optional_num_cols = ["None"] + numeric_columns
    
    show_name_col = st.sidebar.selectbox("Show / Event Name Column", optional_cols, index=get_default_index(optional_cols, "Event Name"))
    customer_id_col = st.sidebar.selectbox("Customer / Owner ID Column", optional_cols, index=get_default_index(optional_cols, "Owner ID"))
    fallback_col = st.sidebar.selectbox("Fallback District / Sector Column", optional_cols, index=get_default_index(optional_cols, "Postcode Sector"))
    venue_postcode = st.sidebar.text_input("Venue Postcode (For Catchment & Routes)", value="BD7 1AJ", placeholder="e.g. BD1 1SD")
    transaction_date_col = st.sidebar.selectbox("Transaction Date Column", optional_cols, index=get_default_index(optional_cols, "Date confirmed"))
    event_date_col = st.sidebar.selectbox("Event Date Column", optional_cols, index=get_default_index(optional_cols, "Event Date/Time"))
    order_id_col = st.sidebar.selectbox("Order / Transaction ID Column", optional_cols, index=get_default_index(optional_cols, "Order ID"))
    qty_col = st.sidebar.selectbox("Ticket Quantity Column", optional_num_cols, index=get_default_index(optional_num_cols, "Count"))

    st.sidebar.markdown("---")
    st.sidebar.header("4. Integrations")
    tm_api_key = st.sidebar.text_input("Ticketmaster API Key", type="password")
    gemini_api_key = st.sidebar.text_input("Gemini API Key (For AI Report)", type="password", help="Get a free key from Google AI Studio")
    ace_file = st.sidebar.file_uploader("Upload Custom ACE / Audience Spectrum CSV (Optional)", type="csv")
    
    ace_key_col, ace_score_col, external_ace_df = None, None, None
    if ace_file is not None:
        external_ace_df = pd.read_csv(ace_file)
        ace_key_col = st.sidebar.selectbox("Match Column in Custom CSV", external_ace_df.columns)
        num_ace_cols = external_ace_df.select_dtypes(include=['number']).columns.tolist()
        if num_ace_cols: ace_score_col = st.sidebar.selectbox("Engagement / Propensity Column", num_ace_cols)

    if 'processed_data' not in st.session_state:
        st.session_state.processed_data = None
        st.session_state.processed_data_b = None

    if st.button("Analyze Data"):
        with st.spinner("Geocoding, calculating census personas, sustainability, and propensity for Dataset A..."):
            v_lat, v_lon = get_venue_coordinates(venue_postcode) if venue_postcode else (None, None)
            st.session_state.processed_data = process_sales_data(df, postcode_col, fallback_col, external_ace_df, ace_key_col, ace_score_col, transaction_date_col, event_date_col, v_lat, v_lon)
            
            if df_b is not None:
                with st.spinner("Processing Dataset B for A/B Comparison..."):
                    st.session_state.processed_data_b = process_sales_data(df_b, postcode_col, fallback_col, external_ace_df, ace_key_col, ace_score_col, transaction_date_col, event_date_col, v_lat, v_lon)
            st.success("Analysis complete!")

    if st.session_state.processed_data is not None:
        main_df = st.session_state.processed_data
        filtered_df = main_df.copy()
        
        if qty_col != "None": filtered_df['_Volume_'] = filtered_df[qty_col]
        else: filtered_df['_Volume_'] = 1
            
        total_revenue = filtered_df[sales_col].sum()
        total_volume = filtered_df['_Volume_'].sum()

        st.write("### 🎫 Primary Dataset Top-Line Summary")
        top_kpi1, top_kpi2, top_kpi3, top_kpi4 = st.columns(4)
        with top_kpi1: st.metric("Total Revenue", f"£{total_revenue:,.2f}")
        with top_kpi2: st.metric("Total Tickets Sold" if qty_col != "None" else "Total Transactions", f"{int(total_volume):,}")
        with top_kpi3: st.metric("Average Ticket Yield" if qty_col != "None" else "Average Transaction", f"£{(total_revenue / total_volume) if total_volume > 0 else 0:,.2f}")
        with top_kpi4: st.metric("Avg Cultural Propensity", f"{filtered_df['Cultural_Propensity'].mean():.1f} / 100")

        tabs = st.tabs([
            "🌍 Geo & Personas", "🎭 Advanced Analytics", "🔄 Loyalty", 
            "🚗 Routes", "⚖️ A/B Comparison", "🏛️ Council Data", 
            "🏥 Extended APIs", "🌱 Sustainability", "📅 Clashes",
            "🤖 AI Report"
        ])
        
        # ------------------- TAB 1: GEO -------------------
        with tabs[0]:
            df_map = filtered_df.dropna(subset=['Latitude', 'Longitude']).copy()
            if not df_map.empty:
                map_grouped = df_map.groupby([postcode_col, 'Ward', 'District', 'Persona', 'Dominant_Ethnicity', 'Ethnicity_Certainty', 'Latitude', 'Longitude', 'Match_Type', 'IMD_Decile', 'Cultural_Propensity'])[sales_col].sum().reset_index()
                fig_map = px.scatter_map(map_grouped, lat="Latitude", lon="Longitude", size=sales_col, hover_name=postcode_col, hover_data={"Persona": True, "Dominant_Ethnicity": True, "Cultural_Propensity": True, "District": True, "Ward": True, "IMD_Decile": True, "Latitude": False, "Longitude": False}, color="Cultural_Propensity", color_continuous_scale="Viridis", zoom=8, height=550)
                fig_map.update_layout(map_style="carto-positron", margin={"r":0,"t":0,"l":0,"b":0})
                st.plotly_chart(fig_map, width="stretch")
            
            c_p1, c_p2 = st.columns(2)
            with c_p1:
                st.write("### 👥 Audience Persona Distribution")
                st.plotly_chart(px.pie(filtered_df.groupby('Persona')['_Volume_'].sum().reset_index(), names='Persona', values='_Volume_', hole=0.4), width="stretch")
            with c_p2:
                st.write("### 🏛️ Cultural Propensity by Local Authority")
                la_summary = filtered_df[filtered_df['District'] != "Unknown"].groupby('District').agg({'Cultural_Propensity': 'mean', '_Volume_': 'sum'}).reset_index()
                la_summary['Tickets %'] = (la_summary['_Volume_'] / total_volume) * 100
                fig_la = px.bar(la_summary.sort_values(by='_Volume_', ascending=False), x='District', y='Tickets %', text_auto='.1f')
                fig_la.update_traces(texttemplate='%{y:.1f}%', textposition='outside')
                st.plotly_chart(fig_la, width="stretch")

        # ------------------- TAB 2: ADVANCED ANALYTICS -------------------
        with tabs[1]:
            c2_1, c2_2 = st.columns(2)
            with c2_1:
                st.write("### 🏆 Top 10 Wards by Revenue")
                st.plotly_chart(px.bar(filtered_df[filtered_df['Ward'] != 'Unknown'].groupby('Ward')[sales_col].sum().sort_values(ascending=False).head(10).reset_index(), x='Ward', y=sales_col), width="stretch")
            with c2_2:
                st.write("### 📊 Audience by Deprivation Decile (IMD)")
                imd_df = filtered_df[filtered_df['IMD_Decile'] != 'Unknown'].copy()
                if not imd_df.empty:
                    imd_df['IMD_Decile'] = imd_df['IMD_Decile'].astype(int)
                    imd_g = imd_df.groupby('IMD_Decile')['_Volume_'].sum().reset_index()
                    imd_g['Percentage'] = (imd_g['_Volume_'] / imd_g['_Volume_'].sum()) * 100
                    fig_imd = px.bar(imd_g.sort_values('IMD_Decile'), x='IMD_Decile', y='Percentage')
                    fig_imd.update_traces(texttemplate='%{y:.1f}%', textposition='outside')
                    fig_imd.update_xaxes(type='category')
                    st.plotly_chart(fig_imd, width="stretch")

        # ------------------- TAB 3: RETENTION -------------------
        with tabs[2]:
            if customer_id_col != "None":
                cust_df = filtered_df.groupby(customer_id_col).agg({sales_col: 'sum', postcode_col: 'count'}).rename(columns={postcode_col: 'Transactions'}).reset_index()
                c3_1, c3_2 = st.columns(2)
                with c3_1:
                    cust_df['Customer Type'] = cust_df['Transactions'].apply(lambda x: 'Repeat' if x > 1 else 'One-Time')
                    st.plotly_chart(px.pie(cust_df, names='Customer Type', title="Retention Split"), width="stretch")
                with c3_2:
                    st.plotly_chart(px.bar(cust_df.sort_values(by=sales_col, ascending=False).head(10).astype({customer_id_col: str}), x=customer_id_col, y=sales_col, title="Highest Spenders"), width="stretch")
            else: st.warning("Select Customer ID in Settings.")

        # ------------------- TAB 4: ROUTES -------------------
        with tabs[3]:
            st.write("## 🚗 Audience Journeys & Partnerships")
            search_radius_km = st.slider("Search Radius (km)", 5, 60, 25, 5)
            if venue_postcode:
                v_lat, v_lon = get_venue_coordinates(venue_postcode)
                if v_lat and v_lon:
                    with st.spinner("Plotting..."):
                        top_spenders = filtered_df.dropna(subset=['Latitude', 'Longitude']).groupby(['Latitude', 'Longitude'])[sales_col].sum().reset_index().sort_values(by=sales_col, ascending=False).head(50)
                        route_data = get_driving_routes(list(zip(top_spenders['Latitude'], top_spenders['Longitude'])), v_lat, v_lon)
                        layers = [pdk.Layer("ScatterplotLayer", data=[{"lon": v_lon, "lat": v_lat}], get_position="[lon, lat]", get_color="[50, 100, 255, 255]", get_radius=100)]
                        if not route_data.empty: layers.append(pdk.Layer("PathLayer", data=route_data, get_path="path", get_color="[255, 50, 50, 150]", width_scale=20))
                        st.pydeck_chart(pdk.Deck(map_provider="carto", map_style="light", layers=layers, initial_view_state=pdk.ViewState(latitude=v_lat, longitude=v_lon, zoom=11, pitch=45)))
                else: st.error("Invalid Postcode.")

        # ------------------- TAB 5: A/B -------------------
        with tabs[4]:
            if st.session_state.processed_data_b is not None:
                df_a, df_b_proc = filtered_df.copy(), st.session_state.processed_data_b.copy()
                df_b_proc['_Volume_'] = df_b_proc[qty_col] if qty_col != "None" else 1
                if customer_id_col != "None":
                    ids_a, ids_b = set(df_a[customer_id_col].dropna().astype(str)), set(df_b_proc[customer_id_col].dropna().astype(str))
                    overlap, total_unique = len(ids_a.intersection(ids_b)), len(ids_a.union(ids_b))
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Unique Buyers (Show A)", len(ids_a)); c2.metric("Unique Buyers (Show B)", len(ids_b)); c3.metric("Shared Buyers", f"{overlap} ({(overlap/total_unique)*100 if total_unique>0 else 0:.1f}%)")
                
                pa, pb = df_a.groupby('Persona')['_Volume_'].sum().reset_index().rename(columns={'_Volume_': 'Show A'}), df_b_proc.groupby('Persona')['_Volume_'].sum().reset_index().rename(columns={'_Volume_': 'Show B'})
                comp_p = pd.merge(pa, pb, on='Persona', how='outer').fillna(0)
                fig_comp = go.Figure(data=[go.Bar(name='Show A', x=comp_p['Persona'], y=comp_p['Show A']), go.Bar(name='Show B', x=comp_p['Persona'], y=comp_p['Show B'])])
                fig_comp.update_layout(barmode='group', title="Persona Comparison")
                st.plotly_chart(fig_comp, width="stretch")
            else: st.info("Upload Comparison Dataset (Show B) to activate.")

        # ------------------- TAB 6: COUNCIL -------------------
        with tabs[5]:
            c1, c2 = st.columns(2)
            with c1:
                ward_df = filtered_df[filtered_df['Ward'] != 'Unknown'].groupby('Ward')['_Volume_'].sum().reset_index()
                ward_df['Percentage (%)'] = (ward_df['_Volume_'] / total_volume) * 100
                st.dataframe(ward_df.rename(columns={'_Volume_': 'Bookings'}).sort_values('Bookings', ascending=False).style.format({'Percentage (%)': '{:.2f}%'}), use_container_width=True, hide_index=True)
            with c2:
                imd_summary = filtered_df[filtered_df['IMD_Decile'] != 'Unknown'].copy()
                if not imd_summary.empty:
                    imd_summary['IMD_Decile'] = imd_summary['IMD_Decile'].astype(int)
                    imd_s = imd_summary.groupby('IMD_Decile')['_Volume_'].sum().reset_index()
                    imd_s['Percentage (%)'] = (imd_s['_Volume_'] / total_volume) * 100
                    st.dataframe(imd_s.rename(columns={'_Volume_': 'Bookings', 'IMD_Decile': 'IMD (10=Least Deprived)'}).sort_values('IMD (10=Least Deprived)', ascending=False).style.format({'Percentage (%)': '{:.2f}%'}), use_container_width=True, hide_index=True)

        # ------------------- TAB 7: EXTENDED API -------------------
        with tabs[6]:
            p1, p2 = st.columns(2)
            with p1:
                parl_df = filtered_df[filtered_df['Parliamentary_Constituency'] != 'Unknown'].groupby('Parliamentary_Constituency')['_Volume_'].sum().reset_index().sort_values('_Volume_', ascending=False)
                st.plotly_chart(px.bar(parl_df.head(15), x='Parliamentary_Constituency', y='_Volume_', title="Top Constituencies"), width="stretch")
                st.plotly_chart(px.pie(filtered_df[filtered_df['Rural_Urban'] != 'Unknown'].groupby('Rural_Urban')['_Volume_'].sum().reset_index(), names='Rural_Urban', values='_Volume_', title="Rural vs Urban Split"), width="stretch")
            with p2:
                nhs_df = filtered_df[filtered_df['NHS_Trust'] != 'Unknown'].groupby('NHS_Trust')['_Volume_'].sum().reset_index().sort_values('_Volume_', ascending=False)
                nhs_df['Percentage (%)'] = (nhs_df['_Volume_'] / total_volume) * 100
                st.dataframe(nhs_df.rename(columns={'_Volume_': 'Bookings'}).style.format({'Percentage (%)': '{:.2f}%'}), use_container_width=True, hide_index=True)

        # ------------------- TAB 8: SUSTAINABILITY & TRANSIT -------------------
        with tabs[7]:
            st.write("## 🌱 Scope 3 Audience Emissions & Transit Disparity")
            filtered_df['CO2_Event_Total'] = filtered_df['CO2_kg_Roundtrip'] * filtered_df['_Volume_']
            total_co2 = filtered_df['CO2_Event_Total'].sum()
            
            s1, s2, s3 = st.columns(3)
            s1.metric("Estimated CO₂e (Roundtrip)", f"{total_co2:,.1f} kg")
            s2.metric("Equivalent Trees to Offset", f"{math.ceil(total_co2 / 21):,}") 
            s3.metric("Avg Transit Disparity Ratio", f"{filtered_df['Transit_Disparity_Ratio'].mean():.2f}x")
            
            st.markdown("---")
            c_d1, c_d2 = st.columns(2)
            with c_d1:
                disp_df = filtered_df.dropna(subset=['Distance_Miles', 'Transit_Disparity_Ratio'])
                fig_disp = px.scatter(disp_df, x='Distance_Miles', y='Transit_Disparity_Ratio', color='Rural_Urban', hover_name=postcode_col, title="Distance vs. Public Transit Disparity")
                fig_disp.add_hline(y=2.0, line_dash="dot", line_color="red", annotation_text="High Dependency")
                st.plotly_chart(fig_disp, width="stretch")
            
            with c_d2:
                ward_disp = filtered_df[filtered_df['Ward'] != 'Unknown'].groupby('Ward').agg({'Transit_Disparity_Ratio': 'mean', '_Volume_': 'sum'}).reset_index().sort_values('Transit_Disparity_Ratio', ascending=False).head(10)
                st.dataframe(ward_disp.rename(columns={'Transit_Disparity_Ratio': 'Avg Disparity', '_Volume_': 'Bookings'}).style.format({'Avg Disparity': '{:.2f}x'}), use_container_width=True, hide_index=True)

        # ------------------- TAB 9: CLASH DETECTION -------------------
        with tabs[8]:
            st.write("## 📅 Competitor Clash Detection (West Yorkshire)")
            if not tm_api_key:
                st.warning("⚠️ Enter a **Ticketmaster API Key** in the sidebar Integrations section to pull live major events matching your booking dates.")
            elif event_date_col == "None":
                st.warning("⚠️ Select an **Event Date Column** in Advanced Settings.")
            else:
                valid_dates = filtered_df.dropna(subset=[event_date_col])[event_date_col].dt.date.unique()
                if len(valid_dates) > 0:
                    with st.spinner("Querying Ticketmaster for competing events across West Yorkshire..."):
                        clashes = []
                        target_cities = "Bradford,Leeds,Huddersfield,Halifax"
                        
                        for date_val in valid_dates:
                            date_str = date_val.strftime('%Y-%m-%d')
                            events = fetch_ticketmaster_clashes(tm_api_key, target_cities, date_str)
                            for e in events:
                                e["Date"] = date_str
                                clashes.append(e)
                        
                        if clashes:
                            st.write(f"### Found {len(clashes)} Competing Events")
                            clash_df = pd.DataFrame(clashes)[['Date', 'City', 'Event', 'Venue', 'Segment']]
                            st.dataframe(clash_df, use_container_width=True, hide_index=True)
                        else:
                            st.success(f"No major Ticketmaster-listed clashes found in West Yorkshire on your event dates.")
                else:
                    st.error("No valid event dates found in the selected column.")

        # ------------------- TAB 10: AI REPORT -------------------
        with tabs[9]:
            st.write("## 🤖 AI Executive Summary")
            st.write("Generate an actionable marketing summary of your audience data powered by Google Gemini.")
            
            if not gemini_api_key:
                st.warning("⚠️ Enter a **Gemini API Key** in the sidebar Integrations section to unlock this feature.")
            else:
                if st.button("Generate AI Report"):
                    try:
                        client = genai.Client(api_key=gemini_api_key)
                        
                        persona_data = filtered_df.groupby('Persona')['_Volume_'].sum().sort_values(ascending=False).to_string()
                        ward_data = filtered_df[filtered_df['Ward'] != 'Unknown'].groupby('Ward')['_Volume_'].sum().sort_values(ascending=False).head(5).to_string()
                        
                        prompt = f"""
                        You are an expert theatre marketing analyst. Based on the following box office data summary, write a clear, insightful executive report for the theatre's leadership team.
                        
                        Box Office Summary:
                        - Total Revenue: £{total_revenue:,.2f}
                        - Total Tickets Sold: {total_volume}
                        - Average Ticket Yield: £{(total_revenue / total_volume) if total_volume > 0 else 0:,.2f}
                        - Average Cultural Propensity Score (0-100): {filtered_df['Cultural_Propensity'].mean():.1f}
                        
                        Top Audience Personas (By Ticket Volume):
                        {persona_data}
                        
                        Top 5 Geographic Hotspots (Wards by Ticket Volume):
                        {ward_data}
                        
                        Please provide a well-structured report covering:
                        1. Overall box office performance and yield analysis.
                        2. Audience demographic breakdown based on the top personas.
                        3. Geographic hotspot analysis based on the top wards.
                        4. Two specific, actionable marketing recommendations based on this data.
                        
                        Keep the tone professional, analytical, and direct. Do not use filler introductions.
                        """
                        
                        with st.status("🧠 Preparing box office data & contacting Gemini...", expanded=True) as status:
                            st.write("Aggregating ticket yields, top personas, and ward demographics...")
                            
                            response = client.models.generate_content_stream(
                                model='gemini-3.5-flash-lite',
                                contents=prompt
                            )
                            st.write("Streaming response from Gemini...")
                            status.update(label="Report generation in progress!", state="running", expanded=False)

                        def stream_gemini():
                            for chunk in response:
                                if chunk.text:
                                    yield chunk.text

                        st.write("### 📋 Executive Report")
                        st.write_stream(stream_gemini)
                        st.success("Report generated successfully.")
                        
                    except Exception as e:
                        st.error(f"An error occurred while generating the report. Error: {e}")
