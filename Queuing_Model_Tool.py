import simpy
import random
import statistics
import pandas as pd
import streamlit as st
import io

# ==========================================
# SIMULATION BACKEND (SimPy Tandem Line)
# ==========================================
class Station:
    def __init__(self, env, config):
        self.env = env
        self.name = config["name"]
        self.capacity = config["capacity"]
        self.server = simpy.Resource(env, capacity=self.capacity)
        self.mean_service_time = config["service_time"]
        
        # Per-Station Downtime Settings
        self.dt_enabled = config.get("dt_enabled", False)
        self.dt_type = config.get("dt_type", "Assumed Downtime")
        
        # Assumed Scheduled (Matches Photo Format)
        self.dt_interval = config.get("dt_interval", 240.0)
        self.dt_duration = config.get("dt_duration", 30.0)
        
        # Data-Driven Unplanned (CSV Upload)
        self.dt_causes = config.get("dt_causes", [])
        self.dt_weights = config.get("dt_weights", [])
        self.mttr_mapping = config.get("mttr_mapping", {})
        self.breakdown_prob = config.get("breakdown_prob", 0.0)

        # Metric Tracking
        self.queue_times = []
        self.process_times = []
        self.system_times = []
        self.total_busy_time = 0.0
        self.breakdown_log = []

        # Start the background scheduled downtime clock if "Assumed Downtime" is chosen
        if self.dt_enabled and self.dt_type == "Assumed Downtime":
            self.env.process(self.scheduled_downtime_cycle())

    def scheduled_downtime_cycle(self):
        """Runs in the background and periodically locks the station for maintenance/cleaning."""
        while True:
            yield self.env.timeout(self.dt_interval)
            with self.server.request() as request:
                yield request
                yield self.env.timeout(self.dt_duration)

    def process_entity(self, entity_name):
        arrival_time = self.env.now

        with self.server.request() as request:
            yield request  # Wait in line for an available server
            
            start_service_time = self.env.now
            queue_time = start_service_time - arrival_time
            self.queue_times.append(queue_time)

            # Normal Processing Time
            service_time = random.expovariate(1.0 / self.mean_service_time)
            yield self.env.timeout(service_time)
            
            # Unplanned Data-Driven Breakdown Check (If CSV Data was uploaded)
            if self.dt_enabled and self.dt_type == "Downtime Data Upload" and self.breakdown_prob > 0 and self.dt_causes:
                if random.random() < self.breakdown_prob:
                    # Select cause based on CSV occurrences
                    cause = random.choices(self.dt_causes, weights=self.dt_weights, k=1)[0]
                    
                    # Fetch specific calculated MTTR
                    specific_mttr = self.mttr_mapping.get(cause, 10.0)
                    repair_time = random.expovariate(1.0 / specific_mttr)
                    
                    yield self.env.timeout(repair_time)
                    
                    # Log the event
                    self.breakdown_log.append({
                        "Station": self.name,
                        "Cause": cause,
                        "Duration (min)": repair_time
                    })

            end_service_time = self.env.now
            process_time = end_service_time - start_service_time
            
            self.process_times.append(process_time)
            self.system_times.append(queue_time + process_time)
            self.total_busy_time += process_time

    def get_metrics(self, total_sim_time):
        avg_queue = statistics.mean(self.queue_times) if self.queue_times else 0
        avg_process = statistics.mean(self.process_times) if self.process_times else 0
        avg_system = statistics.mean(self.system_times) if self.system_times else 0
        utilization = self.total_busy_time / (self.capacity * total_sim_time) if total_sim_time > 0 else 0

        return {
            "Station Name": self.name,
            "Servers Allocated": self.capacity,
            "Units Processed": len(self.system_times),
            "Utilization (%)": round(utilization * 100, 2),
            "Mean Time Waiting (min)": round(avg_queue, 2),
            "Mean Time in Processing (min)": round(avg_process, 2),
            "Mean Total Time in Production System (min)": round(avg_system, 2)
        }

def route_truck_through_line(env, truck_name, stations, scrap_rate, start_index):
    """Routes an individual truck through the downstream stations."""
    for i in range(start_index, len(stations)):
        yield env.process(stations[i].process_entity(truck_name))
        
        # For individual trucks, we simulate probabilistic whole-truck QA rejection
        if random.random() < scrap_rate:
            break 

