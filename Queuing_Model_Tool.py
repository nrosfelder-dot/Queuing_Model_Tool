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
    def __init__(self, env, name, capacity, mean_service_time):
        self.env = env
        self.name = name
        self.capacity = capacity
        self.server = simpy.Resource(env, capacity=capacity)
        self.mean_service_time = mean_service_time

        # Metric Tracking
        self.queue_times = []    # Time spent in storage
        self.process_times = []  # Time spent inside the machine
        self.system_times = []
        self.total_busy_time = 0.0

        # NEW: Track physical storage inventory (WIP) over time
        self.storage_inventory = []
        self.env.process(self.monitor_storage())

    def monitor_storage(self):
        """Background task: Counts units sitting in the upstream storage area every 1 minute."""
        while True:
            self.storage_inventory.append(len(self.server.queue))
            yield self.env.timeout(1.0)

    def process_entity(self, entity_name):
        arrival_time = self.env.now

        with self.server.request() as request:
            yield request  # Wait in storage until a server is available
            
            start_service_time = self.env.now
            queue_time = start_service_time - arrival_time
            self.queue_times.append(queue_time)

            # Process the unit
            service_time = random.expovariate(1.0 / self.mean_service_time)
            yield self.env.timeout(service_time)

            end_service_time = self.env.now
            process_time = end_service_time - start_service_time
            
            self.process_times.append(process_time)
            self.system_times.append(queue_time + process_time)
            self.total_busy_time += process_time

    def get_metrics(self, total_sim_time):
        avg_queue = statistics.mean(self.queue_times) if self.queue_times else 0
        avg_process = statistics.mean(self.process_times) if self.process_times else 0
        avg_wip = statistics.mean(self.storage_inventory) if self.storage_inventory else 0
        max_wip = max(self.storage_inventory) if self.storage_inventory else 0
        
        utilization = self.total_busy_time / (self.capacity * total_sim_time) if total_sim_time > 0 else 0

        return {
            "Station / Machine": self.name,
            "Servers": self.capacity,
            "Utilization (%)": round(utilization * 100, 2),
            "Avg Units in Storage": round(avg_wip, 1),
            "Max Units in Storage": max_wip,
            "Avg Time in Storage (min)": round(avg_queue, 2),
            "Process Time (min)": round(avg_process, 2),
            "Throughput (units)": len(self.system_times)
        }

def entity_generator(env, arrival_mean, stations):
    entity_count = 0
    while True:
        yield env.timeout(random.expovariate(1.0 / arrival_mean))
        entity_count += 1
        env.process(route_through_line(env, f"Batch-{entity_count}", stations))

def route_through_line(env, name, stations):
    for station in stations:
        yield env.process(station.process_entity(name))

def run_tandem_simulation(sim_time, arrival_mean, station_configs):
    env = simpy.Environment()
    random.seed(42)

    stations_list = []
    for config in station_configs:
        stations_list.append(
            Station(env, config["name"], config["capacity"], config["service_time"])
        )

    env.process(entity_generator(env, arrival_mean, stations_list))
    env.run(until=sim_time)
    
    return pd.DataFrame([station.get_metrics(sim_time) for station in stations_list])

# ==========================================
# INTERACTIVE USER INTERFACE (Streamlit)
# ==========================================
st.set_page_config(page_title="Tandem Line Simulation", layout="wide")

st.title("🏭 Sequential Production Line Queuing Model")
st.write("Configure your production flow below. The simulation will calculate how much inventory accumulates in the storage buffers between each machine.")

# Sidebar Controls
st.sidebar.header("1. Global Parameters")
sim_time = st.sidebar.number_input("Simulation Run Time (minutes)", min_value=100, value=10000, step=1000)
arrival_mean = st.sidebar.number_input("Mean Time Spent in Storage (min)", min_value=0.1, value=2.0, step=0.1)

st.sidebar.divider()
st.sidebar.header("2. Line Configuration")
num_stations = st.sidebar.number_input("Number of Stations in Sequence", min_value=1, max_value=10, value=6)

default_names = ["Grinder", "Stuffer", "Oven", "Cutter", "Packing Lines", "Box Lines"]
default_servers = [1, 1, 2, 1, 3, 1]
default_service_times = [1.5, 1.2, 3.0, 0.8, 4.5, 1.0]

station_configs = []

st.subheader("Station Parameters")
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
            svc_time = st.number_input("Mean Service Time (min)", min_value=0.05, value=d_time, step=0.1, key=f"time_{i}")
        
        station_configs.append({"name": name, "capacity": servers, "service_time": svc_time})
    st.divider()

if st.button("🚀 Run Production Simulation", type="primary"):
    with st.spinner('Simulating processing line dynamics...'):
        df_results = run_tandem_simulation(sim_time, arrival_mean, station_configs)
        
        st.subheader("📊 Output Performance & Storage Summary")
        st.dataframe(df_results, use_container_width=True, hide_index=True)

        # Highlight bottlenecks
        overutilized = df_results[df_results["Utilization (%)"] >= 100.0]
        if not overutilized.empty:
            for _, row in overutilized.iterrows():
                st.error(f"⚠️ **{row['Station / Machine']}** is completely bottlenecked. The upstream storage area will overflow infinitely.")

        # Excel Export
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_results.to_excel(writer, index=False, sheet_name='Metrics')
            worksheet = writer.sheets['Metrics']
            
            header_format = writer.book.add_format({'bold': True, 'bg_color': '#1F4E78', 'font_color': 'white', 'border': 1})
            cell_format = writer.book.add_format({'align': 'center', 'border': 1})
            
            for col_num, value in enumerate(df_results.columns.values):
                worksheet.write(0, col_num, value, header_format)
                
            for i, col in enumerate(df_results.columns):
                column_len = max(df_results[col].astype(str).map(len).max(), len(col)) + 4
                worksheet.set_column(i, i, column_len, cell_format)

        st.download_button(
            label="📥 Export Styled Report to Excel",
            data=buffer.getvalue(),
            file_name="production_storage_metrics.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
