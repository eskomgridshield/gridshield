from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session
import pandas as pd
from datetime import datetime
import os
import psycopg2
from dotenv import load_dotenv
import json
from geopy.geocoders import Nominatim
import time
import csv
from io import StringIO
from flask import send_file
from io import BytesIO
import pandas as pd
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def load_electricity_data():
    df = pd.read_csv('durban_electricity_data_enhanced.csv')
    df['Last Purchase Date'] = pd.to_datetime(df['Last Purchase Date'])
    return df

def get_geolocation_data():
    cache_file = 'geolocation_cache.json'
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            return json.load(f)
    
    df = load_electricity_data()
    geolocator = Nominatim(user_agent="power_outage_app")
    locations = {}
    
    for place in df['Place'].unique():
        try:
            location = geolocator.geocode(f"{place}, Durban, South Africa")
            if location:
                locations[place] = {
                    'lat': location.latitude,
                    'lon': location.longitude
                }
            time.sleep(1)
        except Exception as e:
            print(f"Error geocoding {place}: {e}")
            locations[place] = {'lat': -29.8587, 'lon': 31.0218}  # Default to Durban center
    
    with open(cache_file, 'w') as f:
        json.dump(locations, f)
    
    return locations

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except psycopg2.OperationalError as e:
        app.logger.error(f"Database connection error: {e}")
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role')

    if not all([username, password, role]):
        flash("All fields are required")
        return redirect(url_for('index'))

    conn = get_db_connection()
    if not conn:
        flash("Database connection error. Please try again later.")
        return redirect(url_for('index'))

    try:
        with conn.cursor() as cursor:
            db_role = 'admin' if role == 'admin' else 'technician'
            
            cursor.execute(
                """SELECT id, username, role FROM users 
                WHERE username = %s AND role = %s 
                AND password = crypt(%s, password)""",
                (username, db_role, password)
            )
            user = cursor.fetchone()

            if user:
                session['user_id'] = user[0]
                session['username'] = user[1]
                session['role'] = user[2]

                if user[2] == 'admin':
                    return redirect(url_for('dashboard'))
                else:
                    return redirect(url_for('technician_dashboard'))  # New technician dashboard
            else:
                flash("Invalid credentials. Please try again.")
                return redirect(url_for('index'))
    except Exception as e:
        app.logger.error(f"Login error: {e}")
        flash("An error occurred. Please try again.")
        return redirect(url_for('index'))
    finally:
        conn.close()

