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
    def __init__(self, env, name, capacity, mean_service_time, cooler_time):
        self.env = env
        self.name = name
        self.capacity = capacity
        self.server = simpy.Resource(env, capacity=capacity)
        self.mean_service_time = mean_service_time
        self.cooler_time = cooler_time

        # Metric Tracking
        self.cooler_times = []          # Mandatory time truck HAS to spend in cooler
        self.queue_times = []           # Time truck waits in cooler AFTER mandatory time because machine is busy
        self.process_times = []         # Time inside the machine
        self.total_busy_time = 0.0
        self.total_lbs_processed = 0.0  # Track yield

        # Physical Inventory Tracking
        self.trucks_in_cooler = 0
        self.cooler_inventory_log = []
        self.env.process(self.monitor_cooler())

    def monitor_cooler(self):
        """Logs the physical number of trucks sitting in the cooler/staging area every minute."""
        while True:
            self.cooler_inventory_log.append(self.trucks_in_cooler)
            yield self.env.timeout(1.0)

    def process_entity(self, entity_name, truck_weight):
        arrival_time = self.env.now
        
        # 1. TRUCK ENTERS COOLER / STAGING
        self.trucks_in_cooler += 1
        
        # 2. MANDATORY COOLER TIME
        yield self.env.timeout(self.cooler_time)
        end_cooler_time = self.env.now
        self.cooler_times.append(end_cooler_time - arrival_time)

        # 3. QUEUE FOR MACHINE (Wait in cooler until a server opens up)
        with self.server.request() as request:
            yield request 
            
            # 4. TRUCK ENTERS MACHINE (Leaves cooler)
            start_service_time = self.env.now
            self.trucks_in_cooler -= 1 
            
            queue_time = start_service_time - end_cooler_time
            self.queue_times.append(queue_time)

            # Process the unit
            service_time = random.expovariate(1.0 / self.mean_service_time)
            yield self.env.timeout(service_time)

            end_service_time = self.env.now
            process_time = end_service_time - start_service_time
            
            self.process_times.append(process_time)
            self.total_busy_time += process_time
            self.total_lbs_processed += truck_weight

    def get_metrics(self, total_sim_time):
        avg_cooler = statistics.mean(self.cooler_times) if self.cooler_times else 0
        avg_queue = statistics.mean(self.queue_times) if self.queue_times else 0
        avg_process = statistics.mean(self.process_times) if self.process_times else 0
        
        avg_trucks_in_cooler = statistics.mean(self.cooler_inventory_log) if self.cooler_inventory_log else 0
        max_trucks_in_cooler = max(self.cooler_inventory_log) if self.cooler_inventory_log else 0
        
        utilization = self.total_busy_time / (self.capacity * total_sim_time) if total_sim_time > 0 else 0
        
        # Total time spent in the physical cooler area (Mandatory + Waiting for machine)
        total_time_in_cooler = avg_cooler + avg_queue

        return {
            "Stage / Machine": self.name,
            "Servers": self.capacity,
            "Utilization (%)": round(utilization * 100, 2),
            "Max Trucks in Cooler": max_trucks_in_cooler,
            "Avg Trucks in Cooler": round(avg_trucks_in_cooler, 1),
            "Mandatory Cooler Time (min)": round(avg_cooler, 2),
            "Bottleneck Wait (min)": round(avg_queue, 2),
            "Total Time in Cooler (min)": round(total_time_in_cooler, 2),
            "Machine Process Time (min)": round(avg_process, 2),
            "Trucks Completed": len(self.process_times),
            "Yield (lbs)": round(self.total_lbs_processed, 2)
        }

def entity_generator(env, arrival_mean, avg_truck_weight, stations):
    entity_count = 0
    while True:
        # Wait for next truck arrival
        yield env.timeout(random.expovariate(1.0 / arrival_mean))
        entity_count += 1
        
        # Generate a truck weight with a slight natural variation (std dev = 5% of mean)
        truck_weight = max(1.0, random.normalvariate(avg_truck_weight, avg_truck_weight * 0.05))
        
        env.process(route_through_line(env, f"Truck-{entity_count}", truck_weight, stations))

def route_through_line(env, name, truck_weight, stations):
    for station in stations:
        yield env.process(station.process_entity(name, truck_weight))

def run_tandem_simulation(sim_time, arrival_mean, avg_truck_weight, station_configs):
    env = simpy.Environment()
    random.seed(42)

    stations_list = []
    for config in station_configs:
        stations_list.append(
            Station(env, config["name"], config["capacity"], config["service_time"], config["cooler_time"])
        )

    env.process(entity_generator(env, arrival_mean, avg_truck_weight, stations_list))
    env.run(until=sim_time)
    
    return pd.DataFrame([station.get_metrics(sim_time) for station in stations_list])

