import os
import logging
from logging.handlers import TimedRotatingFileHandler

class Logger:
    def __init__(self, log_name):
        # 创建日志目录
        if not os.path.exists('logs'):
            os.makedirs('logs')

        self._logger = logging.getLogger("LiquidationBot")
        self._logger.setLevel(logging.DEBUG)

        # 格式：时间 - 级别 - [文件名:行号] - 消息
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
        )

        # 1. 控制台处理器 - 控制台只打印 INFO 及以上
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)

        # 2. 文件输出 (按天滚动，保留最近1天)
        file_handler = TimedRotatingFileHandler(
            filename=f'logs/{log_name}',
            when='midnight',
            interval=1,
            backupCount=1,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)

        self._logger.addHandler(console_handler)
        self._logger.addHandler(file_handler)

    def __call__(self):
        return self._logger

