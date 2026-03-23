import redis
import os
import logging
from src.broker.MessageBroker import MessageBroker

logger = logging.getLogger(__name__)

class RedisBroker(MessageBroker):
    def __init__(self):
        host = os.getenv('REDIS_HOST', 'localhost')
        port = int(os.getenv('REDIS_PORT', 6379))
        self.r = redis.Redis(host=host, port=port, decode_responses=True)
        try:
            self.r.ping()
        except redis.ConnectionError as e:
            logger.error(f"Redis connection failed: {e}")
            raise

    def produce(self, topic: str, message: str) -> None:
        self.r.rpush(topic, message)

    def consume(self, topic: str, batch_size: int) -> list[str]:
        items = []
        for _ in range(batch_size):
            item = self.r.lpop(topic)
            if item:
                items.append(item)
            else:
                break
        return items
