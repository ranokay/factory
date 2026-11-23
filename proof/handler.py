import subprocess


def run_command(command):
    return subprocess.run(command, capture_output=True, text=True, check=True)
