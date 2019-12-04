import subprocess
import utils

proc = None

def run():
    print("[Redis] Starting redis-timeseries docker...")
    proc = subprocess.call("docker run -p 6379:6379 -it --rm redislabs/redistimeseries", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, universal_newlines=True)
    print("[Redis] Started.")

def stop():
    if proc:
        print("[Redis] Stopping redis-timeseries...")
        proc.kill()
        print("[Redis] Stopped.")

