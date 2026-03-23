import os

def get_broker():
    btype = os.getenv("BROKER_TYPE", "kafka").lower()
    
    if btype == "redis":
        from .RedisBroker import RedisBroker
        return RedisBroker()
    elif btype == "kafka":
        from .KafkaBroker import KafkaBroker
        return KafkaBroker()
    else:
        raise ValueError(f"Unknown broker type configured in environment space: {btype}")
