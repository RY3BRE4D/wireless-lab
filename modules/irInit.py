import subprocess
import time

IR_DEVICE = "rc0"

IR_PROTOCOLS = [
    "nec",
    "rc-5",
    "rc-6",
    "sony",
    "jvc",
    "lirc",
    "rc-5-sz",
    "mce_kbd",
    "xmp",
    "sharp",
    "sanyo",
]

IR_KEYTABLE = "/usr/bin/ir-keytable"

def enableIrProtocols():
    # Wait For Kernel To Expose /sys/class/rc
    for _ in range(20):
        try:
            subprocess.run(
                [IR_KEYTABLE, "-s", IR_DEVICE],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            break
        except Exception:
            time.sleep(0.25)
    else:
        print("[IR] rc Device Not Available")
        return False

    cmd = ["/usr/bin/sudo", "-n", IR_KEYTABLE, "-s", IR_DEVICE]
    for proto in IR_PROTOCOLS:
        cmd += ["-p", proto]
    try:
        res = subprocess.run(cmd, check=True, text=True, capture_output=True)
        print("[IR] Protocols Enabled:", ", ".join(IR_PROTOCOLS))
        if res.stdout:
            print("[IR] stdout:", res.stdout.strip())
        if res.stderr:
            print("[IR] stderr:", res.stderr.strip())
        return True
    except subprocess.CalledProcessError as e:
        print("[IR] Failed To Enable Protocols, rc=", e.returncode)
        print("[IR] stdout:", (e.stdout or "").strip())
        print("[IR] stderr:", (e.stderr or "").strip())
        return False
    except Exception as e:
        print("[IR] Failed To Enable Protocols:", repr(e))
        return False


'''
    try:
        subprocess.run(cmd, check=True)
        print("[IR] Protocols Enabled:", ", ".join(IR_PROTOCOLS))
        return True
    except Exception as e:
        print("[IR] Failed To Enable Protocols:", e)
        return False
'''
