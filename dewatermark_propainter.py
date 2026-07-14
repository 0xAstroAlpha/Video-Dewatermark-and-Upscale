import subprocess
import time
import cv2
import numpy as np
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Quadrant Crop Coordinates (Bottom-Right 640x360 quadrant of 1280x720 video)
quad_x_start, quad_x_end = 640, 1280
quad_y_start, quad_y_end = 360, 720
quad_width = quad_x_end - quad_x_start
quad_height = quad_y_end - quad_y_start

original_video_path = "Va Chạm Vui Nhộn.mp4"
mask_full_path = "extracted_frames/watermark_mask_full.png"

# Temp files
temp_quadrant_path = "temp_quadrant.mp4"
mask_quadrant_path = "extracted_frames/watermark_mask_quadrant.png"
temp_output_path = "temp_propainter.mp4"
final_output_path = "output_propainter.mp4"

print("--- Preparing Quadrant Crop for ProPainter Optimization ---")
start_time = time.time()

# 1. Create temp_quadrant.mp4
cap = cv2.VideoCapture(original_video_path)
fps = cap.get(cv2.CAP_PROP_FPS)
frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
out_quad = cv2.VideoWriter(temp_quadrant_path, fourcc, fps, (quad_width, quad_height))

while True:
    ret, frame = cap.read()
    if not ret:
        break
    crop_quad = frame[quad_y_start:quad_y_end, quad_x_start:quad_x_end]
    out_quad.write(crop_quad)

cap.release()
out_quad.release()
print(f"Created quadrant crop video: {temp_quadrant_path}")

# 2. Create watermark_mask_quadrant.png
mask_full = cv2.imread(mask_full_path)
mask_quad = mask_full[quad_y_start:quad_y_end, quad_x_start:quad_x_end]
cv2.imwrite(mask_quadrant_path, mask_quad)
print(f"Created quadrant mask image: {mask_quadrant_path}")

# 3. Run ProPainter Inference on Quadrant Crop
propainter_dir = os.path.abspath("ProPainter")
cmd = [
    "python", "inference_propainter.py",
    "--video", "../temp_quadrant.mp4",
    "--mask", "../" + mask_quadrant_path,
    "--output", "results",
    "--fp16"
]

print("\nStarting ProPainter inference background command...")
print("Command:", " ".join(cmd))
print("Working directory:", propainter_dir)

env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"

# Run the command
result = subprocess.run(cmd, cwd=propainter_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

if result.returncode != 0:
    print("Error: ProPainter inference failed!")
    print("Stdout:", result.stdout.decode('utf-8', errors='replace'))
    print("Stderr:", result.stderr.decode('utf-8', errors='replace'))
    # Clean up temp quadrant files anyway
    if os.path.exists(temp_quadrant_path): os.remove(temp_quadrant_path)
    if os.path.exists(mask_quadrant_path): os.remove(mask_quadrant_path)
    exit(1)

print("ProPainter inference completed successfully.")
print(result.stdout.decode('utf-8', errors='replace')[-500:])

# Expected output file from ProPainter
propainter_output_path = os.path.join(propainter_dir, "results", "temp_quadrant", "inpaint_out.mp4")

if not os.path.exists(propainter_output_path):
    print(f"Error: Output file not found at {propainter_output_path}")
    if os.path.exists(temp_quadrant_path): os.remove(temp_quadrant_path)
    if os.path.exists(mask_quadrant_path): os.remove(mask_quadrant_path)
    exit(1)

# 4. Perform overlay back onto the original full video
print("\nOverlaying ProPainter quadrant back on the original video...")
cap_orig = cv2.VideoCapture(original_video_path)
cap_prop = cv2.VideoCapture(propainter_output_path)

width = int(cap_orig.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap_orig.get(cv2.CAP_PROP_FRAME_HEIGHT))
out_full = cv2.VideoWriter(temp_output_path, fourcc, fps, (width, height))

frame_idx = 0
while True:
    ret_orig, frame_orig = cap_orig.read()
    ret_prop, frame_prop = cap_prop.read()
    
    if not ret_orig or not ret_prop:
        break
    
    # Resize inpaint quadrant if needed (should match 640x360)
    if frame_prop.shape[1] != quad_width or frame_prop.shape[0] != quad_height:
        frame_prop = cv2.resize(frame_prop, (quad_width, quad_height))
        
    # Paste quadrant crop back onto the original frame
    processed_frame = frame_orig.copy()
    processed_frame[quad_y_start:quad_y_end, quad_x_start:quad_x_end] = frame_prop
    
    out_full.write(processed_frame)
    frame_idx += 1

cap_orig.release()
cap_prop.release()
out_full.release()

total_time = time.time() - start_time
print(f"Overlay complete. Processed {frame_idx} frames.")
print(f"Total time (Inference + Overlay): {total_time:.2f} seconds ({frame_idx/total_time:.2f} FPS overall)")

# 5. Merge audio using FFmpeg
cmd_merge = [
    "ffmpeg", "-y",
    "-i", temp_output_path,
    "-i", original_video_path,
    "-map", "0:v",
    "-map", "1:a?",
    "-c:v", "libx264",
    "-pix_fmt", "yuv420p",
    "-c:a", "copy",
    final_output_path
]

merge_start = time.time()
res = subprocess.run(cmd_merge, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# Cleanup temporary files
if os.path.exists(temp_quadrant_path): os.remove(temp_quadrant_path)
if os.path.exists(mask_quadrant_path): os.remove(mask_quadrant_path)
if os.path.exists(temp_output_path): os.remove(temp_output_path)

if res.returncode == 0:
    print(f"Audio merged successfully. Final output saved to: {final_output_path} (ffmpeg encoding took {time.time()-merge_start:.2f}s)")
else:
    print("Audio merge failed!")
    print(res.stderr.decode('utf-8', errors='replace'))
