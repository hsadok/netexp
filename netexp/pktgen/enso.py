
try:
    from enso.ensogen import *
except ModuleNotFoundError:
    raise RuntimeError('enso-nic not installed.')
