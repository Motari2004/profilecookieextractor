from flask import Flask, jsonify, request, render_template_string
from playwright.sync_api import sync_playwright
import logging
import os
import json
import time
from datetime import datetime
import requests

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================
# CONFIGURATION
# ============================================

BROWSERLESS_TOKEN = os.environ.get('BROWSERLESS_API_KEY', '2V9phNVcUGlxvJJ9154e14b2c71b8c81d6e0f2f23bcfaf323')
BROWSERLESS_ORIGIN = 'https://production-sfo.browserless.io'
PROFILE_NAME = os.environ.get('PROFILE_NAME', 'instagram-login')

# Vercel webhook URL for sending cookies back
VERCEL_WEBHOOK_URL = os.environ.get('VERCEL_WEBHOOK_URL', 'https://fetchgram-one.vercel.app/api/cookies/sync')

# ============================================
# HTML UI
# ============================================

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Instagram Cookie Extractor</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
            background: #fafafa;
            padding: 20px;
        }
        .container { max-width: 700px; margin: 0 auto; }
        h1 { color: #262626; margin-bottom: 20px; }
        .card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            font-size: 14px;
            cursor: pointer;
            font-weight: 600;
            color: white;
        }
        .btn-primary { background: #0095f6; }
        .btn-primary:hover { background: #0077cc; }
        .btn-success { background: #28a745; }
        .btn-success:hover { background: #218838; }
        .btn-warning { background: #ffc107; color: #212529; }
        .btn-warning:hover { background: #e0a800; }
        .btn-danger { background: #ed4956; }
        .btn-danger:hover { background: #c43a46; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .status {
            padding: 12px 16px;
            border-radius: 4px;
            margin-top: 15px;
        }
        .status-info { background: #cce5ff; color: #004085; }
        .status-success { background: #d4edda; color: #155724; }
        .status-error { background: #f8d7da; color: #721c24; }
        .log-container {
            background: #1e1e1e;
            color: #d4d4d4;
            padding: 15px;
            border-radius: 8px;
            max-height: 400px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            line-height: 1.6;
            margin-top: 15px;
        }
        .log-entry .time { color: #569cd6; margin-right: 10px; }
        .log-entry .success { color: #4ec9b0; }
        .log-entry .error { color: #f44747; }
        .log-entry .warning { color: #dcdcaa; }
        .log-entry .info { color: #4ec9b0; }
        .info-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            margin-top: 10px;
        }
        .info-item {
            background: #f8f9fa;
            padding: 10px;
            border-radius: 4px;
        }
        .info-item .label { font-size: 11px; color: #8e8e8e; }
        .info-item .value { font-size: 16px; font-weight: 600; color: #262626; }
        .cookie-list {
            max-height: 300px;
            overflow-y: auto;
            margin-top: 10px;
        }
        .cookie-item {
            padding: 6px 10px;
            border-bottom: 1px solid #efefef;
            font-size: 13px;
        }
        .cookie-item .name { font-weight: 600; color: #262626; }
        .cookie-item .value { color: #8e8e8e; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🍪 Instagram Cookie Extractor</h1>
        
        <div class="card">
            <h3>Extract & Refresh Cookies</h3>
            <p style="color: #8e8e8e; margin: 10px 0;">
                Extract cookies from Browserless profile or refresh the profile with new cookies.
            </p>
            <button id="extractBtn" class="btn btn-primary" onclick="extractCookies()">🍪 Extract Cookies</button>
            <button id="refreshBtn" class="btn btn-warning" onclick="refreshProfile()" style="margin-left: 10px;">🔄 Refresh Profile</button>
            <button class="btn btn-danger" onclick="clearLogs()" style="margin-left: 10px;">🗑️ Clear Logs</button>
            
            <div id="status" style="display: none;" class="status"></div>
            
            <div class="info-grid">
                <div class="info-item">
                    <div class="label">Profile</div>
                    <div class="value" id="profileName">instagram-login</div>
                </div>
                <div class="info-item">
                    <div class="label">Status</div>
                    <div class="value" id="connectionStatus">⏳ Ready</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h3>📋 Extracted Cookies</h3>
            <div id="cookieList">
                <p style="color: #8e8e8e; font-size: 14px;">No cookies extracted yet.</p>
            </div>
            <button class="btn btn-success" onclick="copyCookies()" style="margin-top: 10px;">📋 Copy All Cookies</button>
            <button class="btn btn-primary" onclick="sendToVercel()" style="margin-top: 10px; margin-left: 10px;">📤 Send to Vercel</button>
        </div>
        
        <div class="card">
            <h3>📝 Logs</h3>
            <div id="logContainer" class="log-container">
                <div class="log-entry"><span class="time">[System]</span><span class="info">Ready. Click "Extract Cookies" to begin.</span></div>
            </div>
        </div>
    </div>

    <script>
        let extractedCookies = [];
        
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
        
        function updateStatus(message, type = 'info') {
            const status = document.getElementById('status');
            status.style.display = 'block';
            status.className = `status status-${type}`;
            status.textContent = message;
        }
        
        function updateConnectionStatus(text) {
            document.getElementById('connectionStatus').textContent = text;
        }
        
        async function extractCookies() {
            const btn = document.getElementById('extractBtn');
            btn.disabled = true;
            btn.textContent = '⏳ Extracting...';
            addLog('🔍 Starting cookie extraction...', 'info');
            updateConnectionStatus('🔄 Extracting...');
            updateStatus('⏳ Extracting cookies from Browserless...', 'info');
            
            try {
                const response = await fetch('/api/extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                
                const data = await response.json();
                console.log('Extract response:', data);
                
                if (data.success) {
                    extractedCookies = data.cookies || [];
                    addLog(`✅ Successfully extracted ${extractedCookies.length} cookies!`, 'success');
                    updateStatus(`✅ Extracted ${extractedCookies.length} cookies successfully!`, 'success');
                    updateConnectionStatus('✅ Connected');
                    renderCookies(extractedCookies);
                    
                    if (data.message) {
                        addLog(`📝 ${data.message}`, 'info');
                    }
                } else {
                    addLog(`❌ Failed: ${data.error || 'Unknown error'}`, 'error');
                    updateStatus(`❌ ${data.error || 'Failed to extract cookies'}`, 'error');
                    updateConnectionStatus('❌ Failed');
                }
            } catch (error) {
                addLog(`❌ Error: ${error.message}`, 'error');
                updateStatus(`❌ Error: ${error.message}`, 'error');
                updateConnectionStatus('❌ Error');
            } finally {
                btn.disabled = false;
                btn.textContent = '🍪 Extract Cookies';
            }
        }
        
        async function refreshProfile() {
            const btn = document.getElementById('refreshBtn');
            btn.disabled = true;
            btn.textContent = '⏳ Refreshing...';
            addLog('🔄 Starting profile refresh...', 'info');
            updateConnectionStatus('🔄 Refreshing...');
            updateStatus('⏳ Refreshing Browserless profile...', 'info');
            
            try {
                const response = await fetch('/api/refresh', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                
                const data = await response.json();
                console.log('Refresh response:', data);
                
                if (data.success) {
                    extractedCookies = data.cookies || [];
                    addLog(`✅ Profile refreshed! ${extractedCookies.length} cookies updated.`, 'success');
                    updateStatus(`✅ Profile refreshed with ${extractedCookies.length} cookies!`, 'success');
                    updateConnectionStatus('✅ Refreshed');
                    renderCookies(extractedCookies);
                    
                    if (data.message) {
                        addLog(`📝 ${data.message}`, 'info');
                    }
                } else {
                    addLog(`❌ Refresh failed: ${data.error || 'Unknown error'}`, 'error');
                    updateStatus(`❌ ${data.error || 'Failed to refresh profile'}`, 'error');
                    updateConnectionStatus('❌ Failed');
                }
            } catch (error) {
                addLog(`❌ Error: ${error.message}`, 'error');
                updateStatus(`❌ Error: ${error.message}`, 'error');
                updateConnectionStatus('❌ Error');
            } finally {
                btn.disabled = false;
                btn.textContent = '🔄 Refresh Profile';
            }
        }
        
        function renderCookies(cookies) {
            const container = document.getElementById('cookieList');
            if (!cookies || cookies.length === 0) {
                container.innerHTML = '<p style="color: #8e8e8e; font-size: 14px;">No cookies extracted.</p>';
                return;
            }
            
            let html = `<div class="cookie-list">`;
            cookies.forEach((cookie, i) => {
                const name = cookie.name || 'unknown';
                const value = cookie.value ? cookie.value.substring(0, 50) + (cookie.value.length > 50 ? '...' : '') : '';
                html += `
                    <div class="cookie-item">
                        <span class="name">#${i + 1}. ${name}</span>
                        <span class="value">${value}</span>
                    </div>
                `;
            });
            html += `</div>`;
            html += `<p style="margin-top: 10px; font-size: 13px; color: #8e8e8e;">Total: ${cookies.length} cookies</p>`;
            container.innerHTML = html;
        }
        
        function copyCookies() {
            if (!extractedCookies || extractedCookies.length === 0) {
                alert('No cookies to copy. Extract cookies first.');
                return;
            }
            
            const json = JSON.stringify(extractedCookies, null, 2);
            navigator.clipboard.writeText(json).then(() => {
                addLog('📋 Cookies copied to clipboard!', 'success');
                updateStatus('📋 Cookies copied to clipboard!', 'success');
            }).catch(() => {
                const textarea = document.createElement('textarea');
                textarea.value = json;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
                addLog('📋 Cookies copied to clipboard!', 'success');
                updateStatus('📋 Cookies copied to clipboard!', 'success');
            });
        }
        
        async function sendToVercel() {
            if (!extractedCookies || extractedCookies.length === 0) {
                alert('No cookies to send. Extract cookies first.');
                return;
            }
            
            try {
                const response = await fetch('/api/send-to-vercel', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ cookies: extractedCookies })
                });
                
                const data = await response.json();
                if (data.success) {
                    addLog('✅ Cookies sent to Vercel!', 'success');
                    updateStatus('✅ Cookies sent to Vercel!', 'success');
                } else {
                    addLog(`❌ Failed to send to Vercel: ${data.error}`, 'error');
                    updateStatus(`❌ Failed to send to Vercel: ${data.error}`, 'error');
                }
            } catch (error) {
                addLog(`❌ Error sending to Vercel: ${error.message}`, 'error');
                updateStatus(`❌ Error sending to Vercel: ${error.message}`, 'error');
            }
        }
        
        function clearLogs() {
            const container = document.getElementById('logContainer');
            container.innerHTML = `<div class="log-entry"><span class="time">[System]</span><span class="info">Logs cleared.</span></div>`;
        }
    </script>
</body>
</html>
'''

# ============================================
# EXTRACT COOKIES FROM BROWSERLESS
# ============================================

def extract_cookies_from_browserless():
    """Extract cookies from Browserless profile"""
    logger.info("🍪 Starting cookie extraction...")
    
    if not BROWSERLESS_TOKEN:
        logger.error("❌ BROWSERLESS_API_KEY not set")
        return {"success": False, "error": "BROWSERLESS_API_KEY not set"}
    
    try:
        with sync_playwright() as p:
            logger.info("🔗 Connecting to Browserless...")
            
            # Try to connect with profile
            try:
                browser = p.chromium.connect_over_cdp(
                    f"wss://{BROWSERLESS_ORIGIN.replace('https://', '')}?token={BROWSERLESS_TOKEN}&profile={PROFILE_NAME}"
                )
                logger.info(f"✅ Connected with profile: {PROFILE_NAME}")
            except Exception as e:
                logger.warning(f"⚠️ Profile connection failed: {e}")
                # Try without profile
                browser = p.chromium.connect_over_cdp(
                    f"wss://{BROWSERLESS_ORIGIN.replace('https://', '')}?token={BROWSERLESS_TOKEN}"
                )
                logger.info("✅ Connected without profile")
            
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            
            # Navigate to Instagram
            logger.info("🌐 Navigating to Instagram...")
            page.goto("https://www.instagram.com/", wait_until="networkidle")
            time.sleep(2)
            
            # Check if logged in
            if "login" in page.url:
                browser.close()
                logger.warning("⚠️ Not logged in")
                return {"success": False, "error": "Browserless profile is not logged in"}
            
            # Extract cookies
            logger.info("🍪 Extracting cookies...")
            cookies = context.cookies()
            logger.info(f"✅ Extracted {len(cookies)} cookies")
            
            browser.close()
            return {"success": True, "cookies": cookies, "count": len(cookies)}
            
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return {"success": False, "error": str(e)}

# ============================================
# REFRESH BROWSERLESS PROFILE
# ============================================

def refresh_browserless_profile():
    """
    Refresh the Browserless profile using the refresh endpoint.
    This updates the profile's cookies without launching a browser.
    """
    logger.info("🔄 Refreshing Browserless profile...")
    
    if not BROWSERLESS_TOKEN:
        logger.error("❌ BROWSERLESS_API_KEY not set")
        return {"success": False, "error": "BROWSERLESS_API_KEY not set"}
    
    try:
        # Step 1: Extract fresh cookies
        extract_result = extract_cookies_from_browserless()
        if not extract_result.get('success'):
            return extract_result
        
        cookies = extract_result.get('cookies', [])
        logger.info(f"✅ Extracted {len(cookies)} fresh cookies")
        
        # Step 2: Format cookies for Browserless refresh endpoint
        formatted_cookies = []
        for cookie in cookies:
            formatted_cookies.append({
                "name": cookie.get('name', ''),
                "value": cookie.get('value', ''),
                "domain": cookie.get('domain', '.instagram.com'),
                "path": cookie.get('path', '/'),
                "expires": cookie.get('expirationDate', -1),
                "httpOnly": cookie.get('httpOnly', False),
                "secure": cookie.get('secure', False),
                "session": cookie.get('session', True)
            })
        
        # Step 3: Send refresh request to Browserless
        refresh_url = f"{BROWSERLESS_ORIGIN}/profile/refresh?token={BROWSERLESS_TOKEN}"
        
        refresh_payload = {
            "name": PROFILE_NAME,
            "state": {
                "cookies": formatted_cookies
            }
        }
        
        logger.info(f"📤 Sending refresh request to Browserless...")
        
        response = requests.post(
            refresh_url,
            json=refresh_payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        if response.status_code == 200:
            logger.info("✅ Browserless profile refreshed successfully!")
            
            # Also send cookies to Vercel
            send_cookies_to_vercel(cookies)
            
            return {
                "success": True,
                "message": f"Profile '{PROFILE_NAME}' refreshed with {len(cookies)} cookies",
                "cookies": cookies,
                "count": len(cookies)
            }
        elif response.status_code == 404:
            logger.warning("⚠️ Profile not found, creating new profile...")
            
            # Create new profile
            create_url = f"{BROWSERLESS_ORIGIN}/profile/create?token={BROWSERLESS_TOKEN}"
            create_response = requests.post(
                create_url,
                json=refresh_payload,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            
            if create_response.status_code in [200, 201]:
                logger.info(f"✅ Browserless profile created: {PROFILE_NAME}")
                send_cookies_to_vercel(cookies)
                return {
                    "success": True,
                    "message": f"Profile '{PROFILE_NAME}' created with {len(cookies)} cookies",
                    "cookies": cookies,
                    "count": len(cookies)
                }
            else:
                return {"success": False, "error": f"Profile creation failed: {create_response.status_code}"}
        else:
            logger.error(f"❌ Refresh failed: {response.status_code} - {response.text}")
            return {"success": False, "error": f"Refresh failed: {response.status_code}"}
            
    except Exception as e:
        logger.error(f"❌ Refresh error: {e}")
        return {"success": False, "error": str(e)}

# ============================================
# SEND COOKIES TO VERCEL
# ============================================

def send_cookies_to_vercel(cookies):
    """Send extracted cookies to Vercel webhook"""
    if not VERCEL_WEBHOOK_URL:
        logger.warning("⚠️ VERCEL_WEBHOOK_URL not set, skipping")
        return
    
    try:
        logger.info(f"📤 Sending {len(cookies)} cookies to Vercel...")
        
        # Get username from cookies
        username = None
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

# ============================================
# ROUTES
# ============================================

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/extract', methods=['POST'])
def api_extract():
    """API endpoint to extract cookies"""
    result = extract_cookies_from_browserless()
    return jsonify(result)

@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    """API endpoint to refresh Browserless profile"""
    result = refresh_browserless_profile()
    return jsonify(result)

@app.route('/api/send-to-vercel', methods=['POST'])
def api_send_to_vercel():
    """API endpoint to send cookies to Vercel"""
    data = request.get_json(silent=True) or {}
    cookies = data.get('cookies', [])
    
    if not cookies:
        return jsonify({"success": False, "error": "No cookies provided"})
    
    send_cookies_to_vercel(cookies)
    return jsonify({"success": True, "message": f"Sent {len(cookies)} cookies to Vercel"})

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "browserless_configured": bool(BROWSERLESS_TOKEN),
        "profile": PROFILE_NAME,
        "vercel_webhook_configured": bool(VERCEL_WEBHOOK_URL)
    })

# ============================================
# MAIN
# ============================================

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 Starting Cookie Extractor on port {port}")
    logger.info(f"📂 Profile: {PROFILE_NAME}")
    logger.info(f"🔑 Token: {BROWSERLESS_TOKEN[:10]}..." if BROWSERLESS_TOKEN else "❌ No token")
    logger.info(f"📤 Vercel webhook: {VERCEL_WEBHOOK_URL or 'Not configured'}")
    app.run(host='0.0.0.0', port=port, debug=False)