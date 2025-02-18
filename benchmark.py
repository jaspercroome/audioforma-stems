import asyncio
import time
from fastapi.testclient import TestClient
from src.app import app
import statistics

# Create a test client
client = TestClient(app)

async def benchmark_file_serving(num_requests=100):
    # Path to a sample 30s MP3 file
    test_path = "files/20250217_193627/mdx_extra/input/vocals.mp3"
    
    # Benchmark route-based serving
    route_times = []
    mount_times = []
    
    for _ in range(num_requests):
        # Route-based serving
        start = time.perf_counter()
        response = client.get(f"/{test_path}")
        end = time.perf_counter()
        route_times.append((end - start) * 1000)  # Convert to milliseconds
        
        # Mount-based serving (direct file system access)
        start = time.perf_counter()
        with open(f"temp/{test_path.split('/', 1)[1]}", 'rb') as f:
            _ = f.read()
        end = time.perf_counter()
        mount_times.append((end - start) * 1000)

    print(f"\nResults over {num_requests} requests:")
    print("\nRoute-based serving:")
    print(f"Average response time: {statistics.mean(route_times):.2f}ms")
    print(f"Median response time: {statistics.median(route_times):.2f}ms")
    print(f"95th percentile: {sorted(route_times)[int(num_requests * 0.95)]:.2f}ms")
    
    print("\nMount-based serving:")
    print(f"Average response time: {statistics.mean(mount_times):.2f}ms")
    print(f"Median response time: {statistics.median(mount_times):.2f}ms")
    print(f"95th percentile: {sorted(mount_times)[int(num_requests * 0.95)]:.2f}ms")

if __name__ == "__main__":
    asyncio.run(benchmark_file_serving()) 