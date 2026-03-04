"""Fix file permissions: .env files, backups, postgres password, backup script."""
import pathlib
import os
import subprocess

# 1. Fix world-readable .env files
for env_path in ["/root/monopoly-trader/.env", "/root/us-science-intel/.env"]:
    p = pathlib.Path(env_path)
    if p.exists():
        os.chmod(env_path, 0o600)
        print(f"chmod 600: {env_path}")
    else:
        print(f"Not found: {env_path}")

# 2. Fix backup directory and files
backups = pathlib.Path("/root/backups")
if backups.exists():
    os.chmod(str(backups), 0o700)
    print(f"chmod 700: {backups}")
    for f in backups.glob("*.db"):
        os.chmod(str(f), 0o600)
        print(f"chmod 600: {f}")

# 3. Move postgres password from docker-compose.yml to .env
compose = pathlib.Path("/root/us-science-intel/docker-compose.yml")
env_file = pathlib.Path("/root/us-science-intel/.env")

if compose.exists():
    txt = compose.read_text()
    # Extract hardcoded password
    hardcoded_pw = "xoSTxTfv1jBelN1yTTGibuAIo7C9g_IlXKggPOaQgm0"
    if hardcoded_pw in txt:
        # Add to .env if not already there
        env_content = env_file.read_text() if env_file.exists() else ""
        if "POSTGRES_PASSWORD" not in env_content:
            env_content += f"\nPOSTGRES_PASSWORD={hardcoded_pw}\n"
            env_file.write_text(env_content)
            os.chmod(str(env_file), 0o600)
            print("Added POSTGRES_PASSWORD to .env")

        # Replace in docker-compose.yml
        txt = txt.replace(
            f"POSTGRES_PASSWORD={hardcoded_pw}",
            "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}"
        )
        # Also fix the web service DATABASE_URL if it has the password inline
        compose.write_text(txt)
        print("Replaced hardcoded postgres password in docker-compose.yml with env var")
    else:
        print("Postgres password already not hardcoded")

# 4. Fix backup script to use proper umask
backup_script = pathlib.Path("/root/council-backup.sh")
if backup_script.exists():
    txt = backup_script.read_text()
    if "umask" not in txt:
        # Add umask at the top after shebang
        lines = txt.splitlines()
        if lines and lines[0].startswith("#!"):
            lines.insert(1, "umask 077")
        else:
            lines.insert(0, "umask 077")
        backup_script.write_text("\n".join(lines) + "\n")
        print("Added umask 077 to backup script")
    else:
        print("Backup script already has umask")
else:
    print("No backup script found")

# 5. Verify
print("\n--- Verification ---")
for path in ["/root/monopoly-trader/.env", "/root/us-science-intel/.env",
             "/root/council-of-alignment/.env", "/root/backups"]:
    p = pathlib.Path(path)
    if p.exists():
        mode = oct(p.stat().st_mode)[-3:]
        print(f"  {path}: {mode}")
