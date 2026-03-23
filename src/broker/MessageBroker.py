from abc import ABC, abstractmethod
from typing import List

class MessageBroker(ABC):
    @abstractmethod
    def produce(self, topic: str, message: str) -> None:
        """Publishes a single string payload to the broker Topic/Queue."""
        pass
        
    @abstractmethod
    def consume(self, topic: str, batch_size: int) -> List[str]:
        """Atomically retrieves a safe sub-batch of messages from the broker."""
        pass
