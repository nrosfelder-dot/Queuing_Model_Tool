st.sidebar.divider()
st.sidebar.header("4. Truck Loading Parameters")

# We use st.cache_data so the file is only read once and doesn't slow down the simulation
@st.cache_data
def load_product_data(filepath):
    return pd.read_excel(filepath)

try:
    # Automatically load the file from the same directory as the script
    product_df = load_product_data("Exported_Data_20260727.xlsx")
    
    # Verify the necessary columns are present in the Excel file
    if 'Pack Number' in product_df.columns and 'Weight Per Truck' in product_df.columns:
        
        # Extract unique Pack Numbers as strings to populate the searchable dropdown
        product_list = product_df['Pack Number'].astype(str).dropna().unique().tolist()
        
        # Create the searchable dropdown menu
        selected_product = st.sidebar.selectbox(
            "Select or Search Product Number", 
            options=product_list,
            help="Type to search for a specific Pack Number."
        )
        
        # Locate the specific row associated with the chosen product number
        product_row = product_df[product_df['Pack Number'].astype(str) == selected_product].iloc[0]
        
        # Extract the Description and Weight Per Truck
        pack_description = product_row.get('Pack Description', 'No description available')
        weight_per_truck = product_row['Weight Per Truck']
        
        # Display the product description for visual confirmation
        st.sidebar.write(f"**Item:** {pack_description}")
        
        # Some rows in your sheet say "No matching records found.", so we must handle non-numeric text
        try:
            # Attempt to convert the weight to a float for calculation purposes
            weight_val = float(weight_per_truck)
            st.sidebar.success(f"**Calculated Weight Per Truck:** {weight_val:,.2f} lbs")
            
            # --- ADD ANY ADDITIONAL TRUCK MATH HERE ---
            # Example: config["truck_weight"] = weight_val
            
        except ValueError:
            # If the cell contains text (like "No matching records found."), warn the user
            st.sidebar.warning(f"**Weight Per Truck:** {weight_per_truck}")
            
    else:
        st.sidebar.error("The file must contain 'Pack Number' and 'Weight Per Truck' columns.")
        
except Exception as e:
    # Failsafe in case the file is missing from the folder or corrupted
    st.sidebar.error(f"Error loading Exported_Data_20260727.xlsx: {e}")
