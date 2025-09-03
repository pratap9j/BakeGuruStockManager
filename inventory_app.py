import streamlit as st
import pandas as pd
import sqlite3
from PIL import Image
import io
import hashlib
from fpdf import FPDF

# --- Database Setup ---
conn = sqlite3.connect("inventory.db")
c = conn.cursor()

# Create products table
c.execute('''CREATE TABLE IF NOT EXISTS products
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              sku TEXT,
              name TEXT,
              variation TEXT,
              category TEXT,
              subcategory TEXT,
              list_price REAL,
              wholesale_price REAL,
              retail_price REAL,
              qty INTEGER,
              image BLOB)''')

# Create users table
c.execute('''CREATE TABLE IF NOT EXISTS users
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT UNIQUE,
              password TEXT,
              role TEXT)''')
conn.commit()

# --- Helper Functions ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_user(username, password):
    c.execute("SELECT * FROM users WHERE username=? AND password=?", 
              (username, hash_password(password)))
    return c.fetchone()

def create_user(username, password, role):
    try:
        c.execute("INSERT INTO users (username, password, role) VALUES (?,?,?)", 
                  (username, hash_password(password), role))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def reset_password(username, new_password):
    c.execute("UPDATE users SET password=? WHERE username=?", 
              (hash_password(new_password), username))
    conn.commit()

def load_products():
    return pd.read_sql_query("SELECT * FROM products", conn)

