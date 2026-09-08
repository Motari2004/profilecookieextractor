from flask import Flask, jsonify, request, render_template_string, send_file
from playwright.sync_api import sync_playwright
import logging
import os
import json
import time
import requests
import re
from datetime import datetime
import threading
import queue

# ✅ Import pyotp for 2FA
try:
    import pyotp
    PYOTP_AVAILABLE = True
except ImportError:
    PYOTP_AVAILABLE = False
    print("⚠️ pyotp not installed. Run: pip install pyotp")

from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================
# ENVIRONMENT VARIABLES
# ============================================

USERNAME = os.environ.get('INSTAGRAM_USERNAME', '')
PASSWORD = os.environ.get('INSTAGRAM_PASSWORD', '')
BROWSERLESS_TOKEN = os.environ.get('BROWSERLESS_API_KEY', '')
PROFILE_NAME = os.environ.get('PROFILE_NAME', 'instagram-login')
BROWSERLESS_ORIGIN = os.environ.get('BROWSERLESS_ORIGIN', 'wss://production-sfo.browserless.io')
VERCEL_WEBHOOK_URL = os.environ.get('VERCEL_WEBHOOK_URL', 'https://fetchgram-one.vercel.app/api/cookies/sync')

# ✅ 2FA Secret Key (remove spaces)
TWOFA_SECRET = os.environ.get('INSTAGRAM_2FA_SECRET', '')
if TWOFA_SECRET:
    TWOFA_SECRET = re.sub(r'\s+', '', TWOFA_SECRET)
    logger.info(f"✅ 2FA Secret configured (length: {len(TWOFA_SECRET)})")
else:
    logger.warning("⚠️ INSTAGRAM_2FA_SECRET not set - 2FA will require manual input")

# ============================================
# 2FA FUNCTIONS
# ============================================

def generate_2fa_code():
    """Generate current 2FA code using pyotp"""
    if not PYOTP_AVAILABLE:
        logger.error("❌ pyotp not installed")
        return None
    
    if not TWOFA_SECRET:
        logger.warning("⚠️ No 2FA secret configured")
        return None
    
    try:
        totp = pyotp.TOTP(TWOFA_SECRET, interval=30, digits=6)
        code = totp.now()
        remaining = totp.interval - (int(time.time()) % totp.interval)
        logger.info(f"✅ Generated 2FA code: {code} (expires in {remaining}s)")
        return code
    except Exception as e:
        logger.error(f"❌ Failed to generate 2FA: {e}")
        return None

# ============================================
# 2FA QUEUE (for manual fallback)
# ============================================

twofa_queue = queue.Queue()

# Store login status
login_status = {
    "in_progress": False,
    "completed": False,
    "result": None,
    "start_time": None,
    "end_time": None,
    "error": None,
    "awaiting_2fa": False
}

