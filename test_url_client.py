import requests
import time

def process_audio_from_url(url: str):
    # Start the job
    response = requests.post('http://localhost:8000/api/audio/separate-from-url', 
                           json={"url": url})
    response_data = response.json()
    if 'job_id' not in response_data:
        print(f"Error: {response_data}")
        return
        
    job_id = response_data['job_id']
    print(f"Started job: {job_id}")
    
    # Poll for status
    while True:
        status = requests.get(f'http://localhost:8000/api/audio/status/{job_id}').json()
        print(f"\rProgress: {status.get('progress', 0)}%", end='')
        
        if status['status'] == 'completed':
            print("\nProcessing completed!")
            print("Files available at:")
            for stem_name, stem_path in status['files'].items():
                print(f"{stem_name.title()}: http://localhost:8000{stem_path}")
            break
        elif status['status'] == 'error':
            print(f"\nError: {status['error']}")
            break
            
        time.sleep(1)

if __name__ == "__main__":
    url = "https://p.scdn.co/mp3-preview/c792a8ce84218ee21455433268d5288d3621a5b2?cid=6b58815e509940539428705cce2b1d14"
    process_audio_from_url(url) 