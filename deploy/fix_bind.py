import pathlib
p = pathlib.Path("/etc/systemd/system/council-of-alignment.service")
txt = p.read_text()
txt = txt.replace("--host 0.0.0.0", "--host 127.0.0.1")
p.write_text(txt)
print("Updated service to bind 127.0.0.1")
