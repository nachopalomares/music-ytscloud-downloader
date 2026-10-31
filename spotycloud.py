"""Backward compatibility shim redirecting to ytscloud."""
import sys
from ytscloud import *
from ytscloud import main

if __name__ == "__main__":
    sys.exit(main())
