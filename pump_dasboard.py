"""
Challawa Monitoring System
Real-time monitoring of Siemens S7-1200 PLC via Snap7
"""

from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO
import snap7
from snap7.util import get_real, get_bool
import struct
import time
import threading
import sqlite3
from datetime import datetime, timedelta
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
"""
Challawa Monitoring System
Real-time monitoring of Siemens S7-1200 PLC via Snap7
"""

from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO
import snap7
from snap7.util import get_real, get_bool
import struct
import time
import threading
import sqlite3
from datetime import datetime, timedelta
import io
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

app = Flask(__name__)
app.config['SECRET_KEY'] = 'pump-monitor-secret'
socketio = SocketIO(app, cors_allowed_origins="*")

# PLC Configuration
PLC_IP = "192.168.200.25"
PLC_RACK = 0
PLC_SLOT = 1
DB_NUMBER = 39
CYCLE_TIME = 1.0  # 1 second (increased from 500ms to reduce "Job pending" errors)
MAX_RETRIES = 3
RETRY_DELAY = 0.5  # seconds between retries

# Pump names mapping
PUMP_NAMES = {
    1: "LINE 3&5 UPS FAN COIL UNITS",
    2: "CAN Line UPS Room AHU 1",
    3: "GREENFIELD LV UPS ROOM",
    4: "CAN LINE UPS ROOM AHU 2",
    5: "LINE 7 BLOW MOULD SPARE",
    6: "GREENFIELD LV UPS ROOM AHU & 2",
    7: "LINE 7 BLOWMOULD"
}