@app.route('/technicians')
def technicians():
    # Placeholder technicians data
    technicians = [
        {'id': 1, 'username': 'tech_john', 'full_name': 'John Smith', 'status': 'Available'},
        {'id': 2, 'username': 'tech_sarah', 'full_name': 'Sarah Johnson', 'status': 'Assigned'},
        {'id': 3, 'username': 'tech_mike', 'full_name': 'Mike Davis', 'status': 'Available'},
        {'id': 4, 'username': 'tech_emma', 'full_name': 'Emma Wilson', 'status': 'Onsite'},
        {'id': 5, 'username': 'tech_david', 'full_name': 'David Brown', 'status': 'Assigned'},
        {'id': 6, 'username': 'tech_linda', 'full_name': 'Linda Martinez', 'status': 'Available'},
        {'id': 7, 'username': 'tech_james', 'full_name': 'James Wilson', 'status': 'Completed'},
        {'id': 8, 'username': 'tech_rachel', 'full_name': 'Rachel Green', 'status': 'Assigned'}
    ]
    
    return render_template('technicians.html', 
                         technicians=technicians,
                         last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

@app.route('/technician_dashboard')
def technician_dashboard():
    if 'user_id' not in session or session.get('role') != 'technician':
        flash("Please login as a technician")
        return redirect(url_for('index'))
    return render_template('technician_dashboard.html')

@app.route('/reports')
def reports():
    if 'user_id' not in session or session.get('role') != 'admin':
        flash("Please login as an admin to access this page")
        return redirect(url_for('index'))
    
    df = load_electricity_data()
    return render_template('reports.html',
                         places=sorted(df['Place'].unique().tolist()),
                         meter_types=df['Meter Type'].unique().tolist())
    
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session or session.get('role') != 'admin':
        flash("Please login as an admin to access the dashboard")
        return redirect(url_for('index'))
    
    df = load_electricity_data()
    geolocations = get_geolocation_data()
    
    # Electricity Usage Stats
    total_meters = len(df)
    active_meters = len(df[df['Meter Status'] == 'Active'])
    total_revenue = df['Electricity Bought (R)'].sum()
    
    # Fault Analysis
    outages = df[df['Power Outage'] == 'Yes']
    outage_count = len(outages)
    
    # Group outages by place and user type
    place_outages = outages.groupby('Place').size().reset_index(name='count')
    user_type_outages = outages.groupby('User Type').size().reset_index(name='count')
    
    # Prepare fault trend data (last 6 months)
    df['Month'] = df['Last Purchase Date'].dt.to_period('M')
    monthly_outages = df[df['Power Outage'] == 'Yes'].groupby('Month').size().reset_index(name='count')
    monthly_outages['Month'] = monthly_outages['Month'].astype(str)
    
    # Prepare map data and priority faults
    outage_locations = []
    priority_faults = []
    
    for place in df['Place'].unique():
        place_data = df[df['Place'] == place]
        place_outages = place_data[place_data['Power Outage'] == 'Yes']
        
        if place in geolocations:
            outage_info = {
                'place': place,
                'lat': geolocations[place]['lat'],
                'lon': geolocations[place]['lon'],
                'outage_count': len(place_outages),
                'total_customers': len(place_data),
                'critical_faults': len(place_outages[place_outages['User Type'] == 'Industrial']),
                'standard_faults': len(place_outages[place_outages['User Type'] != 'Industrial'])
            }
            outage_locations.append(outage_info)
            
            # Add to priority faults if there are outages
            if len(place_outages) > 0:
                for _, row in place_outages.iterrows():
                    priority_faults.append({
                        'location': place,
                        'type': row['User Type'],
                        'status': 'Critical' if row['User Type'] == 'Industrial' else 'Standard',
                        'days': (datetime.now() - row['Last Purchase Date']).days
                    })
    
    last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    return render_template('dashboard.html', 
                         username=session['username'].title(),
                         last_updated=last_updated,
                         total_meters=total_meters,
                         active_meters=active_meters,
                         total_revenue=total_revenue,
                         outage_count=outage_count,
                         places=sorted(df['Place'].unique().tolist()),
                         outage_locations=outage_locations,
                         monthly_outages=monthly_outages.to_dict('records'),
                         user_type_outages=user_type_outages.to_dict('records'),
                         priority_faults=priority_faults)
    
@app.route('/alerts')
def alerts():
    # Placeholder alerts data
    alerts = [
        {
            'device_id': 'DTN-3J7M',
            'user_name': 'Chatsworth Residences Block 4',
            'place': 'Chatsworth',
            'issue_type': 'Power Outage',
            'timestamp': '2024-03-20 08:45:00',
            'resolved': 'No',
            'severity': 'Critical'
        },
        {
            'device_id': 'KW-4582-F',
            'user_name': 'Mashu Textiles Ltd',
            'place': 'Kwamashu',
            'issue_type': 'Voltage Fluctuation',
            'timestamp': '2024-03-20 07:30:00',
            'resolved': 'No',
            'severity': 'High'
        },
        {
            'device_id': 'DN-SUB7',
            'user_name': 'Durba',
            'place': 'Durban North',
            'issue_type': 'Transformer Overheat',
            'timestamp': '2024-03-19 18:00:00',
            'resolved': 'No',
            'severity': 'Critical'
        },
    ]
    
    places = ['Chatsworth', 'Durban North', 'Kwamashu', 'Newlands']
    return render_template('alerts.html', 
                         username="Admin",
                         alerts=alerts,
                         places=places,
                         last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

@app.route('/filter_data', methods=['POST'])
def filter_data():
    place = request.form.get('place', 'all')
    df = load_electricity_data()
    
    if place != 'all':
        df = df[df['Place'] == place]
    
    # Electricity Usage Stats
    total_meters = len(df)
    active_meters = len(df[df['Meter Status'] == 'Active'])
    total_revenue = df['Electricity Bought (R)'].sum()
    
    # Fault Analysis
    outages = df[df['Power Outage'] == 'Yes']
    outage_count = len(outages)
    
    # Prepare chart data
    monthly_outages = df[df['Power Outage'] == 'Yes'].groupby(
        df['Last Purchase Date'].dt.to_period('M')
    ).size().reset_index(name='count')
    monthly_outages['Month'] = monthly_outages['Last Purchase Date'].astype(str)
    
    user_type_outages = outages.groupby('User Type').size().reset_index(name='count')
    
    # Prepare priority faults for filtered data
    priority_faults = []
    if place != 'all':
        place_outages = df[df['Power Outage'] == 'Yes']
        for _, row in place_outages.iterrows():
            priority_faults.append({
                'location': place,
                'type': row['User Type'],
                'status': 'Critical' if row['User Type'] == 'Industrial' else 'Standard',
                'days': (datetime.now() - row['Last Purchase Date']).days
            })
    else:
        for place in df['Place'].unique():
            place_outages = df[(df['Place'] == place) & (df['Power Outage'] == 'Yes')]
            for _, row in place_outages.iterrows():
                priority_faults.append({
                    'location': place,
                    'type': row['User Type'],
                    'status': 'Critical' if row['User Type'] == 'Industrial' else 'Standard',
                    'days': (datetime.now() - row['Last Purchase Date']).days
                })
    
    return jsonify({
        'total_meters': total_meters,
        'active_meters': active_meters,
        'total_revenue': total_revenue,
        'outage_count': outage_count,
        'monthly_outages': monthly_outages[['Month', 'count']].to_dict('records'),
        'user_type_outages': user_type_outages.to_dict('records'),
        'priority_faults': priority_faults
    })

@app.route('/generate_report')
def generate_report():
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    report_type = request.args.get('type', 'outage')
    report_format = request.args.get('format', 'csv')
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    area = request.args.get('area', 'all')
    meter_type = request.args.get('meter_type', 'all')

    df = load_electricity_data()
    
    # Apply filters
    if start_date and end_date:
        df = df[(df['Last Purchase Date'] >= start_date) & (df['Last Purchase Date'] <= end_date)]
    if area != 'all':
        df = df[df['Place'] == area]
    if meter_type != 'all':
        df = df[df['Meter Type'] == meter_type]

    # Generate report based on type
    if report_type == 'outage':
        report_df = df[df['Power Outage'] == 'Yes']
        filename = f"outage_report_{datetime.now().strftime('%Y%m%d')}"
    elif report_type == 'usage':
        report_df = df[['User Type', 'Watts Used', 'Electricity Bought (R)']]
        filename = f"usage_report_{datetime.now().strftime('%Y%m%d')}"
    elif report_type == 'revenue':
        report_df = df.groupby('Place')['Electricity Bought (R)'].sum().reset_index()
        filename = f"revenue_report_{datetime.now().strftime('%Y%m%d')}"
    elif report_type == 'meter':
        report_df = df.groupby(['Meter Type', 'Meter Status']).size().reset_index(name='Count')
        filename = f"meter_report_{datetime.now().strftime('%Y%m%d')}"
    else:
        return jsonify({'error': 'Invalid report type'}), 400

    # Generate output
    if report_format == 'csv':
        csv_buffer = StringIO()
        report_df.to_csv(csv_buffer, index=False)
        csv_buffer.seek(0)
        return send_file(
            BytesIO(csv_buffer.getvalue().encode()),
            mimetype='text/csv',
            download_name=f"{filename}.csv",
            as_attachment=True
        )
    elif report_format == 'pdf':
        pdf_buffer = BytesIO()
        p = canvas.Canvas(pdf_buffer, pagesize=letter)
        
        # PDF Header
        p.setFont("Helvetica-Bold", 16)
        p.drawString(72, 750, f"{report_type.capitalize()} Report")
        p.setFont("Helvetica", 12)
        p.drawString(72, 730, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        # Convert DataFrame to table
        data = [report_df.columns.tolist()] + report_df.values.tolist()
        table = Table(data)
        
        # Style table
        style = TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#4361ee')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 12),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f8f9ff')),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#e0e0e0'))
        ])
        table.setStyle(style)
        
        # Draw table
        table.wrapOn(p, 400, 600)
        table.drawOn(p, 72, 600)
        
        p.save()
        pdf_buffer.seek(0)
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            download_name=f"{filename}.pdf",
            as_attachment=True
        )
    else:
        return jsonify({'error': 'Invalid format'}), 400

@app.route('/assign_technician/<int:tech_id>', methods=['POST'])
def assign_technician(tech_id):
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    alert_id = data.get('alert_id')
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Update technician status
        cursor.execute("""
            UPDATE technicians 
            SET status = 'Assigned', 
                current_assignment = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (alert_id, tech_id))
        
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f"Assignment error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/update_tech_status/<int:tech_id>', methods=['POST'])
def update_tech_status(tech_id):
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.json
    new_status = data.get('status')
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE technicians 
            SET status = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (new_status, tech_id))
        
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f"Status update error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=True)