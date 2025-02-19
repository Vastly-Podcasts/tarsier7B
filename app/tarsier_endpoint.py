from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
import torch
from typing import Dict, Any, Optional, List
import tempfile
import os
import requests
from urllib.parse import urlparse
import sys

# Add Tarsier to path
tarsier_path = os.path.dirname(os.path.dirname(__file__))
sys.path.append(tarsier_path)

# Import Tarsier modules
from tasks.inference_quick_start import process_one, load_model_and_processor

app = FastAPI(title="Tarsier Original Implementation API")

# Model configuration
model_path = "omni-research/Tarsier-34b"

class ModelState:
    def __init__(self):
        self.model = None
        self.processor = None

# Create a single instance to hold model state
model_state = ModelState()

class GenerateRequest(BaseModel):
    instruction: str
    max_new_tokens: int = 512
    do_sample: bool = False
    temperature: float = 0.0
    top_p: float = 1.0
    video_url: Optional[HttpUrl] = None

async def download_video(url: str) -> str:
    """Download video from URL to temporary file."""
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        parsed_url = urlparse(url)
        ext = os.path.splitext(parsed_url.path)[1] or '.mp4'
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    tmp.write(chunk)
            return tmp.name
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to download video: {str(e)}")

@app.on_event("startup")
async def load_model():
    try:
        print("Loading model and processor using Tarsier's implementation...")
        
        # Set memory optimization flags
        os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
        
        # Load model and processor using inference_quick_start's implementation
        model_state.model, model_state.processor = load_model_and_processor(model_path, max_n_frames=8)
        
        print("Model and processor loaded successfully!")
    except Exception as e:
        print(f"Error loading model: {str(e)}")
        raise

@app.post("/generate")
async def generate(request: GenerateRequest) -> Dict[str, Any]:
    if model_state.model is None or model_state.processor is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    try:
        # Set up generation kwargs exactly as in inference_quick_start
        generate_kwargs = {
            "do_sample": request.do_sample,
            "max_new_tokens": request.max_new_tokens,
            "top_p": request.top_p,
            "temperature": request.temperature,
            "use_cache": True
        }
        
        # Handle video if URL is provided
        if request.video_url:
            video_path = await download_video(str(request.video_url))
            print(f"Video downloaded to: {video_path}")
            
            try:
                # Validate video file
                if not os.path.exists(video_path):
                    raise HTTPException(status_code=400, detail="Video file not created")
                video_size = os.path.getsize(video_path)
                print(f"Video file size: {video_size / 1024:.2f} KB")
                
                if video_size == 0:
                    raise HTTPException(status_code=400, detail="Video file is empty")
                
                try:
                    print("Processing with instruction:", request.instruction)
                    # Format prompt exactly as in inference_quick_start
                    prompt = "<video>\n" + request.instruction.replace("<image>", "").replace("<video>", "")
                    
                    # Use inference_quick_start's process_one directly
                    generated_text = process_one(
                        model=model_state.model,
                        processor=model_state.processor,
                        prompt=prompt,
                        video_file=video_path,
                        generate_kwargs=generate_kwargs
                    )
                    
                    print(f"\nGenerated text: {generated_text}")
                    
                except Exception as e:
                    print(f"Error processing video: {str(e)}")
                    raise HTTPException(status_code=500, detail=str(e))
            finally:
                # Cleanup temporary video file
                print(f"Cleaning up temporary file: {video_path}")
                os.unlink(video_path)
        else:
            # Text-only processing
            try:
                prompt = request.instruction
                generated_text = process_one(
                    model=model_state.model,
                    processor=model_state.processor,
                    prompt=prompt,
                    video_file=None,
                    generate_kwargs=generate_kwargs
                )
            except Exception as e:
                print(f"Error processing text: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))
        
        return {
            "generated_text": generated_text,
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "model_loaded": model_state.model is not None,
        "device": str(next(model_state.model.parameters()).device) if model_state.model is not None else "not loaded"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 