# ==========================================
# INTERACTIVE USER INTERFACE (Streamlit)
# ==========================================
st.set_page_config(page_title="Food Processing Simulation", layout="wide")

st.title("🏭 Food Processing Line Simulator")
st.write("Configure your production flow below. The model tracks yield (lbs), mandatory cooler times, and bottlenecks.")

# Sidebar Controls
st.sidebar.header("1. Global Run Parameters")
sim_time = st.sidebar.number_input("Simulation Run Time (minutes)", min_value=100, value=10000, step=1000)

st.sidebar.divider()
st.sidebar.header("2. Truck Parameters")
arrival_mean = st.sidebar.number_input("Mean Time Between Truck Arrivals (min)", min_value=0.1, value=5.0, step=0.5)
avg_truck_weight = st.sidebar.number_input("Average Truck Weight (lbs)", min_value=50, value=400, step=50)

st.sidebar.divider()
st.sidebar.header("3. Line Configuration")
num_stations = st.sidebar.number_input("Number of Stages", min_value=1, max_value=10, value=6)

default_names = ["Grinder", "Stuffer", "Oven", "Cutter", "Packing Lines", "Box Lines"]
default_servers = [1, 1, 2, 1, 3, 1]
default_service_times = [4.5, 3.2, 35.0, 2.8, 12.5, 4.0]
default_cooler_times = [0.0, 15.0, 0.0, 120.0, 0.0, 0.0] # E.g., chill for 120 mins before cutting

station_configs = []

st.subheader("Stage Parameters")
for i in range(num_stations):
    d_name = default_names[i] if i < len(default_names) else f"Stage {i+1}"
    d_server = default_servers[i] if i < len(default_servers) else 1
    d_time = default_service_times[i] if i < len(default_service_times) else 5.0
    d_cooler = default_cooler_times[i] if i < len(default_cooler_times) else 0.0

    with st.container():
        c1, c2, c3, c4 = st.columns([2, 1, 1.5, 1.5])
        with c1:
            name = st.text_input(f"Stage {i+1} Name", value=d_name, key=f"name_{i}")
        with c2:
            servers = st.number_input("Machines", min_value=1, value=d_server, key=f"srv_{i}")
        with c3:
            cooler_time = st.number_input("Cooler Time (min)", min_value=0.0, value=d_cooler, step=5.0, key=f"cooler_{i}")
        with c4:
            svc_time = st.number_input("Machine Cycle (min)", min_value=0.05, value=d_time, step=0.5, key=f"time_{i}")
            
        station_configs.append({
            "name": name, 
            "capacity": servers, 
            "service_time": svc_time,
            "cooler_time": cooler_time
        })
    st.divider()

if st.button("🚀 Run Production Simulation", type="primary"):
    with st.spinner('Simulating processing line dynamics...'):
        df_results = run_tandem_simulation(sim_time, arrival_mean, avg_truck_weight, station_configs)
        
        st.subheader("📊 Output Performance & Cooler Summary")
        
        # Format the dataframe display to look nicer (adding commas to Yield)
        st.dataframe(
            df_results.style.format({"Yield (lbs)": "{:,.2f}"}), 
            use_container_width=True, 
            hide_index=True
        )

        # Highlight bottlenecks
        overutilized = df_results[df_results["Utilization (%)"] >= 100.0]
        if not overutilized.empty:
            for _, row in overutilized.iterrows():
                st.error(f"⚠️ **{row['Stage / Machine']}** is completely bottlenecked. The upstream cooler area will overflow infinitely because the machine cannot keep up with the trucks coming in.")

        # Excel Export
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_results.to_excel(writer, index=False, sheet_name='Metrics')
            worksheet = writer.sheets['Metrics']
            
            header_format = writer.book.add_format({'bold': True, 'bg_color': '#1F4E78', 'font_color': 'white', 'border': 1, 'text_wrap': True})
            cell_format = writer.book.add_format({'align': 'center', 'border': 1})
            yield_format = writer.book.add_format({'align': 'center', 'border': 1, 'num_format': '#,##0.00'})
            
            for col_num, value in enumerate(df_results.columns.values):
                worksheet.write(0, col_num, value, header_format)
                
            for i, col in enumerate(df_results.columns):
                column_len = max(df_results[col].astype(str).map(len).max(), len(col)) + 2
                if col == "Yield (lbs)":
                    worksheet.set_column(i, i, column_len, yield_format)
                else:
                    worksheet.set_column(i, i, column_len, cell_format)

        st.download_button(
            label="📥 Export Styled Report to Excel",
            data=buffer.getvalue(),
            file_name="food_processing_metrics.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
