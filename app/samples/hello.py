print("hello from the sandbox")
import os, platform
u = os.uname()
print("sysname=%s release=%s hostname=%s machine=%s" % (u.sysname, u.release, u.nodename, u.machine))
print("python=%s" % platform.python_version())