# Database initialization
def init_database():
    conn = sqlite3.connect('pump_events.db')
    cursor = conn.cursor()
    
    # Trip events table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trip_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pump_id INTEGER NOT NULL,
            pump_name TEXT NOT NULL,
            event_type TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            pressure REAL,
            pressure_setpoint REAL
        )
    ''')
    
    # Pressure history table for health monitoring
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pressure_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pump_id INTEGER NOT NULL,
            pump_name TEXT NOT NULL,
            pressure REAL,
            pressure_setpoint REAL,
            status TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_database()

# Track previous trip states to detect transitions
previous_trip_states = {i: False for i in range(1, 8)}

class PumpMonitor:
    def __init__(self):
        self.plc = snap7.client.Client()
        self.connected = False
        self.running = False
        self.lock = threading.Lock()  # Prevent concurrent PLC access
        
    def connect(self):
        try:
            if self.plc.get_connected():
                self.plc.disconnect()
            self.plc.connect(PLC_IP, PLC_RACK, PLC_SLOT)
            self.connected = True
            print(f"✓ Connected to PLC at {PLC_IP}")
            return True
        except Exception as e:
            print(f"✗ Connection failed: {e}")
            self.connected = False
            return False
    
    def reconnect(self):
        """Force reconnection to PLC"""
        try:
            if self.plc.get_connected():
                self.plc.disconnect()
        except:
            pass
        time.sleep(0.5)
        return self.connect()
    
    def read_db39(self):
        """Read data from DB39 with retry logic"""
        with self.lock:  # Prevent concurrent access
            for attempt in range(MAX_RETRIES):
                try:
                    # Read 70 bytes from DB39 starting at byte 0 (covers all pumps: LINE 3&5 UPS FAN COIL UNITS, CAN Line UPS Room AHU 1, GREENFIELD LV UPS ROOM, CAN LINE UPS ROOM AHU 2, LINE 7 BLOW MOULD SPARE, GREENFIELD LV UPS ROOM AHU & 2, LINE 7 BLOWMOULD)
                    data = self.plc.db_read(DB_NUMBER, 0, 70)
            
                    # Parse Pump 1 data (LINE 3&5 UPS FAN COIL UNITS)
                    alarm = get_bool(data, 0, 0)
                    ready_yellow = get_bool(data, 0, 1)
                    running_green = get_bool(data, 0, 2)
                    trip_red = get_bool(data, 0, 3)
                    pressure = get_real(data, 2)
                    pressure_sp = get_real(data, 6)
                    
                    # Parse Pump 2 data (CAN Line UPS Room AHU 1)
                    p2_ready_yellow = get_bool(data, 10, 0)
                    p2_running_green = get_bool(data, 10, 1)
                    p2_trip_red = get_bool(data, 10, 2)
                    p2_pressure = get_real(data, 12)
                    p2_pressure_sp = get_real(data, 16)
                    
                    # Parse Pump 3 data (GREENFIELD LV UPS ROOM)
                    p3_ready_yellow = get_bool(data, 20, 0)
                    p3_running_green = get_bool(data, 20, 1)
                    p3_trip_red = get_bool(data, 20, 2)
                    p3_pressure = get_real(data, 22)
                    p3_pressure_sp = get_real(data, 26)
                    
                    # Parse Pump 4 data (CAN LINE UPS ROOM AHU 2, bit order: Ready=0, Trip=1, Running=2)
                    p4_ready_yellow = get_bool(data, 30, 0)
                    p4_trip_red = get_bool(data, 30, 1)
                    p4_running_green = get_bool(data, 30, 2)
                    p4_pressure = get_real(data, 32)
                    p4_pressure_sp = get_real(data, 36)
                    
                    # Parse Pump 5 data (LINE 7 BLOW MOULD SPARE, bit order: Running=0, Ready=1, Trip=2)
                    p5_running_green = get_bool(data, 40, 0)
                    p5_ready_yellow = get_bool(data, 40, 1)
                    p5_trip_red = get_bool(data, 40, 2)
                    p5_pressure = get_real(data, 42)
                    p5_pressure_sp = get_real(data, 46)
                    
                    # Parse Pump 6 data (GREENFIELD LV UPS ROOM AHU & 2, Ready=0, Running=1, Trip=2)
                    p6_ready_yellow = get_bool(data, 50, 0)
                    p6_running_green = get_bool(data, 50, 1)
                    p6_trip_red = get_bool(data, 50, 2)
                    p6_pressure = get_real(data, 52)
                    p6_pressure_sp = get_real(data, 56)
                    
                    # Parse Pump 7 data (LINE 7 BLOWMOULD, Ready=0, Running=1, Trip=2)
                    p7_ready_yellow = get_bool(data, 60, 0)
                    p7_running_green = get_bool(data, 60, 1)
                    p7_trip_red = get_bool(data, 60, 2)
                    p7_pressure = get_real(data, 62)
                    p7_pressure_sp = get_real(data, 66)
                    
                    # Mutually exclusive status logic
                    status = "READY" if ready_yellow else ("RUNNING" if running_green else ("TRIP" if trip_red else "UNKNOWN"))
                    p2_status = "READY" if p2_ready_yellow else ("RUNNING" if p2_running_green else ("TRIP" if p2_trip_red else "UNKNOWN"))
                    p3_status = "READY" if p3_ready_yellow else ("RUNNING" if p3_running_green else ("TRIP" if p3_trip_red else "UNKNOWN"))
                    p4_status = "READY" if p4_ready_yellow else ("RUNNING" if p4_running_green else ("TRIP" if p4_trip_red else "UNKNOWN"))
                    p5_status = "READY" if p5_ready_yellow else ("RUNNING" if p5_running_green else ("TRIP" if p5_trip_red else "UNKNOWN"))
                    p6_status = "READY" if p6_ready_yellow else ("RUNNING" if p6_running_green else ("TRIP" if p6_trip_red else "UNKNOWN"))
                    p7_status = "READY" if p7_ready_yellow else ("RUNNING" if p7_running_green else ("TRIP" if p7_trip_red else "UNKNOWN"))
                    
                    return {
                        "alarm": alarm,
                        "ready_yellow": ready_yellow,
                        "running_green": running_green,
                        "trip_red": trip_red,
                        "pressure": round(pressure, 2),
                        "pressure_setpoint": round(pressure_sp, 2),
                        "status": status,
                        "p2_ready_yellow": p2_ready_yellow,
                        "p2_running_green": p2_running_green,
                        "p2_trip_red": p2_trip_red,
                        "p2_pressure": round(p2_pressure, 2),
                        "p2_pressure_setpoint": round(p2_pressure_sp, 2),
                        "p2_status": p2_status,
                        "p3_ready_yellow": p3_ready_yellow,
                        "p3_running_green": p3_running_green,
                        "p3_trip_red": p3_trip_red,
                        "p3_pressure": round(p3_pressure, 2),
                        "p3_pressure_setpoint": round(p3_pressure_sp, 2),
                        "p3_status": p3_status,
                        "p4_ready_yellow": p4_ready_yellow,
                        "p4_running_green": p4_running_green,
                        "p4_trip_red": p4_trip_red,
                        "p4_pressure": round(p4_pressure, 2),
                        "p4_pressure_setpoint": round(p4_pressure_sp, 2),
                        "p4_status": p4_status,
                        "p5_ready_yellow": p5_ready_yellow,
                        "p5_running_green": p5_running_green,
                        "p5_trip_red": p5_trip_red,
                        "p5_pressure": round(p5_pressure, 2),
                        "p5_pressure_setpoint": round(p5_pressure_sp, 2),
                        "p5_status": p5_status,
                        "p6_ready_yellow": p6_ready_yellow,
                        "p6_running_green": p6_running_green,
                        "p6_trip_red": p6_trip_red,
                        "p6_pressure": round(p6_pressure, 2),
                        "p6_pressure_setpoint": round(p6_pressure_sp, 2),
                        "p6_status": p6_status,
                        "p7_ready_yellow": p7_ready_yellow,
                        "p7_running_green": p7_running_green,
                        "p7_trip_red": p7_trip_red,
                        "p7_pressure": round(p7_pressure, 2),
                        "p7_pressure_setpoint": round(p7_pressure_sp, 2),
                        "p7_status": p7_status,
                        "connected": True
                    }
                except Exception as e:
                    error_msg = str(e)
                    # Handle "Job pending" error - PLC is busy, wait and retry
                    if isinstance(e.args[0], bytes) and b'Job pending' in e.args[0]:
                        print(f"⚠ PLC busy (attempt {attempt + 1}/{MAX_RETRIES}), retrying...")
                        time.sleep(RETRY_DELAY)
                        continue
                    elif 'Job pending' in error_msg:
                        print(f"⚠ PLC busy (attempt {attempt + 1}/{MAX_RETRIES}), retrying...")
                        time.sleep(RETRY_DELAY)
                        continue
                    # Handle connection errors - reconnect
                    elif 'Unreachable' in error_msg or 'TCP' in error_msg or not self.plc.get_connected():
                        print(f"⚠ Connection lost, reconnecting...")
                        self.connected = False
                        self.reconnect()
                        time.sleep(RETRY_DELAY)
                        continue
                    else:
                        print(f"Read error: {e}")
                        break
            
            # All retries failed, return error state
            print(f"✗ Failed to read PLC after {MAX_RETRIES} attempts")
            self.connected = False
            return {
                "alarm": False,
                "ready_yellow": False,
                "running_green": False,
                "trip_red": False,
                "pressure": 0.0,
                "pressure_setpoint": 0.0,
                "status": "ERROR",
                "p2_ready_yellow": False,
                "p2_running_green": False,
                "p2_trip_red": False,
                "p2_pressure": 0.0,
                "p2_pressure_setpoint": 0.0,
                "p2_status": "ERROR",
                "p3_ready_yellow": False,
                "p3_running_green": False,
                "p3_trip_red": False,
                "p3_pressure": 0.0,
                "p3_pressure_setpoint": 0.0,
                "p3_status": "ERROR",
                "p4_ready_yellow": False,
                "p4_running_green": False,
                "p4_trip_red": False,
                "p4_pressure": 0.0,
                "p4_pressure_setpoint": 0.0,
                "p4_status": "ERROR",
                "p5_ready_yellow": False,
                "p5_running_green": False,
                "p5_trip_red": False,
                "p5_pressure": 0.0,
                "p5_pressure_setpoint": 0.0,
                "p5_status": "ERROR",
                "p6_ready_yellow": False,
                "p6_running_green": False,
                "p6_trip_red": False,
                "p6_pressure": 0.0,
                "p6_pressure_setpoint": 0.0,
                "p6_status": "ERROR",
                "p7_ready_yellow": False,
                "p7_running_green": False,
                "p7_trip_red": False,
                "p7_pressure": 0.0,
                "p7_pressure_setpoint": 0.0,
                "p7_status": "ERROR",
                "connected": False
            }
    
    def monitor_loop(self):
        """Continuous monitoring loop"""
        log_counter = 0
        while self.running:
            if not self.connected:
                self.connect()
            
            if self.connected:
                data = self.read_db39()
                socketio.emit('pump_data', data)
                
                # Log events and pressure data
                log_events(data)
                
                # Log pressure history every 60 cycles (30 seconds)
                log_counter += 1
                if log_counter >= 60:
                    log_pressure_history(data)
                    log_counter = 0
            
            time.sleep(CYCLE_TIME)
    
    def start(self):
        self.running = True
        self.start_time = time.time()
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
    
    def stop(self):
        self.running = False
        if self.connected:
            self.plc.disconnect()

def log_events(data):
    """Log trip events to database"""
    global previous_trip_states
    
    trip_states = {
        1: data.get('trip_red', False),
        2: data.get('p2_trip_red', False),
        3: data.get('p3_trip_red', False),
        4: data.get('p4_trip_red', False),
        5: data.get('p5_trip_red', False),
        6: data.get('p6_trip_red', False),
        7: data.get('p7_trip_red', False)
    }
    
    pressures = {
        1: (data.get('pressure', 0), data.get('pressure_setpoint', 0)),
        2: (data.get('p2_pressure', 0), data.get('p2_pressure_setpoint', 0)),
        3: (data.get('p3_pressure', 0), data.get('p3_pressure_setpoint', 0)),
        4: (data.get('p4_pressure', 0), data.get('p4_pressure_setpoint', 0)),
        5: (data.get('p5_pressure', 0), data.get('p5_pressure_setpoint', 0)),
        6: (data.get('p6_pressure', 0), data.get('p6_pressure_setpoint', 0)),
        7: (data.get('p7_pressure', 0), data.get('p7_pressure_setpoint', 0))
    }
    
    try:
        conn = sqlite3.connect('pump_events.db')
        cursor = conn.cursor()
        
        for pump_id in range(1, 8):
            current_trip = trip_states[pump_id]
            previous_trip = previous_trip_states[pump_id]
            
            # Detect trip event (transition from False to True)
            if current_trip and not previous_trip:
                cursor.execute('''
                    INSERT INTO trip_events (pump_id, pump_name, event_type, pressure, pressure_setpoint)
                    VALUES (?, ?, ?, ?, ?)
                ''', (pump_id, PUMP_NAMES[pump_id], 'TRIP', pressures[pump_id][0], pressures[pump_id][1]))
            
            # Detect trip cleared (transition from True to False)
            elif not current_trip and previous_trip:
                cursor.execute('''
                    INSERT INTO trip_events (pump_id, pump_name, event_type, pressure, pressure_setpoint)
                    VALUES (?, ?, ?, ?, ?)
                ''', (pump_id, PUMP_NAMES[pump_id], 'TRIP_CLEARED', pressures[pump_id][0], pressures[pump_id][1]))
            
            previous_trip_states[pump_id] = current_trip
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database error: {e}")

def log_pressure_history(data):
    """Log pressure readings for health monitoring"""
    try:
        conn = sqlite3.connect('pump_events.db')
        cursor = conn.cursor()
        
        pump_data = [
            (1, data.get('pressure', 0), data.get('pressure_setpoint', 0), data.get('status', 'UNKNOWN')),
            (2, data.get('p2_pressure', 0), data.get('p2_pressure_setpoint', 0), data.get('p2_status', 'UNKNOWN')),
            (3, data.get('p3_pressure', 0), data.get('p3_pressure_setpoint', 0), data.get('p3_status', 'UNKNOWN')),
            (4, data.get('p4_pressure', 0), data.get('p4_pressure_setpoint', 0), data.get('p4_status', 'UNKNOWN')),
            (5, data.get('p5_pressure', 0), data.get('p5_pressure_setpoint', 0), data.get('p5_status', 'UNKNOWN')),
            (6, data.get('p6_pressure', 0), data.get('p6_pressure_setpoint', 0), data.get('p6_status', 'UNKNOWN')),
            (7, data.get('p7_pressure', 0), data.get('p7_pressure_setpoint', 0), data.get('p7_status', 'UNKNOWN'))
        ]
        
        for pump_id, pressure, setpoint, status in pump_data:
            cursor.execute('''
                INSERT INTO pressure_history (pump_id, pump_name, pressure, pressure_setpoint, status)
                VALUES (?, ?, ?, ?, ?)
            ''', (pump_id, PUMP_NAMES[pump_id], pressure, setpoint, status))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Pressure logging error: {e}")

# Global monitor instance
monitor = PumpMonitor()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/reports')
def reports():
    return REPORTS_TEMPLATE

@app.route('/api/status')
def get_status():
    if monitor.connected:
        return jsonify(monitor.read_db39())
    return jsonify({"connected": False})

@app.route('/api/trip-events')
def get_trip_events():
    pump_id = request.args.get('pump_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    try:
        conn = sqlite3.connect('pump_events.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = "SELECT * FROM trip_events WHERE 1=1"
        params = []
        
        if pump_id and pump_id != 'all':
            query += " AND pump_id = ?"
            params.append(int(pump_id))
        
        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date)
        
        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date + " 23:59:59")
        
        query += " ORDER BY timestamp DESC LIMIT 500"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Count total trips
        count_query = "SELECT COUNT(*) as total FROM trip_events WHERE event_type = 'TRIP'"
        count_params = []
        if pump_id and pump_id != 'all':
            count_query += " AND pump_id = ?"
            count_params.append(int(pump_id))
        if start_date:
            count_query += " AND timestamp >= ?"
            count_params.append(start_date)
        if end_date:
            count_query += " AND timestamp <= ?"
            count_params.append(end_date + " 23:59:59")
        
        cursor.execute(count_query, count_params)
        total_trips = cursor.fetchone()['total']
        
        conn.close()
        
        events = []
        for row in rows:
            events.append({
                'timestamp': row['timestamp'],
                'pump_name': row['pump_name'],
                'event_type': row['event_type'],
                'pressure': row['pressure'],
                'duration': '--'
            })
        
        return jsonify({'events': events, 'total_trips': total_trips})
    except Exception as e:
        return jsonify({"error": str(e), 'events': [], 'total_trips': 0}), 500

@app.route('/api/pressure-history')
def get_pressure_history():
    pump_id = request.args.get('pump_id', type=int)
    hours = request.args.get('hours', 24, type=int)
    
    try:
        conn = sqlite3.connect('pump_events.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        query = "SELECT * FROM pressure_history WHERE timestamp >= ?"
        params = [cutoff_time.strftime('%Y-%m-%d %H:%M:%S')]
        
        if pump_id:
            query += " AND pump_id = ?"
            params.append(pump_id)
        
        query += " ORDER BY timestamp DESC"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return jsonify([dict(row) for row in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/pump-health')
def get_pump_health():
    """Get real-time pump health from PLC + historical trip data from database"""
    try:
        # Get real-time data from PLC
        if monitor.connected:
            plc_data = monitor.read_db39()
        else:
            plc_data = {}
        
        # Get trip counts from database
        conn = sqlite3.connect('pump_events.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        health_data = []
        
        # Map pump data to consistent structure
        pump_realtime = {
            1: {
                'pressure': plc_data.get('pressure', 0),
                'setpoint': plc_data.get('pressure_setpoint', 0),
                'is_ready': plc_data.get('ready_yellow', False),
                'is_running': plc_data.get('running_green', False),
                'is_trip': plc_data.get('trip_red', False)
            },
            2: {
                'pressure': plc_data.get('p2_pressure', 0),
                'setpoint': plc_data.get('p2_pressure_setpoint', 0),
                'is_ready': plc_data.get('p2_ready_yellow', False),
                'is_running': plc_data.get('p2_running_green', False),
                'is_trip': plc_data.get('p2_trip_red', False)
            },
            3: {
                'pressure': plc_data.get('p3_pressure', 0),
                'setpoint': plc_data.get('p3_pressure_setpoint', 0),
                'is_ready': plc_data.get('p3_ready_yellow', False),
                'is_running': plc_data.get('p3_running_green', False),
                'is_trip': plc_data.get('p3_trip_red', False)
            },
            4: {
                'pressure': plc_data.get('p4_pressure', 0),
                'setpoint': plc_data.get('p4_pressure_setpoint', 0),
                'is_ready': plc_data.get('p4_ready_yellow', False),
                'is_running': plc_data.get('p4_running_green', False),
                'is_trip': plc_data.get('p4_trip_red', False)
            },
            5: {
                'pressure': plc_data.get('p5_pressure', 0),
                'setpoint': plc_data.get('p5_pressure_setpoint', 0),
                'is_ready': plc_data.get('p5_ready_yellow', False),
                'is_running': plc_data.get('p5_running_green', False),
                'is_trip': plc_data.get('p5_trip_red', False)
            },
            6: {
                'pressure': plc_data.get('p6_pressure', 0),
                'setpoint': plc_data.get('p6_pressure_setpoint', 0),
                'is_ready': plc_data.get('p6_ready_yellow', False),
                'is_running': plc_data.get('p6_running_green', False),
                'is_trip': plc_data.get('p6_trip_red', False)
            },
            7: {
                'pressure': plc_data.get('p7_pressure', 0),
                'setpoint': plc_data.get('p7_pressure_setpoint', 0),
                'is_ready': plc_data.get('p7_ready_yellow', False),
                'is_running': plc_data.get('p7_running_green', False),
                'is_trip': plc_data.get('p7_trip_red', False)
            }
        }
        
        for pump_id in range(1, 8):
            # Get trip count last 24 hours from database
            cursor.execute('''
                SELECT COUNT(*) as trip_count FROM trip_events 
                WHERE pump_id = ? AND event_type = 'TRIP' 
                AND timestamp >= datetime('now', '-24 hours')
            ''', (pump_id,))
            trip_count = cursor.fetchone()['trip_count']
            
            # Get last trip time
            cursor.execute('''
                SELECT timestamp FROM trip_events 
                WHERE pump_id = ? AND event_type = 'TRIP'
                ORDER BY timestamp DESC LIMIT 1
            ''', (pump_id,))
            last_trip_row = cursor.fetchone()
            last_trip = last_trip_row['timestamp'] if last_trip_row else None
            
            # Get real-time data
            rt = pump_realtime[pump_id]
            
            health_data.append({
                'pump_id': pump_id,
                'name': PUMP_NAMES[pump_id],
                'pressure': rt['pressure'],
                'setpoint': rt['setpoint'],
                'is_ready': rt['is_ready'],
                'is_running': rt['is_running'],
                'is_trip': rt['is_trip'],
                'trip_count_24h': trip_count,
                'last_trip': last_trip
            })
        
        conn.close()
        
        # Calculate uptime (time since last system start)
        import datetime as dt
        uptime_seconds = int(time.time() - monitor.start_time) if hasattr(monitor, 'start_time') else 0
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m"
        
        return jsonify({
            'pumps': health_data,
            'uptime': uptime_str,
            'connected': monitor.connected
        })
    except Exception as e:
        return jsonify({"error": str(e), 'pumps': []}), 500

@app.route('/api/generate-pdf')
def generate_pdf():
    pump_id_param = request.args.get('pump_id')
    pump_id = int(pump_id_param) if pump_id_param and pump_id_param != 'all' else None
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    try:
        conn = sqlite3.connect('pump_events.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get trip events
        query = "SELECT * FROM trip_events WHERE 1=1"
        params = []
        if pump_id:
            query += " AND pump_id = ?"
            params.append(pump_id)
        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date)
        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date + " 23:59:59")
        query += " ORDER BY id DESC"
        cursor.execute(query, params)
        trip_rows = cursor.fetchall()
        
        conn.close()
        
        # Get real-time health data
        health_data = []
        if monitor.connected:
            plc_data = monitor.read_db39()
            pump_realtime = {
                1: {'pressure': plc_data.get('pressure', 0), 'setpoint': plc_data.get('pressure_setpoint', 0),
                    'is_ready': plc_data.get('ready_yellow', False), 'is_running': plc_data.get('running_green', False),
                    'is_trip': plc_data.get('trip_red', False)},
                2: {'pressure': plc_data.get('p2_pressure', 0), 'setpoint': plc_data.get('p2_pressure_setpoint', 0),
                    'is_ready': plc_data.get('p2_ready_yellow', False), 'is_running': plc_data.get('p2_running_green', False),
                    'is_trip': plc_data.get('p2_trip_red', False)},
                3: {'pressure': plc_data.get('p3_pressure', 0), 'setpoint': plc_data.get('p3_pressure_setpoint', 0),
                    'is_ready': plc_data.get('p3_ready_yellow', False), 'is_running': plc_data.get('p3_running_green', False),
                    'is_trip': plc_data.get('p3_trip_red', False)},
                4: {'pressure': plc_data.get('p4_pressure', 0), 'setpoint': plc_data.get('p4_pressure_setpoint', 0),
                    'is_ready': plc_data.get('p4_ready_yellow', False), 'is_running': plc_data.get('p4_running_green', False),
                    'is_trip': plc_data.get('p4_trip_red', False)},
                5: {'pressure': plc_data.get('p5_pressure', 0), 'setpoint': plc_data.get('p5_pressure_setpoint', 0),
                    'is_ready': plc_data.get('p5_ready_yellow', False), 'is_running': plc_data.get('p5_running_green', False),
                    'is_trip': plc_data.get('p5_trip_red', False)},
                6: {'pressure': plc_data.get('p6_pressure', 0), 'setpoint': plc_data.get('p6_pressure_setpoint', 0),
                    'is_ready': plc_data.get('p6_ready_yellow', False), 'is_running': plc_data.get('p6_running_green', False),
                    'is_trip': plc_data.get('p6_trip_red', False)},
                7: {'pressure': plc_data.get('p7_pressure', 0), 'setpoint': plc_data.get('p7_pressure_setpoint', 0),
                    'is_ready': plc_data.get('p7_ready_yellow', False), 'is_running': plc_data.get('p7_running_green', False),
                    'is_trip': plc_data.get('p7_trip_red', False)},
            }
            for pid in range(1, 8):
                rt = pump_realtime[pid]
                status = 'TRIP' if rt['is_trip'] else ('RUNNING' if rt['is_running'] else ('READY' if rt['is_ready'] else 'OFFLINE'))
                health = 'CRITICAL' if rt['is_trip'] else ('NORMAL' if rt['is_running'] else 'ATTENTION')
                health_data.append([pid, PUMP_NAMES[pid], f"{rt['pressure']:.2f}", f"{rt['setpoint']:.2f}", status, health])
        
        # Generate PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)
        elements = []
        styles = getSampleStyleSheet()
        
        # Custom styles
        title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=20, spaceAfter=10, 
                                     textColor=colors.HexColor('#1a1a2e'), alignment=1)
        subtitle_style = ParagraphStyle('Subtitle', parent=styles['Normal'], fontSize=10, 
                                        textColor=colors.HexColor('#666666'), alignment=1, spaceAfter=20)
        section_style = ParagraphStyle('Section', parent=styles['Heading2'], fontSize=14, spaceBefore=20, 
                                       spaceAfter=10, textColor=colors.HexColor('#2c3e50'))
        info_style = ParagraphStyle('Info', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor('#555555'))
        
        # Header
        elements.append(Paragraph("CHALLAWA MONITORING SYSTEM", title_style))
        elements.append(Paragraph("Pump Status & Trip Events Report", subtitle_style))
        
        # Report metadata
        report_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        pump_filter = PUMP_NAMES.get(pump_id, 'All Pumps') if pump_id else 'All Pumps'
        date_range = f"{start_date or 'Beginning'} to {end_date or 'Now'}"
        
        meta_data = [
            ['Report Generated:', report_time, 'Pump Filter:', pump_filter],
            ['Date Range:', date_range, 'Total Trip Events:', str(len(trip_rows))]
        ]
        meta_table = Table(meta_data, colWidths=[1.5*inch, 2*inch, 1.5*inch, 2*inch])
        meta_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#666666')),
            ('TEXTCOLOR', (2, 0), (2, -1), colors.HexColor('#666666')),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica-Bold'),
            ('FONTNAME', (3, 0), (3, -1), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(meta_table)
        elements.append(Spacer(1, 20))
        
        # Section 1: Real-Time Pump Health Status
        elements.append(Paragraph("1. REAL-TIME PUMP HEALTH STATUS", section_style))
        if health_data:
            health_headers = ['ID', 'Pump Name', 'Pressure (bar)', 'Setpoint (bar)', 'Status', 'Health']
            health_table_data = [health_headers] + health_data
            health_table = Table(health_table_data, colWidths=[0.4*inch, 2.2*inch, 1*inch, 1*inch, 0.9*inch, 0.9*inch])
            health_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                ('TOPPADDING', (0, 0), (-1, 0), 10),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            # Color code health status
            for i, row in enumerate(health_data):
                health_status = row[5]
                if health_status == 'CRITICAL':
                    health_table.setStyle(TableStyle([('TEXTCOLOR', (5, i+1), (5, i+1), colors.HexColor('#e74c3c'))]))
                    health_table.setStyle(TableStyle([('TEXTCOLOR', (4, i+1), (4, i+1), colors.HexColor('#e74c3c'))]))
                elif health_status == 'ATTENTION':
                    health_table.setStyle(TableStyle([('TEXTCOLOR', (5, i+1), (5, i+1), colors.HexColor('#f39c12'))]))
                else:
                    health_table.setStyle(TableStyle([('TEXTCOLOR', (5, i+1), (5, i+1), colors.HexColor('#27ae60'))]))
            elements.append(health_table)
        else:
            elements.append(Paragraph("PLC not connected - No real-time data available", info_style))
        
        elements.append(Spacer(1, 20))
        
        # Section 2: Trip Events Log
        elements.append(Paragraph("2. TRIP EVENTS LOG", section_style))
        if trip_rows:
            trip_headers = ['#', 'Pump Name', 'Event', 'Timestamp', 'Pressure', 'Setpoint']
            trip_data = []
            for idx, row in enumerate(trip_rows):
                trip_data.append([
                    str(len(trip_rows) - idx),  # Descending order number
                    row['pump_name'],
                    row['event_type'],
                    row['timestamp'],
                    f"{row['pressure']:.2f}" if row['pressure'] else '--',
                    f"{row['pressure_setpoint']:.2f}" if row['pressure_setpoint'] else '--'
                ])
            
            trip_table_data = [trip_headers] + trip_data
            trip_table = Table(trip_table_data, colWidths=[0.4*inch, 2*inch, 1*inch, 1.5*inch, 0.8*inch, 0.8*inch])
            trip_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e74c3c')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                ('TOPPADDING', (0, 0), (-1, 0), 10),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fff5f5')]),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            # Color code event types
            for i, row in enumerate(trip_data):
                if row[2] == 'TRIP':
                    trip_table.setStyle(TableStyle([('TEXTCOLOR', (2, i+1), (2, i+1), colors.HexColor('#e74c3c'))]))
                else:
                    trip_table.setStyle(TableStyle([('TEXTCOLOR', (2, i+1), (2, i+1), colors.HexColor('#27ae60'))]))
            elements.append(trip_table)
        else:
            elements.append(Paragraph("No trip events found for the selected criteria.", info_style))
        
        elements.append(Spacer(1, 30))
        
        # Footer
        footer_style = ParagraphStyle('Footer', parent=styles['Normal'], fontSize=8, 
                                      textColor=colors.HexColor('#999999'), alignment=1)
        elements.append(Paragraph("--- End of Report ---", footer_style))
        elements.append(Paragraph(f"Challawa Monitoring System | Generated: {report_time}", footer_style))
        
        doc.build(elements)
        buffer.seek(0)
        
        filename = f"challawa_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return Response(buffer.getvalue(), mimetype='application/pdf',
                       headers={'Content-Disposition': f'attachment; filename={filename}'})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Track connected clients (reduce log spam)
connected_clients = 0

@socketio.on('connect')
def handle_connect():
    global connected_clients
    connected_clients += 1
    if connected_clients == 1:
        print(f'Client connected (total: {connected_clients})')
    if monitor.connected:
        socketio.emit('pump_data', monitor.read_db39())

@socketio.on('disconnect')
def handle_disconnect():
    global connected_clients
    connected_clients = max(0, connected_clients - 1)
    if connected_clients == 0:
        print('All clients disconnected')

# HTML Template (save as templates/index.html)
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Challawa Monitoring System</title>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <style>
        :root {
            --primary: #3498db;
            --success: #27ae60;
            --warning: #f39c12;
            --danger: #e74c3c;
            --dark: #1a1a2e;
            --darker: #0f0f1a;
            --card-bg: #16213e;
            --border: #2d3748;
            --text: #e2e8f0;
            --text-muted: #a0aec0;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: var(--darker);
            min-height: 100vh;
            color: var(--text);
            line-height: 1.5;
        }
        
        /* Navigation */
        .navbar {
            background: var(--dark);
            border-bottom: 1px solid var(--border);
            padding: 0 20px;
            position: sticky;
            top: 0;
            z-index: 1000;
        }
        
        .nav-container {
            max-width: 1600px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            height: 60px;
        }
        
        .nav-brand {
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 1.25rem;
            font-weight: 600;
            color: var(--primary);
        }
        
        .nav-brand svg {
            width: 32px;
            height: 32px;
        }
        
        .nav-links {
            display: flex;
            gap: 8px;
        }
        
        .nav-link {
            color: var(--text-muted);
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 0.9rem;
            transition: all 0.2s;
        }
        
        .nav-link:hover, .nav-link.active {
            background: var(--card-bg);
            color: var(--primary);
        }
        
        .nav-status {
            display: flex;
            align-items: center;
            gap: 16px;
        }
        
        .status-badge {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 500;
        }
        
        .status-badge.connected {
            background: rgba(39, 174, 96, 0.15);
            color: var(--success);
        }
        
        .status-badge.disconnected {
            background: rgba(231, 76, 60, 0.15);
            color: var(--danger);
        }
        
        .status-badge.alarm {
            background: rgba(231, 76, 60, 0.2);
            color: var(--danger);
            animation: pulse-alarm 1s infinite;
        }
        
        @keyframes pulse-alarm {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: currentColor;
        }
        
        /* Main Content */
        .main-content {
            max-width: 1600px;
            margin: 0 auto;
            padding: 20px;
        }
        
        .page-header {
            margin-bottom: 24px;
        }
        
        .page-title {
            font-size: 1.5rem;
            font-weight: 600;
            color: var(--text);
        }
        
        .page-subtitle {
            color: var(--text-muted);
            font-size: 0.9rem;
            margin-top: 4px;
        }
        
        /* Pump Grid */
        .pump-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 16px;
        }
        
        /* Pump Card */
        .pump-card {
            background: var(--card-bg);
            border-radius: 12px;
            border: 1px solid var(--border);
            overflow: hidden;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        
        .pump-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0,0,0,0.3);
        }
        
        .pump-card.trip {
            border-color: var(--danger);
            box-shadow: 0 0 20px rgba(231, 76, 60, 0.2);
        }
        
        .pump-card.running {
            border-color: var(--success);
        }
        
        .pump-header {
            padding: 16px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .pump-name {
            font-weight: 600;
            font-size: 0.95rem;
            color: var(--text);
        }
        
        .pump-status {
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .pump-status.running {
            background: rgba(39, 174, 96, 0.15);
            color: var(--success);
        }
        
        .pump-status.ready {
            background: rgba(243, 156, 18, 0.15);
            color: var(--warning);
        }
        
        .pump-status.trip {
            background: rgba(231, 76, 60, 0.15);
            color: var(--danger);
        }
        
        .pump-status.offline {
            background: rgba(160, 174, 192, 0.15);
            color: var(--text-muted);
        }
        
        .pump-body {
            padding: 16px;
            display: flex;
            gap: 16px;
        }
        
        /* Gauge */
        .gauge-wrapper {
            flex: 0 0 100px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        
        .gauge {
            width: 100px;
            height: 100px;
            position: relative;
        }
        
        .gauge svg {
            width: 100%;
            height: 100%;
            transform: rotate(-90deg);
        }
        
        .gauge-bg {
            fill: none;
            stroke: var(--border);
            stroke-width: 8;
        }
        
        .gauge-fill {
            fill: none;
            stroke: var(--primary);
            stroke-width: 8;
            stroke-linecap: round;
            transition: stroke-dashoffset 0.5s ease, stroke 0.3s;
        }
        
        .gauge-center {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            text-align: center;
        }
        
        .gauge-value {
            font-size: 1.25rem;
            font-weight: 700;
            color: var(--text);
        }
        
        .gauge-unit {
            font-size: 0.7rem;
            color: var(--text-muted);
            text-transform: uppercase;
        }
        
        /* Info Panel */
        .info-panel {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        
        .info-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 12px;
            background: rgba(0,0,0,0.2);
            border-radius: 6px;
        }
        
        .info-label {
            font-size: 0.8rem;
            color: var(--text-muted);
        }
        
        .info-value {
            font-size: 0.9rem;
            font-weight: 600;
            color: var(--text);
        }
        
        /* Status Indicators */
        .status-indicators {
            display: flex;
            gap: 8px;
            padding-top: 8px;
        }
        
        .indicator {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 8px;
            background: rgba(0,0,0,0.2);
            border-radius: 6px;
            font-size: 0.75rem;
            color: var(--text-muted);
            transition: all 0.3s;
        }
        
        .indicator .dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--border);
            transition: all 0.3s;
        }
        
        .indicator.active .dot {
            box-shadow: 0 0 10px currentColor;
        }
        
        .indicator.ready.active {
            color: var(--warning);
            background: rgba(243, 156, 18, 0.1);
        }
        
        .indicator.ready.active .dot {
            background: var(--warning);
        }
        
        .indicator.running.active {
            color: var(--success);
            background: rgba(39, 174, 96, 0.1);
        }
        
        .indicator.running.active .dot {
            background: var(--success);
        }
        
        .indicator.trip.active {
            color: var(--danger);
            background: rgba(231, 76, 60, 0.1);
            animation: blink 0.5s infinite;
        }
        
        .indicator.trip.active .dot {
            background: var(--danger);
        }
        
        @keyframes blink {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        /* Responsive */
        @media (max-width: 768px) {
            .nav-container {
                flex-wrap: wrap;
                height: auto;
                padding: 12px 0;
                gap: 12px;
            }
            
            .nav-brand {
                font-size: 1.1rem;
            }
            
            .nav-links {
                order: 3;
                width: 100%;
                justify-content: center;
            }
            
            .pump-grid {
                grid-template-columns: 1fr;
            }
            
            .pump-body {
                flex-direction: column;
                align-items: center;
            }
            
            .gauge-wrapper {
                flex: none;
            }
            
            .info-panel {
                width: 100%;
            }
        }
        
        /* Footer */
        .footer {
            text-align: center;
            padding: 20px;
            color: var(--text-muted);
            font-size: 0.8rem;
            border-top: 1px solid var(--border);
            margin-top: 40px;
        }
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="nav-container">
            <div class="nav-brand">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="3"/>
                    <path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/>
                </svg>
                Challawa Monitoring
            </div>
            <div class="nav-links">
                <a href="/" class="nav-link active">Dashboard</a>
                <a href="/reports" class="nav-link">Reports</a>
            </div>
            <div class="nav-status">
                <div class="status-badge alarm" id="alarmBadge" style="display: none;">
                    <span class="status-dot"></span>
                    <span>ALARM</span>
                </div>
                <div class="status-badge disconnected" id="connectionBadge">
                    <span class="status-dot"></span>
                    <span id="connectionText">Disconnected</span>
                </div>
            </div>
        </div>
    </nav>
    
    <main class="main-content">
        <div class="page-header">
            <h1 class="page-title">Pump Monitoring Dashboard</h1>
            <p class="page-subtitle">Real-time status of all cooling system pumps</p>
        </div>
        
        <div class="pump-grid">
            <!-- Pump 1 -->
            <div class="pump-card" id="pump1Card">
                <div class="pump-header">
                    <span class="pump-name">LINE 3&5 UPS FAN COIL UNITS</span>
                    <span class="pump-status offline" id="p1Status">Offline</span>
                </div>
                <div class="pump-body">
                    <div class="gauge-wrapper">
                        <div class="gauge">
                            <svg viewBox="0 0 100 100">
                                <circle class="gauge-bg" cx="50" cy="50" r="40"/>
                                <circle class="gauge-fill" id="p1Gauge" cx="50" cy="50" r="40" 
                                    stroke-dasharray="251.2" stroke-dashoffset="251.2"/>
                            </svg>
                            <div class="gauge-center">
                                <div class="gauge-value" id="p1Pressure">0.00</div>
                                <div class="gauge-unit">bar</div>
                            </div>
                        </div>
                    </div>
                    <div class="info-panel">
                        <div class="info-row">
                            <span class="info-label">Setpoint</span>
                            <span class="info-value" id="p1Setpoint">0.00 bar</span>
                        </div>
                        <div class="status-indicators">
                            <div class="indicator ready" id="p1Ready">
                                <span class="dot"></span>Ready
                            </div>
                            <div class="indicator running" id="p1Running">
                                <span class="dot"></span>Run
                            </div>
                            <div class="indicator trip" id="p1Trip">
                                <span class="dot"></span>Trip
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Pump 2 -->
            <div class="pump-card" id="pump2Card">
                <div class="pump-header">
                    <span class="pump-name">CAN Line UPS Room AHU 1</span>
                    <span class="pump-status offline" id="p2Status">Offline</span>
                </div>
                <div class="pump-body">
                    <div class="gauge-wrapper">
                        <div class="gauge">
                            <svg viewBox="0 0 100 100">
                                <circle class="gauge-bg" cx="50" cy="50" r="40"/>
                                <circle class="gauge-fill" id="p2Gauge" cx="50" cy="50" r="40" 
                                    stroke-dasharray="251.2" stroke-dashoffset="251.2"/>
                            </svg>
                            <div class="gauge-center">
                                <div class="gauge-value" id="p2Pressure">0.00</div>
                                <div class="gauge-unit">bar</div>
                            </div>
                        </div>
                    </div>
                    <div class="info-panel">
                        <div class="info-row">
                            <span class="info-label">Setpoint</span>
                            <span class="info-value" id="p2Setpoint">0.00 bar</span>
                        </div>
                        <div class="status-indicators">
                            <div class="indicator ready" id="p2Ready">
                                <span class="dot"></span>Ready
                            </div>
                            <div class="indicator running" id="p2Running">
                                <span class="dot"></span>Run
                            </div>
                            <div class="indicator trip" id="p2Trip">
                                <span class="dot"></span>Trip
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Pump 3 -->
            <div class="pump-card" id="pump3Card">
                <div class="pump-header">
                    <span class="pump-name">GREENFIELD LV UPS ROOM</span>
                    <span class="pump-status offline" id="p3Status">Offline</span>
                </div>
                <div class="pump-body">
                    <div class="gauge-wrapper">
                        <div class="gauge">
                            <svg viewBox="0 0 100 100">
                                <circle class="gauge-bg" cx="50" cy="50" r="40"/>
                                <circle class="gauge-fill" id="p3Gauge" cx="50" cy="50" r="40" 
                                    stroke-dasharray="251.2" stroke-dashoffset="251.2"/>
                            </svg>
                            <div class="gauge-center">
                                <div class="gauge-value" id="p3Pressure">0.00</div>
                                <div class="gauge-unit">bar</div>
                            </div>
                        </div>
                    </div>
                    <div class="info-panel">
                        <div class="info-row">
                            <span class="info-label">Setpoint</span>
                            <span class="info-value" id="p3Setpoint">0.00 bar</span>
                        </div>
                        <div class="status-indicators">
                            <div class="indicator ready" id="p3Ready">
                                <span class="dot"></span>Ready
                            </div>
                            <div class="indicator running" id="p3Running">
                                <span class="dot"></span>Run
                            </div>
                            <div class="indicator trip" id="p3Trip">
                                <span class="dot"></span>Trip
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Pump 4 -->
            <div class="pump-card" id="pump4Card">
                <div class="pump-header">
                    <span class="pump-name">CAN LINE UPS ROOM AHU 2</span>
                    <span class="pump-status offline" id="p4Status">Offline</span>
                </div>
                <div class="pump-body">
                    <div class="gauge-wrapper">
                        <div class="gauge">
                            <svg viewBox="0 0 100 100">
                                <circle class="gauge-bg" cx="50" cy="50" r="40"/>
                                <circle class="gauge-fill" id="p4Gauge" cx="50" cy="50" r="40" 
                                    stroke-dasharray="251.2" stroke-dashoffset="251.2"/>
                            </svg>
                            <div class="gauge-center">
                                <div class="gauge-value" id="p4Pressure">0.00</div>
                                <div class="gauge-unit">bar</div>
                            </div>
                        </div>
                    </div>
                    <div class="info-panel">
                        <div class="info-row">
                            <span class="info-label">Setpoint</span>
                            <span class="info-value" id="p4Setpoint">0.00 bar</span>
                        </div>
                        <div class="status-indicators">
                            <div class="indicator ready" id="p4Ready">
                                <span class="dot"></span>Ready
                            </div>
                            <div class="indicator running" id="p4Running">
                                <span class="dot"></span>Run
                            </div>
                            <div class="indicator trip" id="p4Trip">
                                <span class="dot"></span>Trip
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Pump 5 -->
            <div class="pump-card" id="pump5Card">
                <div class="pump-header">
                    <span class="pump-name">LINE 7 BLOW MOULD SPARE</span>
                    <span class="pump-status offline" id="p5Status">Offline</span>
                </div>
                <div class="pump-body">
                    <div class="gauge-wrapper">
                        <div class="gauge">
                            <svg viewBox="0 0 100 100">
                                <circle class="gauge-bg" cx="50" cy="50" r="40"/>
                                <circle class="gauge-fill" id="p5Gauge" cx="50" cy="50" r="40" 
                                    stroke-dasharray="251.2" stroke-dashoffset="251.2"/>
                            </svg>
                            <div class="gauge-center">
                                <div class="gauge-value" id="p5Pressure">0.00</div>
                                <div class="gauge-unit">bar</div>
                            </div>
                        </div>
                    </div>
                    <div class="info-panel">
                        <div class="info-row">
                            <span class="info-label">Setpoint</span>
                            <span class="info-value" id="p5Setpoint">0.00 bar</span>
                        </div>
                        <div class="status-indicators">
                            <div class="indicator ready" id="p5Ready">
                                <span class="dot"></span>Ready
                            </div>
                            <div class="indicator running" id="p5Running">
                                <span class="dot"></span>Run
                            </div>
                            <div class="indicator trip" id="p5Trip">
                                <span class="dot"></span>Trip
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Pump 6 -->
            <div class="pump-card" id="pump6Card">
                <div class="pump-header">
                    <span class="pump-name">GREENFIELD LV UPS ROOM AHU & 2</span>
                    <span class="pump-status offline" id="p6Status">Offline</span>
                </div>
                <div class="pump-body">
                    <div class="gauge-wrapper">
                        <div class="gauge">
                            <svg viewBox="0 0 100 100">
                                <circle class="gauge-bg" cx="50" cy="50" r="40"/>
                                <circle class="gauge-fill" id="p6Gauge" cx="50" cy="50" r="40" 
                                    stroke-dasharray="251.2" stroke-dashoffset="251.2"/>
                            </svg>
                            <div class="gauge-center">
                                <div class="gauge-value" id="p6Pressure">0.00</div>
                                <div class="gauge-unit">bar</div>
                            </div>
                        </div>
                    </div>
                    <div class="info-panel">
                        <div class="info-row">
                            <span class="info-label">Setpoint</span>
                            <span class="info-value" id="p6Setpoint">0.00 bar</span>
                        </div>
                        <div class="status-indicators">
                            <div class="indicator ready" id="p6Ready">
                                <span class="dot"></span>Ready
                            </div>
                            <div class="indicator running" id="p6Running">
                                <span class="dot"></span>Run
                            </div>
                            <div class="indicator trip" id="p6Trip">
                                <span class="dot"></span>Trip
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Pump 7 -->
            <div class="pump-card" id="pump7Card">
                <div class="pump-header">
                    <span class="pump-name">LINE 7 BLOWMOULD</span>
                    <span class="pump-status offline" id="p7Status">Offline</span>
                </div>
                <div class="pump-body">
                    <div class="gauge-wrapper">
                        <div class="gauge">
                            <svg viewBox="0 0 100 100">
                                <circle class="gauge-bg" cx="50" cy="50" r="40"/>
                                <circle class="gauge-fill" id="p7Gauge" cx="50" cy="50" r="40" 
                                    stroke-dasharray="251.2" stroke-dashoffset="251.2"/>
                            </svg>
                            <div class="gauge-center">
                                <div class="gauge-value" id="p7Pressure">0.00</div>
                                <div class="gauge-unit">bar</div>
                            </div>
                        </div>
                    </div>
                    <div class="info-panel">
                        <div class="info-row">
                            <span class="info-label">Setpoint</span>
                            <span class="info-value" id="p7Setpoint">0.00 bar</span>
                        </div>
                        <div class="status-indicators">
                            <div class="indicator ready" id="p7Ready">
                                <span class="dot"></span>Ready
                            </div>
                            <div class="indicator running" id="p7Running">
                                <span class="dot"></span>Run
                            </div>
                            <div class="indicator trip" id="p7Trip">
                                <span class="dot"></span>Trip
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </main>
    
    <footer class="footer">
        Challawa Monitoring System &copy; 2026 | Real-time PLC Data
    </footer>
    
    <script>
        // Socket.IO with Cloudflare-compatible settings
        const socket = io({
            transports: ['websocket', 'polling'],
            upgrade: true,
            rememberUpgrade: true,
            reconnection: true,
            reconnectionAttempts: Infinity,
            reconnectionDelay: 1000,
            reconnectionDelayMax: 5000,
            timeout: 20000
        });
        
        const maxPressure = 10;
        const circumference = 2 * Math.PI * 40; // 251.2
        
        // Fallback polling for when WebSocket fails
        let lastUpdate = Date.now();
        let pollingInterval = null;
        
        function startPollingFallback() {
            if (pollingInterval) return;
            console.log('Starting polling fallback...');
            pollingInterval = setInterval(() => {
                fetch('/api/status')
                    .then(r => r.json())
                    .then(data => {
                        updateStatus(data);
                        lastUpdate = Date.now();
                    })
                    .catch(err => console.error('Polling error:', err));
            }, 1000);
        }
        
        function stopPollingFallback() {
            if (pollingInterval) {
                console.log('Stopping polling fallback, WebSocket active');
                clearInterval(pollingInterval);
                pollingInterval = null;
            }
        }
        
        // Monitor for stale data and activate polling
        setInterval(() => {
            if (Date.now() - lastUpdate > 3000 && !pollingInterval) {
                startPollingFallback();
            }
        }, 2000);
        
        socket.on('connect', () => {
            console.log('WebSocket connected');
            stopPollingFallback();
        });
        
        socket.on('disconnect', () => {
            console.log('WebSocket disconnected');
            startPollingFallback();
        });
        
        socket.on('connect_error', (err) => {
            console.log('WebSocket error:', err.message);
            startPollingFallback();
        });
        
        function updateGauge(gaugeId, value) {
            const gauge = document.getElementById(gaugeId);
            if (!gauge) return;
            const percent = Math.min(value / maxPressure, 1);
            const offset = circumference * (1 - percent);
            gauge.style.strokeDashoffset = offset;
            
            // Color based on value
            if (percent > 0.8) {
                gauge.style.stroke = '#e74c3c';
            } else if (percent > 0.6) {
                gauge.style.stroke = '#f39c12';
            } else {
                gauge.style.stroke = '#3498db';
            }
        }
        
        function updatePumpCard(pumpNum, data, prefix) {
            const card = document.getElementById('pump' + pumpNum + 'Card');
            const status = document.getElementById('p' + pumpNum + 'Status');
            const pressure = document.getElementById('p' + pumpNum + 'Pressure');
            const setpoint = document.getElementById('p' + pumpNum + 'Setpoint');
            const ready = document.getElementById('p' + pumpNum + 'Ready');
            const running = document.getElementById('p' + pumpNum + 'Running');
            const trip = document.getElementById('p' + pumpNum + 'Trip');
            
            // Get data values
            const pressureVal = pumpNum === 1 ? data.pressure : data[prefix + '_pressure'];
            const setpointVal = pumpNum === 1 ? data.pressure_setpoint : data[prefix + '_pressure_setpoint'];
            const isReady = pumpNum === 1 ? data.ready_yellow : data[prefix + '_ready_yellow'];
            const isRunning = pumpNum === 1 ? data.running_green : data[prefix + '_running_green'];
            const isTrip = pumpNum === 1 ? data.trip_red : data[prefix + '_trip_red'];
            
            // Update pressure gauge
            pressure.textContent = (pressureVal || 0).toFixed(2);
            updateGauge('p' + pumpNum + 'Gauge', pressureVal || 0);
            
            // Update setpoint
            setpoint.textContent = (setpointVal || 0).toFixed(2) + ' bar';
            
            // Update indicators
            ready.classList.toggle('active', isReady);
            running.classList.toggle('active', isRunning);
            trip.classList.toggle('active', isTrip);
            
            // Update card status
            card.classList.remove('trip', 'running');
            status.classList.remove('trip', 'running', 'ready', 'offline');
            
            if (isTrip) {
                card.classList.add('trip');
                status.classList.add('trip');
                status.textContent = 'TRIP';
            } else if (isRunning) {
                card.classList.add('running');
                status.classList.add('running');
                status.textContent = 'Running';
            } else if (isReady) {
                status.classList.add('ready');
                status.textContent = 'Ready';
            } else {
                status.classList.add('offline');
                status.textContent = 'Offline';
            }
        }
        
        function updateStatus(data) {
            // Connection status
            const badge = document.getElementById('connectionBadge');
            const text = document.getElementById('connectionText');
            
            if (data.connected) {
                badge.classList.remove('disconnected');
                badge.classList.add('connected');
                text.textContent = 'Connected';
            } else {
                badge.classList.remove('connected');
                badge.classList.add('disconnected');
                text.textContent = 'Disconnected';
            }
            
            // Update all pumps
            updatePumpCard(1, data, '');
            updatePumpCard(2, data, 'p2');
            updatePumpCard(3, data, 'p3');
            updatePumpCard(4, data, 'p4');
            updatePumpCard(5, data, 'p5');
            updatePumpCard(6, data, 'p6');
            updatePumpCard(7, data, 'p7');
            
            // Check for any alarms
            const hasAlarm = data.trip_red || data.p2_trip_red || data.p3_trip_red || 
                           data.p4_trip_red || data.p5_trip_red || data.p6_trip_red || data.p7_trip_red;
            const alarmBadge = document.getElementById('alarmBadge');
            alarmBadge.style.display = hasAlarm ? 'flex' : 'none';
        }
        
        socket.on('pump_data', (data) => {
            updateStatus(data);
            lastUpdate = Date.now();
            stopPollingFallback();
        });
        
        // Initial fetch
        fetch('/api/status')
            .then(r => r.json())
            .then(data => {
                updateStatus(data);
                lastUpdate = Date.now();
            });
        
        // Start polling as initial fallback
        setTimeout(() => {
            if (Date.now() - lastUpdate > 2000) {
                startPollingFallback();
            }
        }, 2000);
    </script>
</body>
</html>
'''