def add_product(sku, name, variation, category, subcategory,
                list_price, wholesale_price, retail_price, qty, image):
    c.execute("""INSERT INTO products
                 (sku, name, variation, category, subcategory,
                  list_price, wholesale_price, retail_price, qty, image)
                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
              (sku, name, variation, category, subcategory,
               list_price, wholesale_price, retail_price, qty, image))
    conn.commit()

def update_product(product_id, field, value):
    c.execute(f"UPDATE products SET {field}=? WHERE id=?", (value, product_id))
    conn.commit()

def export_excel(df, filename="inventory.xlsx"):
    df.to_excel(filename, index=False)
    return filename

def export_quote_pdf(products, filename="quote.pdf"):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, "Quotation", ln=True, align="C")
    pdf.ln(10)

    # Table header
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(30, 10, "SKU", 1)
    pdf.cell(60, 10, "Product Name", 1)
    pdf.cell(20, 10, "Qty", 1)
    pdf.cell(30, 10, "Price", 1)
    pdf.ln()

    pdf.set_font("Arial", size=9)
    for _, row in products.iterrows():
        pdf.cell(30, 10, str(row["sku"]), 1)
        pdf.cell(60, 10, str(row["name"]), 1)
        pdf.cell(20, 10, str(row["qty"]), 1)
        pdf.cell(30, 10, str(row["retail_price"]), 1)
        pdf.ln()

    pdf.ln(10)
    pdf.set_font("Arial", 'I', 9)
    pdf.cell(200, 10, "Disclaimer: GST & Shipping Extra", ln=True, align="L")

    pdf.output(filename)
    return filename

# --- Initialize Default Admin ---
c.execute("SELECT * FROM users")
if not c.fetchone():  # create default admin if no users
    create_user("admin", "admin123", "Admin")

# --- Streamlit App ---
st.set_page_config(page_title="BakeGuru Stock Manager", layout="wide")

# --- Session State ---
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.role = None
    st.session_state.username = None

# --- Login Page ---
if not st.session_state.logged_in:
    st.title("🔑 BakeGuru Stock Manager Login")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        user = verify_user(username, password)
        if user:
            st.session_state.logged_in = True
            st.session_state.username = user[1]
            st.session_state.role = user[3]
            st.success(f"✅ Welcome {st.session_state.username} ({st.session_state.role})")
            st.rerun()
        else:
            st.error("❌ Invalid username or password")

    st.info("Default login → Username: **admin** | Password: **admin123**")

# --- Main App ---
else:
    st.sidebar.write(f"👤 Logged in as **{st.session_state.username} ({st.session_state.role})**")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.session_state.role = None
        st.experimental_rerun()

    if st.sidebar.button("Reset My Password"):
        new_pw = st.sidebar.text_input("Enter new password", type="password")
        if st.sidebar.button("Confirm Reset"):
            reset_password(st.session_state.username, new_pw)
            st.success("✅ Password updated")

    menu = ["Dashboard", "View Stock"]
    if st.session_state.role in ["Admin", "Staff"]:
        menu.append("Add Product")
    if st.session_state.role == "Admin":
        menu.append("User Management")
    choice = st.sidebar.selectbox("Menu", menu)

    # --- Dashboard ---
    if choice == "Dashboard":
        df = load_products()
        total_products = len(df)
        total_qty = df["qty"].sum() if not df.empty else 0
        low_stock = df[df["qty"] < 5]

        st.metric("Total Products", total_products)
        st.metric("Total Stock Qty", total_qty)
        if not low_stock.empty:
            st.warning(f"⚠️ {len(low_stock)} products are low in stock!")
            st.table(low_stock[["sku", "name", "qty"]])

    # --- View Stock ---
    elif choice == "View Stock":
        df = load_products()
        if not df.empty:
            st.subheader("📋 Inventory List")

            # Search/filter
            category_filter = st.selectbox("Filter by Category", ["All"] + sorted(df["category"].dropna().unique().tolist()))
            search_text = st.text_input("Search by SKU or Name")

            if category_filter != "All":
                df = df[df["category"] == category_filter]
            if search_text:
                df = df[df.apply(lambda row: search_text.lower() in str(row["sku"]).lower() or search_text.lower() in str(row["name"]).lower(), axis=1)]

            st.dataframe(df[["sku", "name", "variation", "category", "subcategory", "retail_price", "qty"]])

            # Export inventory
            if st.button("Export Inventory to Excel"):
                file = export_excel(df, "inventory.xlsx")
                with open(file, "rb") as f:
                    st.download_button("⬇️ Download Excel", f, file_name="inventory.xlsx")

            if st.button("Export Inventory to PDF"):
                file = export_quote_pdf(df, "inventory.pdf")
                with open(file, "rb") as f:
                    st.download_button("⬇️ Download PDF", f, file_name="inventory.pdf")

            # Quote builder
            st.subheader("📝 Build Quote")
            selected_ids = st.multiselect("Select Products", df["id"].tolist(),
                                          format_func=lambda x: f"{df.loc[df['id']==x,'name'].values[0]}")

            if selected_ids:
                selected_products = df[df["id"].isin(selected_ids)].copy()
                for i, row in selected_products.iterrows():
                    col1, col2 = st.columns(2)
                    selected_products.loc[i, "qty"] = col1.number_input(f"Qty for {row['name']}", value=row["qty"])
                    selected_products.loc[i, "retail_price"] = col2.number_input(f"Price for {row['name']}", value=row["retail_price"])

                if st.button("Export Quote to Excel"):
                    file = export_excel(selected_products, "quote.xlsx")
                    with open(file, "rb") as f:
                        st.download_button("⬇️ Download Quote (Excel)", f, file_name="quote.xlsx")

                if st.button("Export Quote to PDF"):
                    file = export_quote_pdf(selected_products, "quote.pdf")
                    with open(file, "rb") as f:
                        st.download_button("⬇️ Download Quote (PDF)", f, file_name="quote.pdf")

                # WhatsApp link
                customer_number = st.text_input("Enter Customer WhatsApp Number (with country code)", "91")
                if st.button("Generate WhatsApp Link"):
                    message = "Hello, please find your quotation attached. GST & Shipping Extra."
                    whatsapp_url = f"https://wa.me/{customer_number}?text={message.replace(' ', '%20')}"
                    st.markdown(f"[📲 Send Quote via WhatsApp]({whatsapp_url})", unsafe_allow_html=True)

        else:
            st.info("No products added yet.")

    # --- Add Product ---
    elif choice == "Add Product":
        st.subheader("➕ Add a New Product")
        sku = st.text_input("Product SKU")
        name = st.text_input("Product Name")
        variation = st.text_input("Variation")
        category = st.text_input("Category")
        subcategory = st.text_input("Sub Category")
        list_price = st.number_input("List Price", 0.0)
        wholesale_price = st.number_input("Wholesale Price", 0.0)
        retail_price = st.number_input("Retail Price", 0.0)
        qty = st.number_input("Quantity", 0)
        image_file = st.file_uploader("Upload Image", type=["jpg", "png"])

        if st.button("Add Product"):
            image_data = None
            if image_file is not None:
                buf = io.BytesIO()
                Image.open(image_file).save(buf, format="PNG")
                image_data = buf.getvalue()
            add_product(sku, name, variation, category, subcategory,
                        list_price, wholesale_price, retail_price, qty, image_data)
            st.success(f"✅ {name} added!")

    # --- User Management ---
    elif choice == "User Management" and st.session_state.role == "Admin":
        st.subheader("👥 Manage Users")
        new_username = st.text_input("New Username")
        new_password = st.text_input("New Password", type="password")
        new_role = st.selectbox("Role", ["Admin", "Staff", "Viewer"])

        if st.button("Create User"):
            if create_user(new_username, new_password, new_role):
                st.success(f"✅ User {new_username} created with role {new_role}")
            else:
                st.error("❌ Username already exists")

        # Reset any user password
        st.subheader("🔑 Reset User Password")
        reset_user = st.text_input("Username to reset")
        reset_pw = st.text_input("New Password", type="password")
        if st.button("Reset Password"):
            reset_password(reset_user, reset_pw)
            st.success(f"✅ Password reset for {reset_user}")
