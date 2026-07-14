import cv2
import torch
import numpy as np
import time
import subprocess
import os
import sys
import io
import urllib.request
from basicsr.archs.rrdbnet_arch import RRDBNet
from realesrgan.archs.srvgg_arch import SRVGGNetCompact
from realesrgan import RealESRGANer

# Ensure weights directory exists
os.makedirs("weights", exist_ok=True)

# Pretrained model URLs
urls = {
    "realesr-animevideov3.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth",
    "RealESRGAN_x4plus_anime_6B.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth"
}

def download_weight(name, url):
    path = os.path.join("weights", name)
    if not os.path.exists(path):
        print(f"Downloading {name} from {url}...")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response, open(path, 'wb') as out_file:
            out_file.write(response.read())
        print(f"Downloaded {name} successfully!")
    else:
        print(f"Model weight {name} already exists.")
    return path

# Download weights
weight_animev3 = download_weight("realesr-animevideov3.pth", urls["realesr-animevideov3.pth"])
weight_anime6b = download_weight("RealESRGAN_x4plus_anime_6B.pth", urls["RealESRGAN_x4plus_anime_6B.pth"])

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("Using PyTorch Device:", device)

def get_upsampler(model_name, tile=400):
    if model_name == "realesr-animevideov3":
        # Compact VGG-style network (num_feat=64, num_conv=16, upscale=4, act_type='prelu')
        model = SRVGGNetCompact(
            num_in_ch=3, 
            num_out_ch=3, 
            num_feat=64, 
            num_conv=16, 
            upscale=4, 
            act_type='prelu'
        )
        upsampler = RealESRGANer(
            scale=4,
            model_path=weight_animev3,
            model=model,
            tile=tile,
            tile_pad=10,
            pre_pad=0,
            half=True,
            device=device
        )
    elif model_name == "RealESRGAN_x4plus_anime_6B":
        # RRDBNet with 6 blocks
        model = RRDBNet(
            num_in_ch=3, 
            num_out_ch=3, 
            num_feat=64, 
            num_block=6, 
            num_grow_ch=32, 
            scale=4
        )
        upsampler = RealESRGANer(
            scale=4,
            model_path=weight_anime6b,
            model=model,
            tile=tile,
            tile_pad=10,
            pre_pad=0,
            half=True,
            device=device
        )
    else:
        raise ValueError(f"Unknown model name: {model_name}")
    return upsampler

def upscale_video(input_video_path, model_name, target_resolution="1080p"):
    if not os.path.exists(input_video_path):
        print(f"Error: Input video {input_video_path} not found.")
        return None

    cap = cv2.VideoCapture(input_video_path)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if target_resolution == "1080p":
        target_w, target_h = 1920, 1080
        outscale = 1.5  # 1280 * 1.5 = 1920, 720 * 1.5 = 1080
    elif target_resolution == "2k":
        target_w, target_h = 2560, 1440
        outscale = 2.0  # 1280 * 2.0 = 2560, 720 * 2.0 = 1440
    else:
        raise ValueError(f"Unsupported resolution: {target_resolution}")

    method_label = model_name if model_name else "Bicubic"
    print(f"\n--- Upscaling to {target_resolution} ({target_w}x{target_h}) using {method_label} ---")
    
    # Output path
    temp_output_path = f"temp_upscale_{method_label.lower()}_{target_resolution}.mp4"
    final_output_path = f"output_upscale_{method_label.lower()}_{target_resolution}.mp4"

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(temp_output_path, fourcc, fps, (target_w, target_h))

    start_time = time.time()
    
    if model_name:
        # Load upsampler (tile at 400 to prevent VRAM OOM on 720p inputs)
        upsampler = get_upsampler(model_name, tile=400)
        
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            # Run inference
            t0 = time.time()
            # enhance method expects BGR numpy array, returns BGR numpy array
            # outscale handles resizing to the desired output scale (1.5x or 2.0x)
            output, _ = upsampler.enhance(frame, outscale=outscale)
            t1 = time.time()
            
            out.write(output)
            frame_idx += 1
            if frame_idx % 50 == 0:
                print(f"Upscaled {frame_idx}/{frame_count} frames... Last frame took {t1-t0:.2f}s")
    else:
        # Bicubic baseline
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            output = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_CUBIC)
            out.write(output)
            frame_idx += 1

    cap.release()
    out.release()

    elapsed_time = time.time() - start_time
    print(f"Finished upscaling {frame_idx} frames in {elapsed_time:.2f} seconds ({frame_idx/elapsed_time:.2f} FPS).")

    # Merge audio from input video
    cmd_merge = [
        "ffmpeg", "-y",
        "-i", temp_output_path,
        "-i", input_video_path,
        "-map", "0:v",
        "-map", "1:a?",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        final_output_path
    ]
    merge_start = time.time()
    res = subprocess.run(cmd_merge, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode == 0:
        print(f"Audio merged successfully. Final output: {final_output_path} (ffmpeg took {time.time()-merge_start:.2f}s)")
        if os.path.exists(temp_output_path):
            os.remove(temp_output_path)
    else:
        print("Audio merge failed!")
        print(res.stderr.decode('utf-8', errors='replace'))
    
    return final_output_path

if __name__ == "__main__":
    # Test bicubic and both models on 1080p to show a comparison
    # We will use "output_lama.mp4" first if ProPainter is still running, 
    # but we will check what video file to use.
    video_source = "output_lama.mp4"
    if not os.path.exists(video_source):
        video_source = "Va Chạm Vui Nhộn.mp4" # Fallback if no dewatermarked video exists
        
    print(f"Selected video source for upscaling: {video_source}")
    
    # We can run these one by one or import them in another script.
    # For now, let's just make the functions available.
