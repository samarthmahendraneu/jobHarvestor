import os
import logging
from confluent_kafka import Producer, Consumer, KafkaError
from src.broker.MessageBroker import MessageBroker

logger = logging.getLogger(__name__)

class KafkaBroker(MessageBroker):
    def __init__(self):
        self.kafka_host = os.getenv('KAFKA_HOST', 'localhost:9092')
        self.group_id = os.getenv('KAFKA_GROUP_ID', 'jobharvestor-consumers')

        self.producer_conf = {'bootstrap.servers': self.kafka_host}
        self.consumer_conf = {
            'bootstrap.servers': self.kafka_host,
            'group.id': self.group_id,
            'auto.offset.reset': 'earliest',
            'enable.auto.commit': True
        }
        
        # Maintain persistent native TCP streams for extremely high throughput
        self._producer = Producer(self.producer_conf)
        self._consumer = Consumer(self.consumer_conf)
        self._subscribed = set()

    def produce(self, topic: str, message: str) -> None:
        self._producer.produce(topic, message.encode('utf-8'))
        self._producer.flush()

    def consume(self, topic: str, batch_size: int) -> list[str]:
        if topic not in self._subscribed:
            self._consumer.subscribe([topic])
            self._subscribed.add(topic)
        
        messages = []
        # Consume exactly a maximum batch so we don't hold thousands of items in RAM safely distributing load
        msgs = self._consumer.consume(num_messages=batch_size, timeout=3.0)
        
        for msg in msgs:
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    logger.error(f"Kafka consume error: {msg.error()}")
                continue
            messages.append(msg.value().decode('utf-8'))
            
        return messages
