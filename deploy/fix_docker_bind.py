"""Bind Docker State Science Intel to localhost only."""
import pathlib
import subprocess

compose = pathlib.Path("/root/us-science-intel/docker-compose.yml")
txt = compose.read_text()
if '"8000:8000"' in txt:
    txt = txt.replace('"8000:8000"', '"127.0.0.1:8000:8000"')
    compose.write_text(txt)
    print("docker-compose.yml: bound to 127.0.0.1")
    # Recreate the container with new port binding
    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd="/root/us-science-intel",
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)
else:
    print("docker-compose.yml: already bound to localhost")
