import pytest
import asyncio
from computecapx.wrapper import TelemetryStreamWrapper, AsyncTelemetryStreamWrapper

def test_telemetry_stream_wrapper_sync():
    chunks = [b"hello ", b"world"]
    called = []
    
    def callback(data):
        called.append(data)
        
    wrapper = TelemetryStreamWrapper(chunks, callback)
    
    # Read chunks
    collected = []
    for chunk in wrapper:
        collected.append(chunk)
        
    assert collected == chunks
    assert called == [b"hello world"]

def test_telemetry_stream_wrapper_async():
    class DummyAsyncStream:
        def __init__(self, data):
            self.data = data
            self.index = 0
            
        def __aiter__(self):
            return self
            
        async def __anext__(self):
            if self.index >= len(self.data):
                raise StopAsyncIteration
            val = self.data[self.index]
            self.index += 1
            return val

    chunks = [b"async ", b"hello ", b"world"]
    called = []
    
    def callback(data):
        called.append(data)
        
    dummy = DummyAsyncStream(chunks)
    wrapper = AsyncTelemetryStreamWrapper(dummy, callback)
    
    async def run_test():
        collected = []
        async for chunk in wrapper:
            collected.append(chunk)
        return collected

    collected = asyncio.run(run_test())
        
    assert collected == chunks
    assert called == [b"async hello world"]
