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
            "Mean Time in Queue (min)": round(avg_queue, 2),
            "Mean Time in Process (min)": round(avg_process, 2),
            "Mean Total Time in System (min)": round(avg_system, 2)
        }

def route_through_line(env, name, stations, scrap_rate):
    """Sequentially routes the unit, applying a waste probability at each stage."""
    for station in stations:
        yield env.process(station.process_entity(name))
        if random.random() < scrap_rate:
            break 

def entity_generator(env, arrival_mean, stations, scrap_rate):
    """Injects units into the front of the line."""
    entity_count = 0
    while True:
        yield env.timeout(random.expovariate(1.0 / arrival_mean))
        entity_count += 1
        env.process(route_through_line(env, f"Batch-{entity_count}", stations, scrap_rate))

def run_tandem_simulation(sim_time, arrival_mean, station_configs, scrap_rate):
    env = simpy.Environment()
    
    stations_list = [Station(env, config) for config in station_configs]

    env.process(entity_generator(env, arrival_mean, stations_list, scrap_rate))
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
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------- MAIN BODY (Define Stations First) -----------------
st.title("Sequential Production Line Queuing Model")
st.write("Configure a multi-station linear production flow below to evaluate capacity constraints and process metrics.")
    
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
        
        # Build the baseline config. We will append the downtime settings in the sidebar loop.
        station_configs.append({"name": name, "capacity": servers, "service_time": svc_time})
    st.divider()

# ----------------- SIDEBAR CONFIGURATION -----------------
st.sidebar.header("1. Global Parameters")
sim_time = st.sidebar.number_input("Simulation Run Time (minutes)", min_value=100, value=10000, step=1000)
arrival_mean = st.sidebar.number_input("Mean Time Between Material Arrivals (min)", min_value=0.1, value=2.0, step=0.1, format="%.2f")

st.sidebar.divider()
st.sidebar.header("2. Quality Parameters")
scrap_rate_pct = st.sidebar.number_input("Scrap/Waste Rate per Station (%)", min_value=0.0, max_value=100.0, value=5.0, step=1.0)
scrap_rate = scrap_rate_pct / 100.0

st.sidebar.divider()
st.sidebar.header("3. Per-Station Downtime")

# Dynamically generate downtime controls for each station defined in the main window
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
                
                # Default empty states
                config["dt_causes"], config["dt_weights"], config["mttr_mapping"] = [], [], {}
                config["breakdown_prob"] = 0.0

                if uploaded_csv is not None:
                    try:
                        dt_df = pd.read_csv(uploaded_csv)
                        dt_df.columns = dt_df.columns.str.strip()
                        
                        if 'Downtime Cause' in dt_df.columns and 'Down Time Occurences' in dt_df.columns and 'Total Mins' in dt_df.columns:
                            dt_df = dt_
