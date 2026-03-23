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
        
    def _get_producer(self):
        return Producer(self.producer_conf)
        
    def _get_consumer(self):
        return Consumer(self.consumer_conf)

    def produce(self, topic: str, message: str) -> None:
        p = self._get_producer()
        p.produce(topic, message.encode('utf-8'))
        p.flush()

    def consume(self, topic: str, batch_size: int) -> list[str]:
        c = self._get_consumer()
        c.subscribe([topic])
        
        messages = []
        # Consume exactly a maximum batch so we don't hold thousands of items in RAM safely distributing load
        msgs = c.consume(num_messages=batch_size, timeout=1.0)
        
        for msg in msgs:
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    logger.error(f"Kafka consume error: {msg.error()}")
                continue
            messages.append(msg.value().decode('utf-8'))
            
        c.close()
        return messages
