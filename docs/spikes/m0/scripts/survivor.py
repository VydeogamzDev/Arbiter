import os, sys, time
path = sys.argv[1]
for i in range(int(os.environ.get("SURVIVOR_TICKS","40"))):
    with open(path, "a") as f: f.write(f"{time.time()} pid={os.getpid()} tick={i}\n")
    time.sleep(0.5)
