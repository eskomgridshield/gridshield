from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session
import pandas as pd
from datetime import datetime
import os
import psycopg2
from dotenv import load_dotenv
import json
from geopy.geocoders import Nominatim
import time

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def load_electricity_data():
    df = pd.read_csv('durban_electricity_data.csv')
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
                    flash("Access denied: You are not an admin.")
                    return redirect(url_for('index'))
            else:
                flash("Invalid credentials. Please try again.")
                return redirect(url_for('index'))
    except Exception as e:
        app.logger.error(f"Login error: {e}")
        flash("An error occurred. Please try again.")
        return redirect(url_for('index'))
    finally:
        conn.close()

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

if __name__ == '__main__':
    app.run(debug=True)