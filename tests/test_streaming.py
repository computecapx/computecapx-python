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

def test_telemetry_stream_wrapper_early_close():
    chunks = [b"chunk1", b"chunk2"]
    called = []
    
    def callback(data):
        called.append(data)
        
    class DummyStreamWithClose:
        def __init__(self, data):
            self.data = data
            
        def __iter__(self):
            return iter(self.data)
            
        def close(self):
            pass
            
    dummy = DummyStreamWithClose(chunks)
    wrapper = TelemetryStreamWrapper(dummy, callback)
    
    # Simulate partial read and close
    it = iter(wrapper)
    assert next(it) == b"chunk1"
    wrapper.close()
    
    assert called == [b"chunk1"]

def test_telemetry_stream_wrapper_string_chunks():
    chunks = ["hello ", b"world"]
    called = []
    
    def callback(data):
        called.append(data)
        
    wrapper = TelemetryStreamWrapper(chunks, callback)
    collected = list(wrapper)
    
    assert collected == chunks
    assert called == [b"hello world"]
