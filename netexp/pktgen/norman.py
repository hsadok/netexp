
try:
    from normandp.norman_pktgen import *
except ModuleNotFoundError:
    raise RuntimeError('normandp not installed.')
