from slowapi import Limiter
from slowapi.util import get_remote_address

# Instance tunggal — diimport di main.py dan di route yang butuh limit ketat
limiter = Limiter(key_func=get_remote_address)
