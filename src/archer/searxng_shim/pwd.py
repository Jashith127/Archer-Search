"""Windows stand-in for the Unix `pwd` module, copied into SearXNG's own venv by archer.searxng_local.

SearXNG imports `pwd` at the top of searx/valkeydb.py and only uses it to name the user in a log line.
"""
import collections
import getpass

struct_passwd = collections.namedtuple("struct_passwd", "pw_name pw_passwd pw_uid pw_gid pw_gecos pw_dir pw_shell")


def getpwuid(uid):
    return struct_passwd(getpass.getuser(), "x", uid, 0, "", "", "")
