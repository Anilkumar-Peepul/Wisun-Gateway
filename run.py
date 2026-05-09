# wisun_gateway/run.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wisun_gateway.main import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
