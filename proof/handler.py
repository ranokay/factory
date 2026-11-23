import subprocess


def run_command(command):
    return subprocess.run(command, shell=True, capture_output=True, text=True)
