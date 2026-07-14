import cv2
import numpy as np
import os
import sys
import io
import json
import time
import subprocess

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def detect_profile(video_path, templates_config):
    cap = cv2.VideoCapture(video_path)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    # Read a few key frames across the video for robust template matching
    test_frames_indices = [0, 50, 100, 150]
    test_frames = []
    
    for idx in test_frames_indices:
        if idx < frame_count:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if ret:
                test_frames.append(frame)
    cap.release()
    
    if not test_frames:
        print(f"Error: Could not read frames from {video_path}")
        return None
        
    print(f"Analyzing {video_path} across multiple frames for watermark template match...")
    
    best_match = None
    max_val_found = 0.0
    
    for profile_key, profile in templates_config.items():
        for wm in profile["watermarks"]:
            template_path = wm["template_image"]
            if not os.path.exists(template_path):
                alt_path = os.path.join("extracted_frames", os.path.basename(template_path))
                if os.path.exists(alt_path):
                    template_path = alt_path
                else:
                    continue
                    
            template = cv2.imread(template_path)
            if template is None:
                continue
                
            x, y, w, h = wm["watermark_bbox"]
            margin = 30
            
            # Find the best match across all test frames
            best_frame_score = 0.0
            for frame in test_frames:
                y0 = max(0, y - margin)
                y1 = min(frame.shape[0], y + h + margin)
                x0 = max(0, x - margin)
                x1 = min(frame.shape[1], x + w + margin)
                
                search_area = frame[y0:y1, x0:x1]
                res = cv2.matchTemplate(search_area, template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(res)
                if max_val > best_frame_score:
                    best_frame_score = max_val
                    
            print(f"  - Matching '{profile_key}' ({wm['position']}): Max Score = {best_frame_score:.3f}")
            if best_frame_score > 0.80 and best_frame_score > max_val_found:
                max_val_found = best_frame_score
                best_match = profile_key
                
    if best_match:
        print(f"-> SUCCESS: Matched profile '{best_match}' with score {max_val_found:.3f}")
        return best_match
    else:
        print("-> WARNING: No matching watermark profile found.")
        return None

def generate_local_mask(crop_frame, wm_bbox, crop_bbox, mask_params):
    # crop_bbox = [x_crop, y_crop, w_crop, h_crop]
    # wm_bbox = [x_wm, y_wm, w_wm, h_wm]
    x_c, y_c, _, _ = crop_bbox
    x_w, y_w, w_w, h_w = wm_bbox
    
    # Local watermark coordinates inside the cropped frame
    local_x = x_w - x_c
    local_y = y_w - y_c
    
    # 1. Color thresholding on BGR/Gray to isolate white pixels
    gray = cv2.cvtColor(crop_frame, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, mask_params.get("lower_bright", 160), 255, cv2.THRESH_BINARY)
    
    # 2. Restrict the mask *only* inside the local watermark bounding box
    local_mask = np.zeros_like(thresh)
    local_mask[local_y:local_y+h_w, local_x:local_x+w_w] = thresh[local_y:local_y+h_w, local_x:local_x+w_w]
    
    # 3. Dilation to cover drop shadows
    kernel = np.ones((3, 3), np.uint8)
    dilated = cv2.dilate(local_mask, kernel, iterations=mask_params.get("dilation_iter", 4))
    
    return dilated

def process_dewatermark(video_path, profile_key, templates_config):
    profile = templates_config[profile_key]
    print(f"\nProcessing video: {video_path}")
    print(f"Profile: {profile['name']}")
    
    current_video_source = video_path
    
    # We will process each watermark sequentially
    for idx, wm in enumerate(profile["watermarks"]):
        print(f"\n--- Processing Watermark {idx+1}/{len(profile['watermarks'])} ({wm['position']}) ---")
        
        # Bounding boxes
        x_c, y_c, w_c, h_c = wm["crop_bbox"]
        
        temp_crop_video = f"temp_crop_run.mp4"
        temp_mask_png = f"extracted_frames/temp_mask_run.png"
        temp_overlay_video = f"temp_overlay_run.mp4"
        next_source_video = f"temp_source_step_{idx}.mp4" if idx < len(profile["watermarks"]) - 1 else "output_clean_templated.mp4"
        
        # 1. Read source and crop target area, generating local mask frame-by-frame
        cap = cv2.VideoCapture(current_video_source)
        fps = cap.get(cv2.CAP_PROP_FPS)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out_crop = cv2.VideoWriter(temp_crop_video, fourcc, fps, (w_c, h_c))
        
        # We need a static mask (ProPainter takes a single image if it is static)
        # We'll calculate the dynamic mask for the first frame as the static mask template
        ret, first_frame = cap.read()
        if not ret:
            print("Failed to read video.")
            cap.release()
            return
            
        first_crop = first_frame[y_c:y_c+h_c, x_c:x_c+w_c]
        mask_dilated = generate_local_mask(first_crop, wm["watermark_bbox"], wm["crop_bbox"], wm["mask_params"])
        cv2.imwrite(temp_mask_png, mask_dilated)
        
        # Write cropped video
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            crop = frame[y_c:y_c+h_c, x_c:x_c+w_c]
            out_crop.write(crop)
            
        cap.release()
        out_crop.release()
        
        # 2. Run ProPainter on the crop
        propainter_dir = os.path.abspath("ProPainter")
        cmd = [
            "python", "inference_propainter.py",
            "--video", f"../{temp_crop_video}",
            "--mask", f"../{temp_mask_png}",
            "--output", f"results_run",
            "--subvideo_length", "40",
            "--raft_iter", str(wm["model_params"]["raft_iter"]),
            "--ref_stride", str(wm["model_params"]["ref_stride"]),
            "--neighbor_length", str(wm["model_params"]["neighbor_length"]),
            "--fp16"
        ]
        
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        print(f"Executing ProPainter on crop {w_c}x{h_c}...")
        t0 = time.time()
        result = subprocess.run(cmd, cwd=propainter_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        t1 = time.time()
        print(f"ProPainter completed in {t1-t0:.2f} seconds.")
        
        if result.returncode != 0:
            print("Error: ProPainter execution failed!")
            print("Stderr:", result.stderr.decode('utf-8', errors='replace'))
            return
            
        propainter_out_file = os.path.join(propainter_dir, "results_run", "temp_crop_run", "inpaint_out.mp4")
        if not os.path.exists(propainter_out_file):
            print("Error: ProPainter output not found.")
            return
            
        # 3. Overlay back
        print("Overlaying back onto original frames...")
        cap_orig = cv2.VideoCapture(current_video_source)
        cap_prop = cv2.VideoCapture(propainter_out_file)
        
        orig_w = int(cap_orig.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap_orig.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out_overlay = cv2.VideoWriter(temp_overlay_video, fourcc, fps, (orig_w, orig_h))
        
        while True:
            ret_orig, frame_orig = cap_orig.read()
            ret_prop, frame_prop = cap_prop.read()
            if not ret_orig or not ret_prop:
                break
            
            if frame_prop.shape[1] != w_c or frame_prop.shape[0] != h_c:
                frame_prop = cv2.resize(frame_prop, (w_c, h_c))
                
            processed = frame_orig.copy()
            processed[y_c:y_c+h_c, x_c:x_c+w_c] = frame_prop
            out_overlay.write(processed)
            
        cap_orig.release()
        cap_prop.release()
        out_overlay.release()
        
        # 4. Merge audio and save as next source
        print("Saving intermediate result...")
        cmd_merge = [
            "ffmpeg", "-y",
            "-i", temp_overlay_video,
            "-i", current_video_source,
            "-map", "0:v",
            "-map", "1:a?",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            next_source_video
        ]
        subprocess.run(cmd_merge, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Cleanup loop temp files
        if os.path.exists(temp_crop_video): os.remove(temp_crop_video)
        if os.path.exists(temp_mask_png): os.remove(temp_mask_png)
        if os.path.exists(temp_overlay_video): os.remove(temp_overlay_video)
        
        # If there was a previous step file, clean it up
        if idx > 0 and os.path.exists(current_video_source) and "temp_source_step_" in current_video_source:
            os.remove(current_video_source)
            
        current_video_source = next_source_video
        
    print(f"\nSUCCESS: Processing completed! Output saved to: {current_video_source}")
    return current_video_source

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python process_with_template.py <input_video_path>")
        exit(1)
        
    input_video = sys.argv[1]
    
    # Load configuration
    with open("watermark_templates.json", "r") as f:
        config = json.load(f)
        
    # Detect profile
    profile_key = detect_profile(input_video, config)
    
    if profile_key:
        # Run dewatermarking
        output = process_dewatermark(input_video, profile_key, config)
        if output:
            # Rename output to a more descriptive name
            final_name = f"output_clean_{os.path.basename(input_video)}"
            if os.path.exists(final_name):
                os.remove(final_name)
            os.rename(output, final_name)
            print(f"Final video renamed to: {final_name}")
    else:
        print("Exiting because no matching profile was found.")
