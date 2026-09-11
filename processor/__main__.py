import os
import uvicorn
from .env import load_env

if __name__ == '__main__':
    load_env()
    # Capacity is process-local. The simulator must use exactly one process.
    uvicorn.run('processor.app:create_app', factory=True,
                host=os.getenv('PROCESSOR_HOST', '127.0.0.1'),
                port=int(os.getenv('PROCESSOR_PORT', '8001')), workers=1,
                log_level='warning', access_log=False)
