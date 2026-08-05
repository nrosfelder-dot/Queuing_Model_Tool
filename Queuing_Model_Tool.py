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
        
        # Assumed Scheduled
        self.dt_interval = config.get("dt_interval", 240.0)
        self.dt_duration = config.get("dt_duration", 30.0)
        
        # Data-Driven Unplanned
        self.dt_causes = config.get("dt_causes", [])
        self.dt_weights = config.get("dt_weights", [])
        self.mttr_mapping = config.get("mttr_mapping", {})
        self.breakdown_prob = config.get("breakdown_prob", 0.0)

        # Metric Tracking
        self.queue_times = []
        self.process_times = []
        self.system_times = []
        self.total_busy_time = 0.0
        self.total_weight_processed = 0.0 
        self.breakdown_log = []

        if self.dt_enabled and self.dt_type == "Assumed Downtime":
            self.env.process(self.scheduled_downtime_cycle())

    def scheduled_downtime_cycle(self):
        """Runs in the background and periodically locks the station for maintenance/cleaning."""
        while True:
            yield self.env.timeout(self.dt_interval)
            with self.server.request() as request:
                yield request
                yield self.env.timeout(self.dt_duration)

    def process_entity(self, entity_name, weight=0.0):
        arrival_time = self.env.now

        with self.server.request() as request:
            yield request 
            
            start_service_time = self.env.now
            queue_time = start_service_time - arrival_time
            self.queue_times.append(queue_time)
            
            self.total_weight_processed += weight

            # Normal Processing Time
            service_time = random.expovariate(1.0 / self.mean_service_time)
            yield self.env.timeout(service_time)
            
            # Unplanned Breakdown Check
            if self.dt_enabled and self.dt_type == "Downtime Data Upload" and self.breakdown_prob > 0 and self.dt_causes:
                if random.random() < self.breakdown_prob:
                    cause = random.choices(self.dt_causes, weights=self.dt_weights, k=1)[0]
                    specific_mttr = self.mttr_mapping.get(cause, 10.0)
                    repair_time = random.expovariate(1.0 / specific_mttr)
                    
                    yield self.env.timeout(repair_time)
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

    def get_metrics(self, total_sim_time, split_index, cut_index, current_index, sticks_per_truck):
        avg_queue = statistics.mean(self.queue_times) if self.queue_times else 0
        avg_process = statistics.mean(self.process_times) if self.process_times else 0
        avg_system = statistics.mean(self.system_times) if self.system_times else 0
        utilization = self.total_busy_time / (self.capacity * total_sim_time) if total_sim_time > 0 else 0

        # DETERMINE UNIT TYPE USING THE OPTIMIZED MULTIPLIER
        if current_index <= split_index:
            unit_type = "Blends"
            reported_units = len(self.system_times)
        elif current_index <= cut_index:
            unit_type = "Trucks"
            reported_units = len(self.system_times)
        else:
            unit_type = "Sections/Sticks"
            # Multiplier logic: We simulated 1 truck, but report the mathematical stick count
            reported_units = len(self.system_times) * sticks_per_truck

        return {
            "Station Name": self.name,
            "Unit Type": unit_type,
            "Servers": self.capacity,
            "Units Processed": reported_units,
            "Total Pounds": round(self.total_weight_processed, 2),
            "Utilization (%)": round(utilization * 100, 2),
            "Mean Wait (min)": round(avg_queue, 2),
            "Mean Process (min)": round(avg_process, 2),
            "Total Time (min)": round(avg_system, 2)
        }

def route_truck_through_line(env, truck_name, stations, scrap_rate, start_index, cook_index, cook_yield, kitchen_weight):
    """Routes a truck through remaining stations, reducing weight after the oven."""
    current_weight = kitchen_weight
    
    for i in range(start_index, len(stations)):
        yield env.process(stations[i].process_entity(truck_name, weight=current_weight))
        
        # Shrink the weight for all subsequent downstream stations
        if i == cook_index:
            current_weight = current_weight * cook_yield
            
        # Whole-truck probabilistic scrap rejection
        if random.random() < scrap_rate:
            break 

