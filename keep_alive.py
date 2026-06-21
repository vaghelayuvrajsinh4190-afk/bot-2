"""
Mack Bot — Keep Alive Server
HTTP server to keep the bot alive on hosting platforms like Replit/Render.
Includes /health endpoint for monitoring.
UptimeRobot pings this every 5 minutes to bypass 15-minute sleep timers.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import os
import json
import time

# Track uptime
_start_time = time.time()


class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            # JSON health check endpoint
            uptime_seconds = int(time.time() - _start_time)
            hours = uptime_seconds // 3600
            minutes = (uptime_seconds % 3600) // 60
            seconds = uptime_seconds % 60

            health_data = {
                "status": "healthy",
                "bot": "Mack Bot",
                "uptime": f"{hours}h {minutes}m {seconds}s",
                "uptime_seconds": uptime_seconds,
                "version": "2027 Edition"
            }

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(health_data, indent=2).encode())
        else:
            # Default response for UptimeRobot pings
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()

            uptime_seconds = int(time.time() - _start_time)
            hours = uptime_seconds // 3600
            minutes = (uptime_seconds % 3600) // 60

            html = (
                f"<html><body style='background:#18191c;color:#2efc67;font-family:monospace;padding:40px;'>"
                f"<h1>🚀 Mack Bot</h1>"
                f"<p>✅ Bot is alive and running!</p>"
                f"<p>⏱️ Uptime: {hours}h {minutes}m</p>"
                f"<p>🛡️ Anti-crash: Active</p>"
                f"<p style='color:#666;'>Ping /health for JSON status.</p>"
                f"</body></html>"
            )
            self.wfile.write(html.encode())

    def log_message(self, format, *args):
        # Silence default request logging to keep the bot console output neat
        return


def run_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), KeepAliveHandler)
    print(f"🌐 Keep-alive server running on port {port}", flush=True)
    server.serve_forever()


def keep_alive():
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
