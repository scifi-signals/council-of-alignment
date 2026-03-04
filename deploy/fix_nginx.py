"""Add nginx rate limiting, HTTPS catch-all, and harden default server block."""
import pathlib
import subprocess

# 1. Add rate limit zone to nginx.conf (in http block)
nginx_conf = pathlib.Path("/etc/nginx/nginx.conf")
txt = nginx_conf.read_text()
if "limit_req_zone" not in txt:
    # Insert after the http { line
    txt = txt.replace(
        "http {",
        "http {\n\t# Rate limiting\n\tlimit_req_zone $binary_remote_addr zone=council:10m rate=10r/s;\n\tlimit_req_zone $binary_remote_addr zone=council_api:10m rate=5r/s;",
        1
    )
    nginx_conf.write_text(txt)
    print("Added rate limit zones to nginx.conf")
else:
    print("Rate limit zones already exist")

# 2. Add rate limiting to council site config
council_conf = pathlib.Path("/etc/nginx/sites-enabled/council")
txt = council_conf.read_text()
if "limit_req" not in txt:
    txt = txt.replace(
        "proxy_pass http://127.0.0.1:8890;",
        "limit_req zone=council burst=20 nodelay;\n        proxy_pass http://127.0.0.1:8890;",
        1
    )
    council_conf.write_text(txt)
    print("Added rate limiting to council site")
else:
    print("Council site already has rate limiting")

# 3. Replace default server block with a secure catch-all
default_conf = pathlib.Path("/etc/nginx/sites-enabled/default")
default_conf.write_text("""# Catch-all: drop requests to unknown hosts or direct IP
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 444;
}

server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name _;
    ssl_certificate /etc/letsencrypt/live/council.stardreamgames.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/council.stardreamgames.com/privkey.pem;
    return 444;
}
""")
print("Replaced default server block with catch-all (returns 444)")

# 4. Test and reload nginx
result = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
if result.returncode == 0:
    subprocess.run(["systemctl", "reload", "nginx"], check=True)
    print("nginx config test passed, reloaded")
else:
    print(f"nginx config test FAILED:\n{result.stderr}")
