import requests
import time

def process_audio(file_path: str):
    # Start the job
    with open(file_path, 'rb') as f:
        response = requests.post('http://localhost:8000/api/audio/separate', 
                               files={'file': f})
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
        
        if status['status'] in ['completed', 'error']:
            print(f"\nJob {status['status']}")
            if 'result' in status:
                print(f"Output: {status['result']}")
            elif 'error' in status:
                print(f"Error: {status['error']}")
            break
            
        time.sleep(1)

if __name__ == "__main__":
    process_audio("sample.mp3") 