def route_blend_through_line(env, blend_name, stations, scrap_rate, split_index, cook_index, cook_yield, blend_size, kitchen_weight):
    """Routes the blend through upstream stations, applies yield loss, then splits into trucks."""
    remaining_weight = blend_size
    
    # Phase 1: Blend Processing
    for i in range(split_index + 1):
        yield env.process(stations[i].process_entity(blend_name, weight=remaining_weight))
        remaining_weight -= (remaining_weight * scrap_rate)
            
    # Phase 2: Split into Trucks
    num_trucks = int(remaining_weight // kitchen_weight) if kitchen_weight > 0 else 1 
        
    for t in range(num_trucks):
        truck_name = f"{blend_name}-Trk{t+1}"
        env.process(route_truck_through_line(env, truck_name, stations, scrap_rate, split_index + 1, cook_index, cook_yield, kitchen_weight))

def entity_generator(env, arrival_mean, stations, scrap_rate, split_index, cook_index, cook_yield, blend_size, kitchen_weight, target_blends, sim_mode, generator_status):
    """Injects new Blends based on the selected simulation mode."""
    blend_count = 0
    while True:
        if target_blends is not None and blend_count >= target_blends:
            generator_status["is_done"] = True
            break
            
        # Mode 1: Max Throughput
        if "1. Theoretical" in sim_mode:
            # Constantly feed line, but throttle to prevent memory crash (WIP Cap)
            if len(stations[0].server.queue) < (stations[0].capacity * 2):
                blend_count += 1
                env.process(route_blend_through_line(env, f"Blend-{blend_count}", stations, scrap_rate, split_index, cook_index, cook_yield, blend_size, kitchen_weight))
            yield env.timeout(1.0)
            continue
            
        # Modes 2 & 3: Standard inter-arrival
        yield env.timeout(random.expovariate(1.0 / arrival_mean))
        blend_count += 1
        env.process(route_blend_through_line(env, f"Blend-{blend_count}", stations, scrap_rate, split_index, cook_index, cook_yield, blend_size, kitchen_weight))

def system_empty_monitor(env, stations, generator_status, done_event):
    """Polls the system to see if all units have completely finished processing (For Mode 2)."""
    while True:
        yield env.timeout(1.0)
        if generator_status["is_done"]:
            all_empty = all(len(s.server.queue) == 0 and s.server.count == 0 for s in stations)
            if all_empty:
                done_event.succeed()
                break

def run_tandem_simulation(sim_time, arrival_mean, station_configs, scrap_rate, split_index, cut_index, cook_index, cook_yield, blend_size, kitchen_weight, sticks_per_truck, target_blends, sim_mode):
    env = simpy.Environment()
    stations_list = [Station(env, config) for config in station_configs]

    generator_status = {"is_done": False}
    done_event = env.event()

    env.process(entity_generator(
        env, arrival_mean, stations_list, scrap_rate, split_index, cook_index, cook_yield,
        blend_size, kitchen_weight, target_blends, sim_mode, generator_status
    ))
    
    if "2. Time to Process" in sim_mode:
        env.process(system_empty_monitor(env, stations_list, generator_status, done_event))
        env.run(until=done_event)
        actual_sim_time = env.now
    else:
        env.run(until=sim_time)
        actual_sim_time = sim_time

    # Compile data, passing indices for the Multiplier logic
    results = [station.get_metrics(actual_sim_time, split_index, cut_index, i, sticks_per_truck) for i, station in enumerate(stations_list)]
    
    all_breakdowns = []
    for station in stations_list:
        all_breakdowns.extend(station.breakdown_log)
        
    return pd.DataFrame(results), pd.DataFrame(all_breakdowns), actual_sim_time

# ==========================================
# INTERACTIVE USER INTERFACE (Streamlit)
# ==========================================
st.set_page_config(page_title="Production Line Simulation", layout="wide")

st.markdown(
    """
    <style>
    :root { --app-font-family: Verdana, sans-serif; --app-font-size: 16px; }
    .stApp { background-color: #E02B27; font-family: var(--app-font-family); font-size: var(--app-font-size); }
    [data-testid="stSidebar"] { background-color: #000000; font-family: var(--app-font-family); font-size: calc(var(--app-font-size) - 1px); }
    [data-testid="column"]:nth-of-type(2) [data-testid="stImage"] { margin-top: -60px; }
    </style>
    """, unsafe_allow_html=True
)

header_col, logo_col = st.columns([6, 2])

with header_col:
    st.title("Sequential Production Line Queuing Model")
    st.write("Configure a multi-station linear production flow below to evaluate capacity constraints and process metrics.")
    
with st.expander("Model Limitations"):
    st.markdown("*This model assumes a steady state production after setup has been completed. Valid for identifying bottlenecks, but verify with floor data before physical implementation.*")

with logo_col:
    st.image("Jack_Links_Logo.png", width=700) # Fallback to missing image icon if missing

st.divider()
st.subheader("Simulation Objective")
sim_mode = st.radio(
    "Select what you want to calculate:",
    options=[
        "1. Theoretical Max Throughput",
        "2. Time to Process Target Blends",
        "3. Standard Fixed-Time Model (Current Version)"
    ],
    captions=[
        "Identifies absolute system constraints by keeping the first station 100% fed with infinite material.",
        "Calculates the total time (in minutes) required to clear a specific daily quota.",
        "Runs for a set duration with a steady arrival cadence to measure standard performance."
    ]
)
st.divider()

default_names = ["Grinder", "Stuffer", "Oven", "Cutter", "Packing Lines", "Box Lines"]
default_servers = [1, 5, 14, 1, 3, 3]
default_service_times = [45, 60, 600, 60, 60, 60]

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
            svc_time = st.number_input("Mean Service Time (min)", min_value=0.05, value=float(d_time), step=0.1, format="%.2f", key=f"time_{i}")
        
        station_configs.append({"name": name, "capacity": servers, "service_time": svc_time})
    st.divider()

# ----------------- SIDEBAR CONFIGURATION -----------------
st.sidebar.header("1. Global Parameters")

if "1. Theoretical" in sim_mode:
    st.sidebar.info("Max Throughput Mode enabled. The system will run for 10,000 minutes with a constant raw material supply.")
    sim_time = 10000
    arrival_mean = 0.01
    use_target_blends = False
    target_blends = None

elif "2. Time to Process" in sim_mode:
    target_blends = st.sidebar.number_input("Target Blends to Process", min_value=1, value=20, step=1)
    arrival_mean = st.sidebar.number_input("Mean Time Between Material Arrivals (min)", min_value=0.1, value=2.0, step=0.1)
    st.sidebar.info("Simulation clock stops when the final package clears the line.")
    sim_time = 999999 
    use_target_blends = True

else:
    sim_time = st.sidebar.number_input("Simulation Run Time (minutes)", min_value=100, value=10000, step=1000)
    arrival_mean = st.sidebar.number_input("Mean Time Between Material Arrivals (min)", min_value=0.1, value=2.0, step=0.1)
    use_target_blends = st.sidebar.checkbox("Enable Target Blends (Daily Quota)", value=False)
    target_blends = st.sidebar.number_input("Target Blends per Run", min_value=1, value=20, step=1) if use_target_blends else None

st.sidebar.divider()
st.sidebar.header("2. Quality & Batch Parameters")
scrap_rate_pct = st.sidebar.number_input("Scrap/Waste Rate per Station (%)", min_value=0.0, max_value=100.0, value=5.0, step=1.0)
scrap_rate = scrap_rate_pct / 100.0
blend_size = st.sidebar.number_input("Average Blend Size (lbs)", min_value=100, value=3600, step=100)

st.sidebar.divider()
st.sidebar.header("3. Master Data (Weights & Specs)")

kitchen_weight_lbs = 500.0 
cook_weight_lbs = 400.0
sticks_per_truck = 100  

@st.cache_data
def load_product_data(filepath):
    return pd.read_excel(filepath)

try:
    product_df = load_product_data("MasterData.xlsx")
    
    # --- UPDATED TO USE 'Pack Number' INSTEAD OF 'Blend Number' ---
    if 'Pack Number' in product_df.columns:
        product_list = product_df['Pack Number'].astype(str).dropna().unique().tolist()
        selected_product = st.sidebar.selectbox("Select or Search Pack Number", options=product_list)
        product_row = product_df[product_df['Pack Number'].astype(str) == selected_product].iloc[0]
        
        pack_description = product_row.get('Pack Description', 'No description available')
        st.sidebar.write(f"**Item:** {pack_description}")
        
        # Attempt to pull Kitchen/Cook weights if they exist, otherwise fallback to generic 'Weight Per Truck'
        if 'Kitchen Weight' in product_df.columns and 'Cook Weight' in product_df.columns:
            kitchen_weight_lbs = float(product_row['Kitchen Weight'])
            cook_weight_lbs = float(product_row['Cook Weight'])
            st.sidebar.success(f"**Loaded Weights:** Kitchen {kitchen_weight_lbs} lbs | Cook {cook_weight_lbs} lbs")
        elif 'Weight Per Truck' in product_df.columns:
            kitchen_weight_lbs = float(product_row['Weight Per Truck'])
            st.sidebar.info("Found general 'Weight Per Truck'. Proceeding with manual input for cooked yield loss.")
            kitchen_weight_lbs = st.sidebar.number_input("Kitchen Truck Weight (lbs entering Oven)", value=kitchen_weight_lbs)
            cook_weight_lbs = st.sidebar.number_input("Cooked Truck Weight (lbs exiting Oven)", value=kitchen_weight_lbs * 0.8)
        
        # Cut Specs Check
        if 'Sticks Per Truck' in product_df.columns:
            sticks_val = product_row.get('Sticks Per Truck')
            if pd.notna(sticks_val):
                sticks_per_truck = int(sticks_val)
                st.sidebar.success(f"**Cut Spec:** {sticks_per_truck} sections/sticks per truck.")
            else:
                sticks_per_truck = st.sidebar.number_input("Manual Input: Sections/Sticks per Truck", min_value=1, value=120, step=10)
        else:
            sticks_per_truck = st.sidebar.number_input("Manual Input: Sections/Sticks per Truck", min_value=1, value=120, step=10)
            
    else:
        st.sidebar.error("MasterData.xlsx missing required columns ('Pack Number').")
except Exception as e:
    st.sidebar.error("Could not load MasterData.xlsx. Proceeding with manual inputs.")
    kitchen_weight_lbs = st.sidebar.number_input("Kitchen Truck Weight (lbs entering Oven)", value=500.0)
    cook_weight_lbs = st.sidebar.number_input("Cooked Truck Weight (lbs exiting Oven)", value=400.0)
    sticks_per_truck = st.sidebar.number_input("Manual Input: Sections/Sticks per Truck", min_value=1, value=120, step=10)

# Calculate dynamic yield based on weights
cook_yield = cook_weight_lbs / kitchen_weight_lbs if kitchen_weight_lbs > 0 else 1.0
st.sidebar.caption(f"*(Calculated Oven Yield: {cook_yield * 100:.1f}%)*")

st.sidebar.divider()
st.sidebar.header("4. Station Mapping")
station_names = [cfg["name"] for cfg in station_configs]

split_station_name = st.sidebar.selectbox("Station that Loads the Trucks (Split 1)", options=station_names, index=min(1, len(station_names)-1))
split_index = station_names.index(split_station_name)

cook_station_name = st.sidebar.selectbox("Cooking Station (Weight Reduction)", options=station_names, index=min(2, len(station_names)-1))
cook_index = station_names.index(cook_station_name)

cut_station_name = st.sidebar.selectbox("Station that Cuts Trucks into Sections (Split 2)", options=station_names, index=min(3, len(station_names)-1))
cut_index = station_names.index(cut_station_name)


st.sidebar.divider()
st.sidebar.header("5. Per-Station Downtime")
for i, config in enumerate(station_configs):
    with st.sidebar.expander(f"⚙️ Configure {config['name']}", expanded=False):
        dt_enabled = st.checkbox("Simulate Downtime", key=f"dt_en_{i}")
        config["dt_enabled"] = dt_enabled
        if dt_enabled:
            dt_type = st.radio("Downtime Source", ["Assumed Downtime", "Downtime Data Upload"], key=f"dt_src_{i}")
            config["dt_type"] = dt_type
            
            if dt_type == "Assumed Downtime":
                config["dt_interval"] = st.number_input("Time Between Stops (min)", value=240.0, key=f"dt_int_{i}")
                config["dt_duration"] = st.number_input("Duration of Downtime (min)", value=30.0, key=f"dt_dur_{i}")
            elif dt_type == "Downtime Data Upload":
                uploaded_csv = st.file_uploader(f"Upload Pareto CSV", type=['csv'], key=f"dt_csv_{i}")

# ----------------- EXECUTION & REPORTING -----------------
if st.button("Run Production Simulation", type="primary"):
    with st.spinner('Simulating processing line dynamics...'):
        
        df_results, df_breakdowns, actual_time = run_tandem_simulation(
            sim_time, arrival_mean, station_configs, scrap_rate, split_index, cut_index, cook_index, cook_yield,
            blend_size, kitchen_weight_lbs, sticks_per_truck, target_blends, sim_mode
        )
        
        st.subheader("Simulation Results")
        
        # Dynamic KPI based on Mode
        if "1. Theoretical" in sim_mode:
            final_station_row = df_results.iloc[-1]
            st.success(f"**Max Theoretical Throughput Found:** {final_station_row['Total Pounds']:,.2f} lbs ({final_station_row['Units Processed']:,} {final_station_row['Unit Type']}) finished per 10,000 minutes.")
        elif "2. Time to Process" in sim_mode:
            st.success(f"**Target Reached:** It took **{actual_time:,.2f} minutes** (~{actual_time/60:,.2f} hours) to completely process all {target_blends} blends.")
        else:
            st.success(f"**Standard Simulation Complete:** Ran for {actual_time} minutes.")
            
        st.dataframe(df_results, use_container_width=True, hide_index=True)

        overutilized = df_results[df_results["Utilization (%)"] >= 100.0]
        if not overutilized.empty:
            for _, row in overutilized.iterrows():
                st.error(f"{row['Station Name']}** is completely bottlenecked (Utilization ≥ 100%).")

        if not df_breakdowns.empty:
            st.subheader("Unplanned Downtime Events Logged")
            summary_dt = df_breakdowns.groupby(["Station", "Cause"]).agg(
                Simulated_Occurrences=("Cause", "count"), Total_Minutes_Lost=("Duration (min)", "sum")
            ).reset_index().sort_values(by="Total_Minutes_Lost", ascending=False)
            
            c1, c2 = st.columns([2, 3])
            with c1: st.dataframe(summary_dt, use_container_width=True, hide_index=True)
            with c2: st.bar_chart(data=summary_dt, x="Cause", y="Total_Minutes_Lost", color="Station")

        # Excel Export
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_results.to_excel(writer, index=False, sheet_name='Line Performance')
            workbook  = writer.book
            worksheet = writer.sheets['Line Performance']
            alert_format = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006', 'align': 'center', 'border': 1})
            
            # Find the column index for Utilization dynamically to prevent formatting errors
            util_col_idx = df_results.columns.get_loc("Utilization (%)")
            worksheet.conditional_format(1, util_col_idx, len(df_results), util_col_idx, 
                                         {'type': 'cell', 'criteria': '>=', 'value': 85, 'format': alert_format})

        st.download_button(label="Export Styled Report to Excel", data=buffer.getvalue(), 
                           file_name="production_metrics.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