def route_blend_through_line(env, blend_name, stations, scrap_rate, split_index, blend_size, truck_weight):
    """Routes the blend through upstream stations, applies yield loss, then splits into trucks."""
    # Start with the full initial batch weight
    remaining_weight = blend_size
    
    # Phase 1: Process the Blend through the early stations (up to and including the split point)
    for i in range(split_index + 1):
        yield env.process(stations[i].process_entity(blend_name))
        
        # Deduct the scrap percentage from the physical weight at this station (continuous loss)
        remaining_weight -= (remaining_weight * scrap_rate)
            
    # Phase 2: The split station has finished. Calculate trucks from the SURVIVING weight!
    if truck_weight > 0:
        # Divide the remaining weight by the target truck weight (rounded down to whole trucks)
        num_trucks = int(remaining_weight // truck_weight) 
    else:
        num_trucks = 1  # Failsafe
        
    # Spawn a new independent process for each generated truck to flow downstream
    for t in range(num_trucks):
        truck_name = f"{blend_name}-Trk{t+1}"
        env.process(route_truck_through_line(env, truck_name, stations, scrap_rate, split_index + 1))

def entity_generator(env, arrival_mean, stations, scrap_rate, split_index, blend_size, truck_weight, target_blends):
    """Injects new Blends into the front of the line until the target is met."""
    blend_count = 0
    while True:
        # Stop spawning if we reached our target blends
        if target_blends is not None and blend_count >= target_blends:
            break
            
        yield env.timeout(random.expovariate(1.0 / arrival_mean))
        blend_count += 1
        env.process(route_blend_through_line(
            env, f"Blend-{blend_count}", stations, scrap_rate, split_index, blend_size, truck_weight
        ))

def run_tandem_simulation(sim_time, arrival_mean, station_configs, scrap_rate, split_index, blend_size, truck_weight, target_blends):
    env = simpy.Environment()
    
    stations_list = [Station(env, config) for config in station_configs]

    # Pass the target_blends limit into the generator
    env.process(entity_generator(env, arrival_mean, stations_list, scrap_rate, split_index, blend_size, truck_weight, target_blends))
    env.run(until=sim_time)

    # Compile data
    results = [station.get_metrics(sim_time) for station in stations_list]
    
    all_breakdowns = []
    for station in stations_list:
        all_breakdowns.extend(station.breakdown_log)
        
    return pd.DataFrame(results), pd.DataFrame(all_breakdowns)

# ==========================================
# INTERACTIVE USER INTERFACE (Streamlit)
# ==========================================
st.set_page_config(page_title="Production Line Simulation", layout="wide")

st.markdown(
    """
    <style>
    :root {
        --app-font-family: Verdana, sans-serif;
        --app-font-size: 16px;
    }
    .stApp {
        background-color: #E02B27;
        font-family: var(--app-font-family);
        font-size: var(--app-font-size);
    }
    [data-testid="stSidebar"] {
        background-color: #000000;
        font-family: var(--app-font-family);
        font-size: calc(var(--app-font-size) - 1px);
    }
    /* Pulls the logo upward */
    [data-testid="column"]:nth-of-type(2) [data-testid="stImage"] {
        margin-top: -60px; 
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------- MAIN BODY (Define Stations First) -----------------

# Create two columns: a wide one for text, and a narrow one for the logo
header_col, logo_col = st.columns([6, 2])

with header_col:
    st.title("Sequential Production Line Queuing Model")
    st.write("Configure a multi-station linear production flow below to evaluate capacity constraints and process metrics.")
    st.write("Accounting for probabilistic food waste and scheduled sanitation/shift downtime.")
    
with st.expander("Model Limitations"):
    st.markdown("*This model assumes a steady state production after setup has been completed. Additionally, this serves as a model of the New Glarus plant and may be able to identify potential bottlenecks but more in depth data and observation is required before implementing actions. For more information about other assumptions or limitations please consult the pass down instructions.*")

with logo_col:
    # Optional: Replace with your actual logo file path if available
    st.image("Jack_Links_Logo.png", width=700)

default_names = ["Grinder", "Stuffer", "Oven", "Cutter", "Packing Lines", "Box Lines"]
default_servers = [1, 1, 2, 1, 3, 1]
default_service_times = [1.5, 1.2, 3.0, 0.8, 4.5, 1.0]

station_configs = []

st.subheader("Station Parameters")
num_stations = st.sidebar.number_input("Number of Stations in Sequence", min_value=1, max_value=10, value=6)

for i in range(num_stations):
    d_name = default_names[i] if i < len(default_names) else f"Station {i+1}"
    d_server = default_servers[i] if i < len(default_servers) else 1
    d_time = default_service_times[i] if i < len(default_service_times) else 2.0

    with st.container():
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            name = st.text_input(f"Station {i+1} Name", value=d_name, key=f"name_{i}")
        with c2:
            servers = st.number_input("Active Servers / Lines", min_value=1, value=d_server, key=f"srv_{i}")
        with c3:
            svc_time = st.number_input("Mean Service Time (min)", min_value=0.05, value=d_time, step=0.1, format="%.2f", key=f"time_{i}")
        
        station_configs.append({"name": name, "capacity": servers, "service_time": svc_time})
    st.divider()

# ----------------- SIDEBAR CONFIGURATION -----------------
st.sidebar.header("1. Global Parameters")
sim_time = st.sidebar.number_input("Simulation Run Time (minutes)", min_value=100, value=10000, step=1000)
arrival_mean = st.sidebar.number_input("Mean Time Between Material Arrivals (min)", min_value=0.1, value=2.0, step=0.1, format="%.2f")

use_target_blends = st.sidebar.checkbox("Enable Target Blends (Daily Quota)", value=False)
target_blends = None
if use_target_blends:
    target_blends = st.sidebar.number_input("Target Blends per Simulation Run", min_value=1, value=20, step=1)
    st.sidebar.caption(f"The simulation will stop generating new material after {target_blends} blends, but will continue processing remaining queues until the run time limit.")

st.sidebar.divider()
st.sidebar.header("2. Quality & Batch Parameters")
scrap_rate_pct = st.sidebar.number_input("Scrap/Waste Rate per Station (%)", min_value=0.0, max_value=100.0, value=5.0, step=1.0)
scrap_rate = scrap_rate_pct / 100.0

blend_size = st.sidebar.number_input("Average Blend Size (lbs)", min_value=100, value=3600, step=100)

# Automatically list the stations the user created, and default to the second one (usually the Stuffer at index 1)
station_names = [cfg["name"] for cfg in station_configs]
split_station_name = st.sidebar.selectbox("Station that Loads the Trucks", options=station_names, index=min(1, len(station_names)-1))
split_index = station_names.index(split_station_name)

st.sidebar.divider()
st.sidebar.header("3. Truck Loading Parameters")

truck_weight_lbs = 0.0 

@st.cache_data
def load_product_data(filepath):
    return pd.read_excel(filepath)

try:
    product_df = load_product_data("MasterData.xlsx")
    
    if 'Pack Number' in product_df.columns and 'Weight Per Truck' in product_df.columns:
        product_list = product_df['Pack Number'].astype(str).dropna().unique().tolist()
        
        selected_product = st.sidebar.selectbox(
            "Select or Search Product Number", 
            options=product_list,
            help="Type to search for a specific Pack Number."
        )
        
        product_row = product_df[product_df['Pack Number'].astype(str) == selected_product].iloc[0]
        pack_description = product_row.get('Pack Description', 'No description available')
        weight_per_truck = product_row['Weight Per Truck']
        
        st.sidebar.write(f"**Item:** {pack_description}")
        
        try:
            weight_val = float(weight_per_truck)
            truck_weight_lbs = weight_val 
            st.sidebar.success(f"**Calculated Target Weight:** {weight_val:,.2f} lbs")
            
            # Calculate the estimated weight remaining after scrap at the upstream stations
            estimated_surviving_weight = blend_size * ((1.0 - scrap_rate) ** (split_index + 1))
            estimated_trucks = int(estimated_surviving_weight // weight_val)
            
            st.sidebar.info(f"**Estimated Yield:** ~{estimated_trucks} trucks per blend (after {scrap_rate*100:.1f}% scrap loss at upstream stations)")
            
        except ValueError:
            st.sidebar.warning(f"**Weight Per Truck:** {weight_per_truck}")
            
    else:
        st.sidebar.error("The file must contain 'Pack Number' and 'Weight Per Truck' columns.")
        
except Exception as e:
    st.sidebar.error(f"Error loading MasterData.xlsx: {e}")

st.sidebar.divider()
st.sidebar.header("4. Per-Station Downtime")

for i, config in enumerate(station_configs):
    with st.sidebar.expander(f"⚙️ Configure {config['name']}", expanded=False):
        dt_enabled = st.checkbox("Simulate Downtime", key=f"dt_en_{i}")
        config["dt_enabled"] = dt_enabled
        
        if dt_enabled:
            dt_type = st.radio("Downtime Source", ["Assumed Downtime", "Downtime Data Upload"], key=f"dt_src_{i}")
            config["dt_type"] = dt_type
            
            if dt_type == "Assumed Downtime":
                st.markdown("**Schedule Settings**")
                config["dt_interval"] = st.number_input("Time Between Scheduled Stops (min)", min_value=10.0, value=240.0, step=30.0, key=f"dt_int_{i}")
                config["dt_duration"] = st.number_input("Duration of Downtime (min)", min_value=1.0, value=30.0, step=5.0, key=f"dt_dur_{i}")
            
            elif dt_type == "Downtime Data Upload":
                uploaded_csv = st.file_uploader(f"Upload Pareto CSV for {config['name']}", type=['csv'], key=f"dt_csv_{i}")
                
                config["dt_causes"], config["dt_weights"], config["mttr_mapping"] = [], [], {}
                config["breakdown_prob"] = 0.0

                if uploaded_csv is not None:
                    try:
                        dt_df = pd.read_csv(uploaded_csv)
                        dt_df.columns = dt_df.columns.str.strip()
                        
                        if 'Downtime Cause' in dt_df.columns and 'Down Time Occurences' in dt_df.columns and 'Total Mins' in dt_df.columns:
                            dt_df = dt_df[dt_df['Down Time Occurences'] > 0]
                            config["dt_causes"] = dt_df['Downtime Cause'].tolist()
                            
                            config["dt_weights"] = (dt_df['Down Time Occurences'] / dt_df['Down Time Occurences'].sum()).tolist()
                            
                            dt_df['Calculated_MTTR'] = dt_df['Total Mins'] / dt_df['Down Time Occurences']
                            config["mttr_mapping"] = dict(zip(dt_df['Downtime Cause'], dt_df['Calculated_MTTR']))
                            
                            st.success(f"Successfully loaded {len(config['dt_causes'])} downtime causes.")
                            
                            prob_pct = st.number_input("Probability of Failure per Cycle (%)", min_value=0.1, max_value=100.0, value=3.0, step=0.1, key=f"dt_prob_{i}")
                            config["breakdown_prob"] = prob_pct / 100.0
                        else:
                            st.error("CSV must contain 'Downtime Cause', 'Total Mins', and 'Down Time Occurences'.")
                    except Exception as e:
                        st.error(f"Error reading CSV: {e}")

# ----------------- EXECUTION & REPORTING -----------------
if st.button("Run Production Simulation", type="primary"):
    with st.spinner('Simulating processing line dynamics...'):
        df_results, df_breakdowns = run_tandem_simulation(
            sim_time, arrival_mean, station_configs, scrap_rate, split_index, blend_size, truck_weight_lbs, target_blends
        )
        
        st.subheader("Output Performance Summary")
        
        # Add a helpful note to explain the shift in units to the user
        st.caption(f"**Note:** 'Units Processed' reflects Blends upstream of the **{split_station_name}**, and individual Trucks downstream.")
        
        st.dataframe(df_results, use_container_width=True, hide_index=True)

        overutilized = df_results[df_results["Utilization (%)"] >= 100.0]
        if not overutilized.empty:
            for _, row in overutilized.iterrows():
                st.error(f"**{row['Station Name']}** is completely bottlenecked (Utilization ≥ 100%). Downstream stations will starve, and upstream queues will grow infinitely.")

        if not df_breakdowns.empty:
            st.subheader("Unplanned Downtime Events Logged")
            
            summary_dt = df_breakdowns.groupby(["Station", "Cause"]).agg(
                Simulated_Occurrences=("Cause", "count"),
                Total_Minutes_Lost=("Duration (min)", "sum")
            ).reset_index().sort_values(by="Total_Minutes_Lost", ascending=False)
            
            summary_dt["Total_Minutes_Lost"] = summary_dt["Total_Minutes_Lost"].round(2)
            
            c1, c2 = st.columns([2, 3])
            with c1:
                st.dataframe(summary_dt, use_container_width=True, hide_index=True)
            with c2:
                st.bar_chart(data=summary_dt, x="Cause", y="Total_Minutes_Lost", color="Station")

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_results.to_excel(writer, index=False, sheet_name='Line Performance Metrics')
            if not df_breakdowns.empty:
                df_breakdowns.to_excel(writer, index=False, sheet_name='Raw Breakdown Log')
                summary_dt.to_excel(writer, index=False, sheet_name='Downtime Summary')
            
            workbook  = writer.book
            worksheet = writer.sheets['Line Performance Metrics']
            header_format = workbook.add_format({'bold': True, 'text_wrap': True, 'valign': 'vcenter', 'align': 'center', 'fg_color': '#111111', 'font_color': 'white', 'border': 1})
            cell_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
            alert_format = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006', 'align': 'center', 'border': 1})

            for col_num, value in enumerate(df_results.columns.values):
                worksheet.write(0, col_num, value, header_format)
            for i, col in enumerate(df_results.columns):
                column_len = max(df_results[col].astype(str).map(len).max(), len(col)) + 4
                worksheet.set_column(i, i, column_len, cell_format)
            worksheet.conditional_format(1, 3, len(df_results), 3, {'type': 'cell', 'criteria': '>=', 'value': 85, 'format': alert_format})

        st.download_button(
            label="Export Styled Report to Excel",
            data=buffer.getvalue(),
            file_name="production_line_metrics.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
