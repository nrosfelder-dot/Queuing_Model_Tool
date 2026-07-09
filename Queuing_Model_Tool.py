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
        self.queue_times = []
        self.process_times = []
        self.system_times = []
        self.total_busy_time = 0.0

    def process_entity(self, entity_name):
        arrival_time = self.env.now

        with self.server.request() as request:
            yield request  # Wait in line for an available server
            
            start_service_time = self.env.now
            queue_time = start_service_time - arrival_time
            self.queue_times.append(queue_time)

            # Model service time as an exponential distribution based on the mean
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

def entity_generator(env, arrival_mean, stations):
    """Injects units into the front of the line (Grinder)"""
    entity_count = 0
    while True:
        yield env.timeout(random.expovariate(1.0 / arrival_mean))
        entity_count += 1
        env.process(route_through_line(env, f"Batch-{entity_count}", stations))

def route_through_line(env, name, stations):
    """Sequentially routes the unit from one station directly to the next"""
    for station in stations:
        yield env.process(station.process_entity(name))

def run_tandem_simulation(sim_time, arrival_mean, station_configs):
    env = simpy.Environment()
    
   
    # Construct the sequential network dynamically from UI inputs
    stations_list = []
    for config in station_configs:
        stations_list.append(
            Station(env, config["name"], config["capacity"], config["service_time"])
        )

    # Start the entry-point generator
    env.process(entity_generator(env, arrival_mean, stations_list))
    env.run(until=sim_time)

    # Compile data
    results = [station.get_metrics(sim_time) for station in stations_list]
    return pd.DataFrame(results)

# ==========================================
# INTERACTIVE USER INTERFACE (Streamlit)
# ==========================================
st.set_page_config(page_title="Tandem Line Simulation", layout="wide")

# Sidebar Controls
st.sidebar.header("1. Global Parameters")
sim_time = st.sidebar.number_input("Simulation Run Time (minutes)", min_value=100, value=10000, step=1000)
arrival_mean = st.sidebar.number_input("Mean Time Between Material Arrivals (minutes)", min_value=0.1, value=2.0, step=0.1, format="%.2f")

st.sidebar.divider()
st.sidebar.header("2. Line Configuration")
num_stations = st.sidebar.number_input("Number of Stations in Sequence", min_value=1, max_value=10, value=6)

st.sidebar.divider()

font_size = st.sidebar.slider("Base Font Size", min_value=12, max_value=24, value=16, step=1)

st.markdown(
    f"""
    <style>
    :root {{
        --app-font-family: Verdana, sans-serif;
        --app-font-size: {font_size}px;
    }}
    .stApp {{
        background-color: #DC143C;
        font-family: var(--app-font-family);
        font-size: var(--app-font-size);
    }}
    [data-testid="stSidebar"] {{
        background-color: #000000;
        font-family: var(--app-font-family);
        font-size: calc(var(--app-font-size) - 1px);
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Sequential Production Line Queuing Model")
st.write("Configure a multi-station linear production flow below to evaluate capacity constraints and process metrics.")
st.write("Using mean times, input data into the individual stations in order to see process statistics")

# Pre-defined defaults matching your physical plant setup
default_names = ["Grinder", "Stuffer", "Oven", "Cutter", "Packing Lines", "Box Lines"]
default_servers = [1, 1, 2, 1, 3, 1]
default_service_times = [1.5, 1.2, 3.0, 0.8, 4.5, 1.0]

station_configs = []

st.subheader("Station Parameters")
st.write("Define server capacities and mean internal processing times for each point along the sequence:")

# Generate layout dynamically using columns
for i in range(num_stations):
    # Fallback to generic naming if user scales beyond the initial 6 default stations
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
        
        station_configs.append({
            "name": name,
            "capacity": servers,
            "service_time": svc_time
        })
    st.divider()

# Execution and Reporting
if st.button("Run Production Simulation", type="primary"):
    with st.spinner('Simulating processing line dynamics...'):
        df_results = run_tandem_simulation(sim_time, arrival_mean, station_configs)
        
        st.subheader("📊 Output Performance Summary")
        st.dataframe(df_results, use_container_width=True, hide_index=True)

        # Highlight severe bottlenecks visually
        overutilized = df_results[df_results["Utilization (%)"] >= 100.0]
        if not overutilized.empty:
            for _, row in overutilized.iterrows():
                st.error(f"**{row['Station Name']}** is completely bottlenecked (Utilization ≥ 100%). Downstream stations will starve, and upstream queues will grow infinitely.")

        # Construct highly-formatted in-memory Excel file
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_results.to_excel(writer, index=False, sheet_name='Line Performance Metrics')
            
            workbook  = writer.book
            worksheet = writer.sheets['Line Performance Metrics']
            
            # Professional formatting layouts
            header_format = workbook.add_format({
                'bold': True, 'text_wrap': True, 'valign': 'vcenter', 'align': 'center',
                'fg_color': '#111111', 'font_color': 'white', 'border': 1
            })
            cell_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
            alert_format = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006', 'align': 'center', 'border': 1})

            # Overwrite header styling
            for col_num, value in enumerate(df_results.columns.values):
                worksheet.write(0, col_num, value, header_format)
                
            # Apply widths and basic alignments
            for i, col in enumerate(df_results.columns):
                column_len = max(df_results[col].astype(str).map(len).max(), len(col)) + 4
                worksheet.set_column(i, i, column_len, cell_format)

            # Conditional Formatting: Highlight any utilization exceeding 85%
            worksheet.conditional_format(1, 3, len(df_results), 3, {
                'type': 'cell', 'criteria': '>=', 'value': 85, 'format': alert_format
            })

        st.download_button(
            label="Export Styled Report to Excel",
            data=buffer.getvalue(),
            file_name="production_line_metrics.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