# ============================================
# HTML UI
# ============================================

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Instagram Login Bot</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
            background: #fafafa;
            padding: 20px;
        }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { color: #262626; margin-bottom: 20px; font-weight: 300; }
        
        .controls {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        .form-group {
            margin-bottom: 15px;
        }
        .form-group label {
            display: block;
            font-size: 14px;
            font-weight: 600;
            color: #262626;
            margin-bottom: 5px;
        }
        .form-group input {
            width: 100%;
            padding: 10px;
            border: 1px solid #dbdbdb;
            border-radius: 4px;
            font-size: 14px;
        }
        .form-group input:focus {
            border-color: #0095f6;
            outline: none;
        }
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
        }
        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            font-size: 14px;
            cursor: pointer;
            font-weight: 600;
        }
        .btn-primary { background: #0095f6; color: white; width: 100%; }
        .btn-primary:hover { background: #0077cc; }
        .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-success { background: #28a745; color: white; }
        .btn-success:hover { background: #218838; }
        .btn-secondary { background: #dbdbdb; color: #262626; }
        .btn-secondary:hover { background: #c4c4c4; }
        
        .status {
            padding: 12px 16px;
            border-radius: 4px;
            font-size: 14px;
            font-weight: 500;
            margin-top: 15px;
        }
        .status-idle { background: #efefef; color: #8e8e8e; }
        .status-running { background: #fff3cd; color: #856404; animation: pulse 1s infinite; }
        .status-success { background: #d4edda; color: #155724; }
        .status-error { background: #f8d7da; color: #721c24; }
        .status-2fa { background: #cce5ff; color: #004085; animation: pulse 1s infinite; }
        .status-auto-2fa { background: #d1ecf1; color: #0c5460; animation: pulse 1s infinite; }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
        }
        
        .info-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-top: 20px;
        }
        .info-card {
            background: white;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .info-card .label {
            font-size: 12px;
            color: #8e8e8e;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .info-card .value {
            font-size: 16px;
            font-weight: 600;
            color: #262626;
            margin-top: 5px;
            word-break: break-all;
        }
        
        .log-container {
            background: #1e1e1e;
            color: #d4d4d4;
            padding: 15px;
            border-radius: 8px;
            margin-top: 20px;
            max-height: 300px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            line-height: 1.6;
        }
        .log-entry .time { color: #569cd6; margin-right: 10px; }
        .log-entry .info { color: #4ec9b0; }
        .log-entry .error { color: #f44747; }
        .log-entry .success { color: #4ec9b0; }
        .log-entry .warning { color: #dcdcaa; }
        .log-entry .highlight { color: #dcdcaa; font-weight: bold; }
        .log-entry .auto-2fa { color: #17a2b8; font-weight: bold; }
        
        .error-box {
            background: #f8d7da;
            color: #721c24;
            padding: 15px;
            border-radius: 4px;
            margin-top: 15px;
            border: 1px solid #f5c6cb;
        }
        .error-box strong { display: block; margin-bottom: 5px; }
        
        .twofa-box {
            background: #cce5ff;
            color: #004085;
            padding: 15px;
            border-radius: 4px;
            margin-top: 15px;
            border: 1px solid #b8daff;
            display: none;
        }
        .twofa-box strong { display: block; margin-bottom: 10px; }
        .twofa-box .form-group { margin-bottom: 10px; }
        .twofa-box .form-group input { 
            font-size: 24px; 
            letter-spacing: 5px;
            text-align: center;
            max-width: 200px;
        }
        
        .auto-2fa-badge {
            display: inline-block;
            background: #17a2b8;
            color: white;
            padding: 2px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            margin-left: 10px;
        }
        
        @media (max-width: 600px) {
            .form-row { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📸 Instagram Login Bot <span class="auto-2fa-badge">🤖 AUTO 2FA</span></h1>
        
        <div class="controls">
            <div class="form-row">
                <div class="form-group">
                    <label>Username</label>
                    <input type="text" id="username" value="{{ username }}" readonly>
                </div>
                <div class="form-group">
                    <label>Password</label>
                    <input type="password" id="password" placeholder="Enter your Instagram password">
                </div>
            </div>
            <button class="btn btn-primary" onclick="startLogin()" id="loginBtn">🚀 Start Login</button>
            
            <div id="twofaBox" class="twofa-box">
                <strong>🔐 2FA Required</strong>
                <p>Please enter your 6-digit authentication code (codes expire every 30 seconds):</p>
                <div class="form-group">
                    <input type="text" id="twofaInput" placeholder="Enter 6-digit code" maxlength="6" inputmode="numeric" pattern="[0-9]*">
                </div>
                <button class="btn btn-success" onclick="submit2FA()" id="twofaBtn">✅ Submit 2FA Code</button>
            </div>
            
            <div id="status" class="status status-idle">⏸ Ready</div>
        </div>
        
        <div class="info-grid">
            <div class="info-card">
                <div class="label">Browser</div>
                <div class="value">🔗 Browserless</div>
            </div>
            <div class="info-card">
                <div class="label">Status</div>
                <div class="value" id="loginStatusText">Not logged in</div>
            </div>
            <div class="info-card">
                <div class="label">Session</div>
                <div class="value" id="sessionStatus">-</div>
            </div>
            <div class="info-card">
                <div class="label">2FA</div>
                <div class="value" id="twofaStatus">🤖 Auto</div>
            </div>
        </div>
        
        <div id="errorBox" style="display:none;"></div>
        
        <div id="logContainer" class="log-container">
            <div class="log-entry"><span class="time">[System]</span><span class="info">Ready. Enter your password and click "Start Login".</span></div>
            <div class="log-entry"><span class="time">[System]</span><span class="auto-2fa">🤖 Auto 2FA is enabled</span></div>
        </div>
        
        <div style="margin-top: 10px; display: flex; gap: 10px;">
            <button class="btn btn-secondary" onclick="refreshStatus()" style="flex:1;">🔄 Refresh</button>
            <button class="btn btn-secondary" onclick="clearSession()" style="flex:1;">🗑️ Clear Session</button>
        </div>
    </div>

    <script>
        let isRefreshing = false;
        let checkInterval = null;
        
        async function startLogin() {
            const btn = document.getElementById('loginBtn');
            const password = document.getElementById('password').value;
            const username = document.getElementById('username').value;
            
            if (!password) {
                alert('Please enter your password');
                return;
            }
            
            btn.disabled = true;
            btn.textContent = '⏳ Logging in...';
            hideError();
            hide2FA();
            
            try {
                const response = await fetch('/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: username,
                        password: password
                    })
                });
                const data = await response.json();
                if (data.success) {
                    updateStatus('running', '🔄 Login in progress...');
                    addLog('Login started successfully', 'info');
                    if (checkInterval) clearInterval(checkInterval);
                    checkInterval = setInterval(checkFor2FA, 2000);
                } else {
                    updateStatus('error', '❌ Failed: ' + (data.message || data.error || 'Unknown error'));
                    addLog('Error: ' + (data.message || data.error || 'Unknown error'), 'error');
                    showError(data.message || data.error || 'Unknown error');
                }
            } catch (error) {
                updateStatus('error', '❌ Error: ' + error.message);
                addLog('Error: ' + error.message, 'error');
                showError(error.message);
            }
            
            btn.disabled = false;
            btn.textContent = '🚀 Start Login';
            setTimeout(refreshStatus, 2000);
        }
        
        async function checkFor2FA() {
            try {
                const response = await fetch('/status');
                const data = await response.json();
                
                if (data.login_status && data.login_status.awaiting_2fa) {
                    clearInterval(checkInterval);
                    show2FA();
                    updateStatus('2fa', '🔐 2FA Required - Auto-submitting...');
                    addLog('🔐 2FA page detected! Auto-submitting 2FA code...', 'auto-2fa');
                    
                    // Auto-submit after 2 seconds
                    setTimeout(async () => {
                        await autoSubmit2FA();
                    }, 2000);
                }
            } catch (error) {
                console.error('Check for 2FA error:', error);
            }
        }
        
        async function autoSubmit2FA() {
            try {
                const response = await fetch('/auto-2fa', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                const data = await response.json();
                
                if (data.success) {
                    hide2FA();
                    updateStatus('running', '🔄 2FA auto-submitted, waiting for response...');
                    addLog('✅ 2FA code auto-submitted', 'success');
                    document.getElementById('twofaStatus').textContent = '✅ Auto';
                    
                    // Check result after 2FA
                    setTimeout(async () => {
                        const result = await fetch('/result');
                        const resultData = await result.json();
                        if (resultData.success) {
                            updateStatus('success', '✅ Login successful!');
                            addLog('✅ Login completed successfully!', 'success');
                            document.getElementById('loginStatusText').textContent = '✅ Logged in';
                        } else {
                            updateStatus('error', '❌ Login failed after 2FA');
                            addLog('❌ Login failed after 2FA: ' + (resultData.error || 'Unknown error'), 'error');
                        }
                        refreshStatus();
                    }, 10000);
                } else {
                    updateStatus('error', '❌ Auto 2FA failed: ' + (data.error || 'Unknown error'));
                    addLog('❌ Auto 2FA failed: ' + (data.error || 'Unknown error'), 'error');
                    showError(data.error || 'Auto 2FA failed');
                }
            } catch (error) {
                updateStatus('error', '❌ Error: ' + error.message);
                addLog('Error: ' + error.message, 'error');
                showError(error.message);
            }
        }
        
        async function submit2FA() {
            const code = document.getElementById('twofaInput').value.trim();
            
            if (!code || code.length < 6) {
                alert('Please enter a valid 6-digit code');
                return;
            }
            
            const btn = document.getElementById('twofaBtn');
            btn.disabled = true;
            btn.textContent = '⏳ Submitting...';
            
            try {
                const response = await fetch('/submit_2fa', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ code: code })
                });
                const data = await response.json();
                if (data.success) {
                    hide2FA();
                    updateStatus('running', '🔄 2FA submitted, waiting for response...');
                    addLog('✅ 2FA code submitted', 'success');
                    document.getElementById('twofaStatus').textContent = '✅ Manual';
                    
                    setTimeout(async () => {
                        const result = await fetch('/result');
                        const resultData = await result.json();
                        if (resultData.success) {
                            updateStatus('success', '✅ Login successful!');
                            addLog('✅ Login completed successfully!', 'success');
                            document.getElementById('loginStatusText').textContent = '✅ Logged in';
                        } else {
                            updateStatus('error', '❌ Login failed after 2FA');
                            addLog('❌ Login failed after 2FA: ' + (resultData.error || 'Unknown error'), 'error');
                        }
                        refreshStatus();
                    }, 10000);
                } else {
                    updateStatus('error', '❌ 2FA submission failed');
                    addLog('❌ 2FA submission failed: ' + (data.error || 'Unknown error'), 'error');
                    showError(data.error || '2FA submission failed');
                }
            } catch (error) {
                updateStatus('error', '❌ Error: ' + error.message);
                addLog('Error: ' + error.message, 'error');
                showError(error.message);
            }
            
            btn.disabled = false;
            btn.textContent = '✅ Submit 2FA Code';
        }
        
        function show2FA() {
            document.getElementById('twofaBox').style.display = 'block';
            document.getElementById('twofaInput').focus();
        }
        
        function hide2FA() {
            document.getElementById('twofaBox').style.display = 'none';
            document.getElementById('twofaInput').value = '';
        }
        
        async function refreshStatus() {
            if (isRefreshing) return;
            isRefreshing = true;
            
            try {
                const response = await fetch('/status');
                const data = await response.json();
                
                document.getElementById('loginStatusText').textContent = data.logged_in ? '✅ Logged in' : '❌ Not logged in';
                document.getElementById('sessionStatus').textContent = data.session_exists ? '✅ Exists' : '❌ None';
                
                if (data.login_status && data.login_status.in_progress) {
                    updateStatus('running', '🔄 Login in progress...');
                } else if (data.login_status && data.login_status.completed) {
                    const result = await fetch('/result');
                    const resultData = await result.json();
                    if (resultData.success) {
                        updateStatus('success', '✅ Login successful!');
                        addLog('✅ Login completed successfully!', 'success');
                        document.getElementById('loginStatusText').textContent = '✅ Logged in';
                    } else {
                        updateStatus('error', '❌ Login failed');
                        addLog('❌ Login failed: ' + (resultData.error || 'Unknown error'), 'error');
                        showError(resultData.error || 'Login failed');
                    }
                } else if (data.logged_in) {
                    updateStatus('success', '✅ Already logged in');
                } else {
                    updateStatus('idle', '⏸ Idle');
                }
            } catch (error) {
                console.error('Refresh error:', error);
            }
            
            isRefreshing = false;
        }
        
        async function clearSession() {
            if (!confirm('Clear saved session?')) return;
            try {
                const response = await fetch('/clear_session', { method: 'POST' });
                const data = await response.json();
                if (data.success) {
                    addLog('✅ Session cleared', 'success');
                    document.getElementById('sessionStatus').textContent = '❌ None';
                }
            } catch (error) {
                addLog('Error clearing session: ' + error.message, 'error');
            }
        }
        
        function updateStatus(type, message) {
            const el = document.getElementById('status');
            el.className = 'status status-' + type;
            el.textContent = message;
        }
        
        function addLog(message, level = 'info') {
            const container = document.getElementById('logContainer');
            const time = new Date().toLocaleTimeString();
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            entry.innerHTML = `<span class="time">[${time}]</span><span class="${level}">${message}</span>`;
            container.appendChild(entry);
            container.scrollTop = container.scrollHeight;
            
            while (container.children.length > 100) {
                container.removeChild(container.firstChild);
            }
        }
        
        function showError(message) {
            const box = document.getElementById('errorBox');
            box.style.display = 'block';
            box.className = 'error-box';
            box.innerHTML = `<strong>❌ Error:</strong> ${message}`;
        }
        
        function hideError() {
            document.getElementById('errorBox').style.display = 'none';
        }
        
        setInterval(refreshStatus, 3000);
        setTimeout(refreshStatus, 500);
    </script>
</body>
</html>
'''

# ============================================
# ROUTES
# ============================================

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, username=USERNAME)

@app.route('/login', methods=['POST'])
def login():
    global login_status, twofa_queue
    
    if login_status.get("in_progress"):
        return jsonify({"success": False, "message": "Login already in progress"})
    
    data = request.json or {}
    username = data.get('username', USERNAME)
    password = data.get('password')
    
    if not username:
        return jsonify({"success": False, "error": "Username required"}), 400
    
    if not password:
        return jsonify({"success": False, "error": "Password required"}), 400
    
    if not BROWSERLESS_TOKEN:
        return jsonify({"success": False, "error": "BROWSERLESS_API_KEY not configured"}), 400
    
    # Clear old queue items
    while not twofa_queue.empty():
        try:
            twofa_queue.get_nowait()
        except:
            pass
    
    login_status = {
        "in_progress": True,
        "completed": False,
        "result": None,
        "start_time": datetime.now().isoformat(),
        "end_time": None,
        "error": None,
        "awaiting_2fa": False
    }
    
    thread = threading.Thread(target=run_login_background, args=(username, password))
    thread.daemon = True
    thread.start()
    
    return jsonify({"success": True, "message": "Login started"})

@app.route('/status')
def status():
    try:
        session_exists = os.path.exists('session.json')
        session_data = None
        if session_exists:
            try:
                with open('session.json', 'r') as f:
                    session_data = json.load(f)
            except:
                pass
        
        return jsonify({
            "logged_in": session_exists,
            "session_exists": session_exists,
            "session_user": session_data.get('username') if session_data else None,
            "session_time": session_data.get('timestamp') if session_data else None,
            "session_url": session_data.get('url') if session_data else None,
            "session_indicators": session_data.get('indicators') if session_data else None,
            "username": USERNAME,
            "login_status": {
                "in_progress": login_status.get("in_progress", False),
                "completed": login_status.get("completed", False),
                "awaiting_2fa": login_status.get("awaiting_2fa", False),
                "start_time": login_status.get("start_time"),
                "end_time": login_status.get("end_time"),
                "error": login_status.get("error")
            }
        })
    except Exception as e:
        logger.error(f"Status error: {e}")
        return jsonify({
            "logged_in": False,
            "session_exists": False,
            "username": USERNAME,
            "login_status": {
                "in_progress": False,
                "completed": False,
                "awaiting_2fa": False
            },
            "error": str(e)
        })

@app.route('/result')
def get_result():
    if login_status.get("completed"):
        return jsonify(login_status.get("result", {"success": False, "error": "No result"}))
    return jsonify({"success": False, "message": "Login not completed yet"})

@app.route('/auto-2fa', methods=['POST'])
def auto_2fa():
    """Auto-generate and submit 2FA code"""
    logger.info("🤖 Auto 2FA triggered")
    
    # Generate 2FA code
    code = generate_2fa_code()
    
    if not code:
        return jsonify({"success": False, "error": "Failed to generate 2FA code"}), 500
    
    # Put the code in the queue for the login thread
    try:
        twofa_queue.put(code)
        logger.info(f"✅ 2FA code {code} added to queue")
        return jsonify({"success": True, "message": "2FA code auto-generated and submitted"})
    except Exception as e:
        logger.error(f"❌ Failed to submit auto 2FA: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/submit_2fa', methods=['POST'])
def submit_2fa():
    """Submit 2FA code via queue (manual)"""
    global twofa_queue
    
    data = request.json or {}
    code = data.get('code', '')
    
    if not code or len(code) < 6:
        return jsonify({"success": False, "error": "Invalid 2FA code"}), 400
    
    try:
        twofa_queue.put(code)
        logger.info(f"✅ 2FA code added to queue: {code}")
        return jsonify({"success": True, "message": "2FA code received"})
    except Exception as e:
        logger.error(f"2FA submission error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/clear_session', methods=['POST'])
def clear_session():
    """Clear the session.json file"""
    try:
        if os.path.exists('session.json'):
            os.remove('session.json')
            logger.info("✅ Session cleared")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "browserless_configured": bool(BROWSERLESS_TOKEN),
        "profile": PROFILE_NAME,
        "session_exists": os.path.exists('session.json'),
        "2fa_configured": bool(TWOFA_SECRET),
        "2fa_auto": bool(TWOFA_SECRET and PYOTP_AVAILABLE)
    })

@app.route('/debug/screenshot')
def debug_screenshot():
    """Return the last screenshot for debugging"""
    try:
        screenshot_path = '/tmp/login_attempt.png'
        if os.path.exists(screenshot_path):
            return send_file(screenshot_path, mimetype='image/png')
        return jsonify({"error": "No screenshot found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================
# VERCEL API ENDPOINTS
# ============================================

@app.route('/api/extract', methods=['POST'])
def api_extract():
    """API endpoint to extract cookies from existing profile"""
    logger.info("🍪 Extract endpoint called")
    
    try:
        if not os.path.exists('session.json'):
            return jsonify({"success": False, "error": "No session found. Please login first."}), 404
        
        with open('session.json', 'r') as f:
            session_data = json.load(f)
        
        cookies = session_data.get('cookies', [])
        return jsonify({
            "success": True,
            "cookies": cookies,
            "count": len(cookies),
            "username": session_data.get('username')
        })
    except Exception as e:
        logger.error(f"❌ Extract error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    """API endpoint to refresh profile"""
    logger.info("🔄 Refresh endpoint called")
    
    if os.path.exists('session.json'):
        try:
            with open('session.json', 'r') as f:
                session_data = json.load(f)
            cookies = session_data.get('cookies', [])
            if cookies:
                send_cookies_to_vercel(cookies, session_data.get('username'))
                return jsonify({
                    "success": True,
                    "message": "Cookies refreshed from existing session",
                    "cookies": cookies,
                    "count": len(cookies)
                })
        except:
            pass
    
    return jsonify({
        "success": False,
        "error": "No session found. Please login first using the web interface.",
        "login_url": "/"
    }), 404

# ============================================
# BROWSERLESS LOGIN FUNCTION (WITH AUTO 2FA)
# ============================================

def send_cookies_to_vercel(cookies, username=None):
    """Send extracted cookies to Vercel webhook"""
    if not VERCEL_WEBHOOK_URL:
        logger.warning("⚠️ VERCEL_WEBHOOK_URL not set, skipping")
        return
    
    try:
        logger.info(f"📤 Sending {len(cookies)} cookies to Vercel...")
        
        if not username:
            for cookie in cookies:
                if cookie.get('name') == 'ds_user_id':
                    username = cookie.get('value')
                    break
        
        response = requests.post(
            VERCEL_WEBHOOK_URL,
            json={
                "cookies": cookies,
                "username": username or "Instagram User",
                "timestamp": datetime.now().isoformat()
            },
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        if response.status_code == 200:
            logger.info("✅ Cookies sent to Vercel successfully")
        else:
            logger.warning(f"⚠️ Vercel responded with: {response.status_code}")
            
    except Exception as e:
        logger.error(f"❌ Failed to send cookies to Vercel: {e}")

def login_with_browserless(username, password):
    """Login to Instagram using Browserless with DIRECT AUTO 2FA"""
    global login_status, twofa_queue
    
    if not BROWSERLESS_TOKEN:
        return {"success": False, "error": "BROWSERLESS_API_KEY not set"}
    
    try:
        logger.info(f"🚀 Starting login for: {username}")
        logger.info(f"🤖 Auto 2FA: {'Enabled' if TWOFA_SECRET and PYOTP_AVAILABLE else 'Disabled'}")
        
        # Create profile session
        profile_url = f"https://production-sfo.browserless.io/profile?token={BROWSERLESS_TOKEN}"
        profile_response = requests.post(
            profile_url,
            headers={'Content-Type': 'application/json'},
            json={'name': PROFILE_NAME}
        )
        
        if profile_response.status_code != 200:
            return {"success": False, "error": f"Failed to create profile: {profile_response.text}"}
        
        session_data = profile_response.json()
        logger.info(f"✅ Profile session created")
        
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(session_data['connect'])
            logger.info("✅ Connected to Browserless")
            
            try:
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()
                
                # Navigate to Instagram
                logger.info("🌐 Navigating to Instagram...")
                page.goto('https://www.instagram.com/accounts/login/', wait_until='domcontentloaded')
                time.sleep(3)
                
                # Handle cookie banner
                try:
                    allow_cookies = page.query_selector('button:has-text("Allow all cookies")')
                    if allow_cookies:
                        allow_cookies.click()
                        logger.info("✅ Handled cookie banner")
                        time.sleep(1)
                except:
                    pass
                
                # Fill username
                logger.info("📝 Filling username...")
                username_field = page.query_selector('input[name="username"]')
                if username_field and username_field.is_visible():
                    username_field.fill(username)
                    logger.info("✅ Username filled")
                else:
                    inputs = page.query_selector_all('input[type="text"]')
                    if inputs and len(inputs) > 0:
                        inputs[0].fill(username)
                        logger.info("✅ Username filled (by fallback)")
                    else:
                        return {"success": False, "error": "Could not find username field"}
                time.sleep(1)
                
                # Fill password
                logger.info("🔑 Filling password...")
                password_field = page.query_selector('input[name="password"]')
                if password_field and password_field.is_visible():
                    password_field.fill(password)
                    logger.info("✅ Password filled")
                else:
                    password_field = page.query_selector('input[type="password"]')
                    if password_field and password_field.is_visible():
                        password_field.fill(password)
                        logger.info("✅ Password filled (by type)")
                    else:
                        return {"success": False, "error": "Could not find password field"}
                time.sleep(1)
                
                # Submit
                logger.info("📤 Submitting login...")
                submit_button = page.query_selector('button[type="submit"]')
                if submit_button and submit_button.is_visible():
                    submit_button.click()
                    logger.info("✅ Clicked login button")
                else:
                    page.keyboard.press("Enter")
                    logger.info("✅ Pressed Enter")
                
                # Wait for response
                logger.info("⏳ Waiting for response...")
                time.sleep(5)
                page.wait_for_load_state('networkidle', timeout=30000)
                
                # ============================================
                # 🔥 CHECK FOR 2FA INPUT FIELD ON THE PAGE
                # ============================================
                logger.info("🔍 Checking for 2FA input field...")
                
                # ✅ Look for 2FA input field directly
                twofa_input = None
                twofa_selectors = [
                    'input[type="text"]',
                    'input[autocomplete="off"]',
                    'input[placeholder*="code" i]',
                    'input[inputmode="numeric"]',
                    'input[type="tel"]',
                    'input[name="verificationCode"]',
                    'input[aria-label*="code" i]',
                    'input[class*="code" i]',
                    'input[class*="two_factor" i]',
                    'input[class*="verification" i]'
                ]
                
                # Check each selector for a visible input
                for selector in twofa_selectors:
                    try:
                        inputs = page.query_selector_all(selector)
                        for inp in inputs:
                            if inp.is_visible():
                                # Check if it's a 2FA input (usually has a label or placeholder)
                                placeholder = inp.get_attribute('placeholder') or ''
                                aria_label = inp.get_attribute('aria-label') or ''
                                name = inp.get_attribute('name') or ''
                                
                                # Check if it's likely a 2FA input
                                if any(keyword in (placeholder + aria_label + name).lower() for keyword in ['code', 'verification', 'two factor', '2fa']):
                                    twofa_input = inp
                                    logger.info(f"✅ Found 2FA input with selector: {selector}")
                                    break
                                
                                # If no specific 2FA keywords, but it's the only visible text input, use it
                                if not twofa_input:
                                    twofa_input = inp
                                    logger.info(f"✅ Found potential 2FA input: {selector}")
                        if twofa_input:
                            break
                    except:
                        continue
                
                # If still not found, check the URL as fallback
                if not twofa_input:
                    current_url = page.url
                    logger.info(f"📍 Current URL: {current_url}")
                    
                    if "two_step_verification" in current_url or "challenge" in current_url:
                        logger.info("🔐 2FA URL detected, looking for input field...")
                        # Try to find any visible text input
                        all_inputs = page.query_selector_all('input')
                        for inp in all_inputs:
                            if inp.is_visible():
                                twofa_input = inp
                                logger.info("✅ Found input field as fallback")
                                break
                
                # ============================================
                # IF 2FA INPUT FOUND → AUTO-SUBMIT CODE
                # ============================================
                if twofa_input:
                    logger.info("🔐 2FA input detected! Auto-submitting 2FA code...")
                    login_status["awaiting_2fa"] = True
                    
                    # ✅ STEP 1: Generate 2FA code directly
                    twofa_code = None
                    if TWOFA_SECRET and PYOTP_AVAILABLE:
                        twofa_code = generate_2fa_code()
                        logger.info(f"🤖 Generated 2FA code: {twofa_code}")
                    else:
                        logger.error("❌ 2FA secret not configured!")
                        login_status["awaiting_2fa"] = False
                        return {"success": False, "error": "2FA secret not configured"}
                    
                    if not twofa_code:
                        logger.error("❌ Failed to generate 2FA code")
                        login_status["awaiting_2fa"] = False
                        return {"success": False, "error": "Failed to generate 2FA code"}
                    
                    # ✅ STEP 2: Fill the 2FA code
                    logger.info(f"📝 Filling 2FA code: {twofa_code}")
                    twofa_input.click()
                    time.sleep(0.5)
                    
                    # Clear the field
                    twofa_input.fill("")
                    time.sleep(0.3)
                    
                    # Type the code character by character
                    for char in twofa_code:
                        twofa_input.type(char, delay=50)
                        time.sleep(0.05)
                    
                    logger.info(f"✅ Auto-filled 2FA code: {twofa_code}")
                    time.sleep(0.5)
                    
                    # ✅ STEP 3: Submit 2FA
                    logger.info("📤 Submitting 2FA code...")
                    submit_2fa = None
                    
                    # Try different button types
                    button_selectors = [
                        'button[type="submit"]',
                        'button:has-text("Submit")',
                        'button:has-text("Confirm")',
                        'button:has-text("Verify")',
                        'button:has-text("Send")',
                        'button:has-text("Continue")'
                    ]
                    
                    for selector in button_selectors:
                        try:
                            submit_2fa = page.query_selector(selector)
                            if submit_2fa and submit_2fa.is_visible():
                                logger.info(f"✅ Found submit button with: {selector}")
                                break
                        except:
                            continue
                    
                    if submit_2fa and submit_2fa.is_visible():
                        submit_2fa.click()
                        logger.info("✅ 2FA submitted via button")
                    else:
                        page.keyboard.press("Enter")
                        logger.info("✅ 2FA submitted with Enter")
                    
                    # ✅ STEP 4: Wait for verification
                    logger.info("⏳ Waiting for 2FA verification...")
                    time.sleep(5)
                    page.wait_for_load_state('networkidle', timeout=30000)
                    
                    # ✅ STEP 5: Check if 2FA was successful
                    final_url = page.url
                    logger.info(f"📍 After 2FA URL: {final_url}")
                    
                    # Handle post-2FA prompts
                    if "two_step_verification" not in final_url and "challenge" not in final_url:
                        logger.info("✅ 2FA verification successful!")
                        
                        # Check for "Save Info" button
                        try:
                            save_info = page.query_selector('button:has-text("Save Info")')
                            if save_info and save_info.is_visible():
                                save_info.click()
                                logger.info("✅ Clicked 'Save Info'")
                                time.sleep(1)
                        except:
                            pass
                        
                        # Check for "Not Now" button
                        try:
                            not_now = page.query_selector('button:has-text("Not Now")')
                            if not_now and not_now.is_visible():
                                not_now.click()
                                logger.info("✅ Clicked 'Not Now'")
                                time.sleep(1)
                        except:
                            pass
                    else:
                        # Try one more time with Enter
                        logger.info("⏳ Still on 2FA page, trying Enter one more time...")
                        try:
                            page.keyboard.press("Enter")
                            time.sleep(5)
                            page.wait_for_load_state('networkidle', timeout=30000)
                            final_url = page.url
                            logger.info(f"📍 After second try: {final_url}")
                        except:
                            pass
                    
                    login_status["awaiting_2fa"] = False
                
                # Check if login was successful
                final_url = page.url
                logger.info(f"📍 Final URL: {final_url}")
                
                if "login" not in final_url and "two_step" not in final_url:
                    logger.info("🎉 Login successful!")
                    cookies = page.context.cookies()
                    
                    # Save session
                    session_data = {
                        "username": username,
                        "cookies": cookies,
                        "timestamp": datetime.now().isoformat(),
                        "url": final_url
                    }
                    with open('session.json', 'w') as f:
                        json.dump(session_data, f, indent=2)
                    logger.info(f"✅ Session saved with {len(cookies)} cookies")
                    
                    # Send to Vercel
                    send_cookies_to_vercel(cookies, username)
                    
                    return {
                        "success": True,
                        "username": username,
                        "cookies": cookies,
                        "url": final_url,
                        "message": "Login successful!"
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Login failed. URL: {final_url}",
                        "url": final_url
                    }
                    
            except Exception as e:
                logger.error(f"❌ Login error: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                return {"success": False, "error": str(e)}
            finally:
                browser.close()
                
    except Exception as e:
        logger.error(f"❌ Browserless error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {"success": False, "error": str(e)}

# ============================================
# BACKGROUND LOGIN
# ============================================

def run_login_background(username, password):
    global login_status
    
    try:
        result = login_with_browserless(username, password)
        login_status["completed"] = True
        login_status["result"] = result
        login_status["in_progress"] = False
        login_status["end_time"] = datetime.now().isoformat()
                
    except Exception as e:
        logger.error(f"❌ Background login error: {str(e)}")
        login_status["completed"] = True
        login_status["result"] = {"success": False, "error": str(e)}
        login_status["in_progress"] = False

# ============================================
# MAIN
# ============================================

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Starting server on port {port}")
    logger.info(f"📂 Profile: {PROFILE_NAME}")
    logger.info(f"🔑 Token: {BROWSERLESS_TOKEN[:10]}..." if BROWSERLESS_TOKEN else "❌ No token")
    logger.info(f"👤 Username: {USERNAME}")
    logger.info(f"🤖 Auto 2FA: {'Enabled' if TWOFA_SECRET and PYOTP_AVAILABLE else 'Disabled'}")
    app.run(host='0.0.0.0', port=port, debug=False)