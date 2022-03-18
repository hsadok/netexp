
try:
    from norman.norman_pktgen import *
except ModuleNotFoundError:
    raise RuntimeError('norman-dp not installed.')
