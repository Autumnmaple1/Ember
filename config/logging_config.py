import logging
import os
from datetime import datetime

# 确保日志目录存在
LOG_DIR = os.path.join(os.getcwd(), "data", "logs")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# 配置日志记录
log_file = os.path.join(LOG_DIR, "system.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        # logging.StreamHandler()
    ],
)

# 屏蔽第三方库的请求日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name):
    return logging.getLogger(name)