REPORTS_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reports - Challawa Monitoring</title>
    <style>
        :root {
            --primary: #3498db;
            --success: #27ae60;
            --warning: #f39c12;
            --danger: #e74c3c;
            --dark: #1a1a2e;
            --darker: #0f0f1a;
            --card-bg: #16213e;
            --border: #2d3748;
            --text: #e2e8f0;
            --text-muted: #a0aec0;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: var(--darker);
            min-height: 100vh;
            color: var(--text);
            line-height: 1.5;
        }
        
        /* Navigation */
        .navbar {
            background: var(--dark);
            border-bottom: 1px solid var(--border);
            padding: 0 20px;
            position: sticky;
            top: 0;
            z-index: 1000;
        }
        
        .nav-container {
            max-width: 1600px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            height: 60px;
        }
        
        .nav-brand {
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 1.25rem;
            font-weight: 600;
            color: var(--primary);
        }
        
        .nav-brand svg {
            width: 32px;
            height: 32px;
        }
        
        .nav-links {
            display: flex;
            gap: 8px;
        }
        
        .nav-link {
            color: var(--text-muted);
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 0.9rem;
            transition: all 0.2s;
        }
        
        .nav-link:hover, .nav-link.active {
            background: var(--card-bg);
            color: var(--primary);
        }
        
        /* Main Content */
        .main-content {
            max-width: 1600px;
            margin: 0 auto;
            padding: 20px;
        }
        
        .page-header {
            margin-bottom: 10px;
        }
        
        .page-title {
            font-size: 1.1rem;
            font-weight: 600;
        }
        
        .page-subtitle {
            color: var(--text-muted);
            font-size: 0.75rem;
            margin-top: 2px;
        }
        
        /* Filter Panel */
        .filter-panel {
            background: var(--card-bg);
            border-radius: 8px;
            border: 1px solid var(--border);
            padding: 10px 14px;
            margin-bottom: 10px;
        }
        
        .filter-row {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: flex-end;
        }
        
        .filter-group {
            display: flex;
            flex-direction: column;
            gap: 3px;
        }
        
        .filter-group label {
            font-size: 0.65rem;
            color: var(--text-muted);
            font-weight: 500;
        }
        
        .filter-group select,
        .filter-group input {
            padding: 6px 10px;
            border-radius: 6px;
            border: 1px solid var(--border);
            background: var(--dark);
            color: var(--text);
            font-size: 0.8rem;
            min-width: 140px;
        }
        
        .filter-group select:focus,
        .filter-group input:focus {
            outline: none;
            border-color: var(--primary);
        }
        
        .btn {
            padding: 6px 12px;
            border-radius: 6px;
            border: none;
            cursor: pointer;
            font-size: 0.8rem;
            font-weight: 600;
            transition: all 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }
        
        .btn-primary {
            background: var(--primary);
            color: #fff;
        }
        
        .btn-primary:hover {
            background: #2980b9;
            transform: translateY(-1px);
        }
        
        .btn-success {
            background: var(--success);
            color: #fff;
        }
        
        .btn-success:hover {
            background: #1e8449;
        }
        
        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 8px;
            margin-bottom: 10px;
        }
        
        .stat-card {
            background: var(--card-bg);
            border-radius: 8px;
            border: 1px solid var(--border);
            padding: 8px 10px;
            text-align: center;
        }
        
        .stat-label {
            font-size: 0.65rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 2px;
        }
        
        .stat-value {
            font-size: 1.3rem;
            font-weight: 700;
        }
        
        .stat-value.danger { color: var(--danger); }
        .stat-value.success { color: var(--success); }
        .stat-value.warning { color: var(--warning); }
        .stat-value.primary { color: var(--primary); }
        
        /* Cards Layout */
        .content-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
        }
        
        @media (max-width: 1200px) {
            .content-grid {
                grid-template-columns: 1fr;
            }
        }
        
        .card {
            background: var(--card-bg);
            border-radius: 8px;
            border: 1px solid var(--border);
            overflow: hidden;
        }
        
        .card-header {
            padding: 10px 14px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .card-title {
            font-size: 0.85rem;
            font-weight: 600;
        }
        
        .card-body {
            padding: 10px;
            max-height: 350px;
            overflow-y: auto;
        }
        
        /* Table Styles */
        .data-table {
            width: 100%;
            border-collapse: collapse;
        }
        
        .data-table th,
        .data-table td {
            padding: 6px 8px;
            text-align: left;
            border-bottom: 1px solid var(--border);
            font-size: 0.75rem;
        }
        
        .data-table th {
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.65rem;
            letter-spacing: 0.5px;
        }
        
        .data-table tbody tr:hover {
            background: rgba(255,255,255,0.02);
        }
        
        .badge {
            display: inline-block;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.65rem;
            font-weight: 600;
        }
        
        .badge-danger {
            background: rgba(231, 76, 60, 0.15);
            color: var(--danger);
        }
        
        .badge-success {
            background: rgba(39, 174, 96, 0.15);
            color: var(--success);
        }
        
        .badge-warning {
            background: rgba(243, 156, 18, 0.15);
            color: var(--warning);
        }
        
        /* Industrial Health Cards */
        .health-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
            gap: 8px;
        }
        
        .health-card {
            background: var(--dark);
            border-radius: 6px;
            border: 2px solid var(--border);
            overflow: hidden;
            transition: all 0.3s;
        }
        
        .health-card.critical {
            border-color: var(--danger);
            animation: pulse-critical 1.5s infinite;
        }
        
        .health-card.attention {
            border-color: var(--warning);
        }
        
        .health-card.normal {
            border-color: var(--success);
        }
        
        @keyframes pulse-critical {
            0%, 100% { box-shadow: 0 0 8px rgba(231, 76, 60, 0.3); }
            50% { box-shadow: 0 0 15px rgba(231, 76, 60, 0.5); }
        }
        
        .health-header {
            padding: 6px 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: rgba(0,0,0,0.3);
        }
        
        .health-name {
            font-weight: 600;
            font-size: 0.75rem;
        }
        
        .health-level {
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 0.55rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .health-level.critical {
            background: var(--danger);
            color: white;
        }
        
        .health-level.attention {
            background: var(--warning);
            color: #1a1a2e;
        }
        
        .health-level.normal {
            background: var(--success);
            color: white;
        }
        
        .health-body {
            padding: 8px;
        }
        
        .health-status-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
            padding-bottom: 6px;
            border-bottom: 1px solid var(--border);
        }
        
        .health-status-label {
            font-size: 0.65rem;
            color: var(--text-muted);
        }
        
        .health-status-value {
            font-weight: 600;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.65rem;
        }
        
        .health-status-value.running {
            background: rgba(39, 174, 96, 0.15);
            color: var(--success);
        }
        
        .health-status-value.ready {
            background: rgba(243, 156, 18, 0.15);
            color: var(--warning);
        }
        
        .health-status-value.trip {
            background: rgba(231, 76, 60, 0.15);
            color: var(--danger);
        }
        
        .health-status-value.offline {
            background: rgba(160, 174, 192, 0.15);
            color: var(--text-muted);
        }
        
        .health-metrics {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 6px;
            margin-bottom: 6px;
        }
        
        .health-metric {
            text-align: center;
            padding: 6px;
            background: rgba(0,0,0,0.2);
            border-radius: 4px;
        }
        
        .health-metric-value {
            font-size: 1rem;
            font-weight: 700;
            color: var(--primary);
        }
        
        .health-metric-label {
            font-size: 0.55rem;
            color: var(--text-muted);
            margin-top: 2px;
        }
        
        .health-footer {
            background: rgba(0,0,0,0.2);
            padding: 5px 10px;
            display: flex;
            justify-content: space-between;
            font-size: 0.6rem;
            color: var(--text-muted);
        }
        
        .health-footer.has-trips {
            background: rgba(231, 76, 60, 0.1);
            color: var(--danger);
        }
        
        .trip-count-badge {
            background: var(--danger);
            color: white;
            padding: 1px 5px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.6rem;
        }
        
        .no-data {
            text-align: center;
            padding: 20px;
            color: var(--text-muted);
            font-size: 0.8rem;
        }
        
        .loading {
            text-align: center;
            padding: 12px;
            color: var(--text-muted);
            font-size: 0.8rem;
        }
        
        /* Pump Health Cards */
        .pump-health-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
            gap: 8px;
        }
        
        .health-status.running {
            background: rgba(39, 174, 96, 0.15);
            color: var(--success);
        }
        
        .health-status.ready {
            background: rgba(243, 156, 18, 0.15);
            color: var(--warning);
        }
        
        .health-status.trip {
            background: rgba(231, 76, 60, 0.15);
            color: var(--danger);
        }
        
        .health-stats {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 6px;
        }
        
        .health-stat {
            text-align: center;
            padding: 6px;
            background: rgba(0,0,0,0.2);
            border-radius: 4px;
        }
        
        .health-stat-value {
            font-size: 0.9rem;
            font-weight: 700;
            color: var(--primary);
        }
        
        .health-stat-label {
            font-size: 0.55rem;
            color: var(--text-muted);
            margin-top: 1px;
        }
        
        .health-trips {
            margin-top: 6px;
            padding: 6px;
            background: rgba(231, 76, 60, 0.05);
            border-radius: 4px;
            display: flex;
            justify-content: space-between;
            font-size: 0.65rem;
        }
        
        .health-trips.has-trips {
            background: rgba(231, 76, 60, 0.15);
        }
        
        /* Responsive */
        @media (max-width: 768px) {
            .nav-container {
                flex-wrap: wrap;
                height: auto;
                padding: 12px 0;
                gap: 12px;
            }
            
            .nav-links {
                order: 3;
                width: 100%;
                justify-content: center;
            }
            
            .filter-row {
                flex-direction: column;
            }
            
            .filter-group {
                width: 100%;
            }
            
            .filter-group select,
            .filter-group input {
                width: 100%;
            }
            
            .stats-grid {
                grid-template-columns: repeat(2, 1fr);
            }
            
            .content-grid {
                grid-template-columns: 1fr;
            }
        }
        
        .footer {
            text-align: center;
            padding: 20px;
            color: var(--text-muted);
            font-size: 0.8rem;
            border-top: 1px solid var(--border);
            margin-top: 40px;
        }
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="nav-container">
            <div class="nav-brand">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="3"/>
                    <path d="M12 1v4M12 19v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M1 12h4M19 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83"/>
                </svg>
                Challawa Monitoring
            </div>
            <div class="nav-links">
                <a href="/" class="nav-link">Dashboard</a>
                <a href="/reports" class="nav-link active">Reports</a>
            </div>
        </div>
    </nav>
    
    <main class="main-content">
        <div class="page-header">
            <h1 class="page-title">Reports & Analytics</h1>
            <p class="page-subtitle">View trip events, pump health, and generate reports</p>
        </div>
        
        <div class="filter-panel">
            <div class="filter-row">
                <div class="filter-group">
                    <label>Pump</label>
                    <select id="pumpSelect">
                        <option value="all">All Pumps</option>
                        <option value="1">Line 7 Blow Mould</option>
                        <option value="2">Line 7 Blow Mould Spare</option>
                        <option value="3">Greenfield LV/UPS/BRFC/ACU</option>
                        <option value="4">Can Line UPS Room AHU2</option>
                        <option value="5">Line 3&5 UPS Fan Coil Units</option>
                        <option value="6">Can Line UPS Room AHU 1</option>
                        <option value="7">Greenfield LV/UPS Room AHU 1&2</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>Start Date</label>
                    <input type="date" id="startDate">
                </div>
                <div class="filter-group">
                    <label>End Date</label>
                    <input type="date" id="endDate">
                </div>
                <button class="btn btn-primary" onclick="queryData()">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>
                    </svg>
                    Query
                </button>
                <button class="btn btn-success" onclick="downloadPDF()">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/>
                    </svg>
                    Export PDF
                </button>
            </div>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Total Trips</div>
                <div class="stat-value danger" id="totalTrips">--</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Critical</div>
                <div class="stat-value danger" id="criticalCount">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Running</div>
                <div class="stat-value success" id="runningCount">--</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Standby</div>
                <div class="stat-value warning" id="readyCount">--</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Uptime</div>
                <div class="stat-value primary" id="uptime">--</div>
            </div>
        </div>
        
        <div class="content-grid">
            <div class="card">
                <div class="card-header">
                    <h2 class="card-title">Trip Events Log</h2>
                </div>
                <div class="card-body" id="tripEventsContainer">
                    <p class="no-data">Click "Query" to load trip events</p>
                </div>
            </div>
            
            <div class="card">
                <div class="card-header">
                    <h2 class="card-title">Real-Time Pump Health</h2>
                </div>
                <div class="card-body">
                    <div class="pump-health-grid" id="healthGrid">
                        <p class="loading">Loading...</p>
                    </div>
                </div>
            </div>
        </div>
    </main>
    
    <footer class="footer">
        Challawa Monitoring System &copy; 2026 | Real-time PLC Data
    </footer>
    
    <script>
        // Set default dates
        const today = new Date();
        const lastWeek = new Date(today);
        lastWeek.setDate(lastWeek.getDate() - 7);
        document.getElementById('endDate').value = today.toISOString().split('T')[0];
        document.getElementById('startDate').value = lastWeek.toISOString().split('T')[0];
        
        // Load data on start
        loadHealth();
        queryData();
        
        function queryData() {
            const pump = document.getElementById('pumpSelect').value;
            const start = document.getElementById('startDate').value;
            const end = document.getElementById('endDate').value;
            
            document.getElementById('tripEventsContainer').innerHTML = '<p class="loading">Loading...</p>';
            
            fetch(`/api/trip-events?pump_id=${pump}&start_date=${start}&end_date=${end}`)
                .then(r => r.json())
                .then(data => {
                    document.getElementById('totalTrips').textContent = data.total_trips || 0;
                    renderEvents(data.events);
                })
                .catch(() => {
                    document.getElementById('tripEventsContainer').innerHTML = '<p class="no-data">Error loading data</p>';
                });
        }
        
        function renderEvents(events) {
            const container = document.getElementById('tripEventsContainer');
            if (!events || events.length === 0) {
                container.innerHTML = '<p class="no-data">No trip events found</p>';
                return;
            }
            
            let html = `<table class="data-table">
                <thead><tr><th>Time</th><th>Pump</th><th>Event</th><th>Pressure</th></tr></thead>
                <tbody>`;
            
            events.forEach(e => {
                const badge = e.event_type === 'TRIP' ? 'badge-danger' : 'badge-success';
                html += `<tr>
                    <td>${e.timestamp}</td>
                    <td>${e.pump_name}</td>
                    <td><span class="badge ${badge}">${e.event_type}</span></td>
                    <td>${e.pressure ? e.pressure.toFixed(2) : '--'} bar</td>
                </tr>`;
            });
            
            html += '</tbody></table>';
            container.innerHTML = html;
        }
        
        function loadHealth() {
            fetch('/api/pump-health')
                .then(r => r.json())
                .then(data => {
                    renderHealth(data.pumps);
                    document.getElementById('uptime').textContent = data.uptime || '--';
                    
                    let running = 0, ready = 0, critical = 0;
                    if (data.pumps) {
                        data.pumps.forEach(p => {
                            if (p.is_trip) critical++;
                            else if (p.is_running) running++;
                            else if (p.is_ready) ready++;
                        });
                    }
                    document.getElementById('runningCount').textContent = running;
                    document.getElementById('readyCount').textContent = ready;
                    document.getElementById('criticalCount').textContent = critical;
                })
                .catch(() => {
                    document.getElementById('healthGrid').innerHTML = '<p class="no-data">Error loading health data</p>';
                });
        }
        
        function renderHealth(pumps) {
            const container = document.getElementById('healthGrid');
            if (!pumps || pumps.length === 0) {
                container.innerHTML = '<p class="no-data">No data available</p>';
                return;
            }
            
            let html = '';
            pumps.forEach(p => {
                // Determine health level (industrial standard)
                let healthLevel = 'normal';
                let healthText = 'NORMAL';
                let statusClass = 'offline';
                let statusText = 'OFFLINE';
                
                if (p.is_trip) {
                    healthLevel = 'critical';
                    healthText = 'CRITICAL';
                    statusClass = 'trip';
                    statusText = 'TRIP';
                } else if (p.is_running) {
                    healthLevel = 'normal';
                    healthText = 'NORMAL';
                    statusClass = 'running';
                    statusText = 'RUNNING';
                } else if (p.is_ready) {
                    healthLevel = 'attention';
                    healthText = 'ATTENTION';
                    statusClass = 'ready';
                    statusText = 'STANDBY';
                } else {
                    healthLevel = 'attention';
                    healthText = 'OFFLINE';
                }
                
                const hasTrips = p.trip_count_24h > 0;
                const footerClass = hasTrips ? 'has-trips' : '';
                
                html += `<div class="health-card ${healthLevel}">
                    <div class="health-header">
                        <span class="health-name">${p.name}</span>
                        <span class="health-level ${healthLevel}">${healthText}</span>
                    </div>
                    <div class="health-body">
                        <div class="health-status-row">
                            <span class="health-status-label">Current Status</span>
                            <span class="health-status-value ${statusClass}">${statusText}</span>
                        </div>
                        <div class="health-metrics">
                            <div class="health-metric">
                                <div class="health-metric-value">${p.pressure ? p.pressure.toFixed(2) : '--'}</div>
                                <div class="health-metric-label">Pressure (bar)</div>
                            </div>
                            <div class="health-metric">
                                <div class="health-metric-value">${p.setpoint ? p.setpoint.toFixed(2) : '--'}</div>
                                <div class="health-metric-label">Setpoint (bar)</div>
                            </div>
                        </div>
                    </div>
                    <div class="health-footer ${footerClass}">
                        <span>Trips (24h): ${hasTrips ? '<span class="trip-count-badge">' + p.trip_count_24h + '</span>' : '0'}</span>
                        <span>Last Trip: ${p.last_trip ? p.last_trip.split(' ')[0] : 'None'}</span>
                    </div>
                </div>`;
            });
            
            container.innerHTML = html;
        }
        
        function downloadPDF() {
            const pump = document.getElementById('pumpSelect').value;
            const start = document.getElementById('startDate').value;
            const end = document.getElementById('endDate').value;
            window.location.href = `/api/generate-pdf?pump_id=${pump}&start_date=${start}&end_date=${end}`;
        }
        
        // Refresh health every 5 seconds
        setInterval(loadHealth, 5000);
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    # Create templates directory and save HTML
    import os
    os.makedirs('templates', exist_ok=True)
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write(HTML_TEMPLATE)
    with open('templates/reports.html', 'w', encoding='utf-8') as f:
        f.write(REPORTS_TEMPLATE)
    
    # Initialize database
    init_database()
    
    # Start monitoring
    monitor.start()

    try:
        print("\n" + "=" * 50)
        print("  Challawa Monitoring System")
        print("=" * 50)
        print(f"  PLC: {PLC_IP}")
        print("  Local Dashboard: http://127.0.0.1:5000")
        print("  Public Dashboard: https://challawaop.akfotekengineering.com")
        print("=" * 50 + "\n")

        socketio.run(
            app,
            host="0.0.0.0",
            port=5000,
            debug=False,
            allow_unsafe_werkzeug=True
        )

    except KeyboardInterrupt:
        print("\nShutting down...")
        monitor.stop()

