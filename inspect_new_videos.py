import cv2
import numpy as np
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

video1 = "Cat_and_dog_leaving_shop_202607142342.mp4"
video2 = "Dog_grabs_wolf_throws_ground_202607142343.mp4"

os.makedirs("extracted_frames", exist_ok=True)

def analyze_video(video_path, name_prefix):
    if not os.path.exists(video_path):
        print(f"Error: {video_path} not found.")
        return
        
    cap = cv2.VideoCapture(video_path)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"\n--- Analyzing {video_path} ---")
    print(f"Resolution: {width}x{height}")
    print(f"FPS: {fps:.2f}")
    print(f"Frame Count: {frame_count}")
    
    # Read 30 frames evenly distributed to calculate temporal stats
    step = max(1, frame_count // 30)
    frames = []
    
    for i in range(30):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i * step)
        ret, frame = cap.read()
        if ret:
            frames.append(frame)
            
    cap.release()
    
    if not frames:
        print("Failed to read frames.")
        return
        
    frames_arr = np.array(frames) # Shape: (N, H, W, 3)
    
    # Save the first frame
    first_frame_path = f"extracted_frames/{name_prefix}_first_frame.png"
    cv2.imwrite(first_frame_path, frames[0])
    print(f"Saved first frame: {first_frame_path}")
    
    # Calculate temporal average and variance across frames
    # High average brightness + low temporal variance = Static watermark!
    mean_img = np.mean(frames_arr, axis=0).astype(np.uint8)
    std_img = np.std(frames_arr, axis=0).astype(np.uint8)
    
    # Save stats images for visualization
    cv2.imwrite(f"extracted_frames/{name_prefix}_mean.png", mean_img)
    cv2.imwrite(f"extracted_frames/{name_prefix}_std.png", std_img)
    
    # Let's search the 4 corners of the variance (std) image to find where the watermark is.
    # We define corners as 20% of width and 20% of height.
    h_c, w_c = int(height * 0.20), int(width * 0.20)
    corners = {
        "top_left": (0, h_c, 0, w_c),
        "top_right": (0, h_c, width-w_c, width),
        "bottom_left": (height-h_c, height, 0, w_c),
        "bottom_right": (height-h_c, height, width-w_c, width)
    }
    
    print("Searching corners for static elements (low variance / low std dev)...")
    for corner_name, (y0, y1, x0, x1) in corners.items():
        # Crop the corner from mean and std image
        corner_mean = mean_img[y0:y1, x0:x1]
        corner_std = std_img[y0:y1, x0:x1]
        
        # In the std image, moving background has high values, static parts have very low values (~0)
        # We can find static bright pixels: mean > 180 (bright/white) and std < 15 (static)
        gray_mean = cv2.cvtColor(corner_mean, cv2.COLOR_BGR2GRAY)
        gray_std = cv2.cvtColor(corner_std, cv2.COLOR_BGR2GRAY)
        
        static_bright = (gray_mean > 160) & (gray_std < 20)
        num_static_pixels = np.sum(static_bright)
        
        # If there are significant static bright pixels, this is likely the watermark!
        if num_static_pixels > 100:
            print(f"-> Potential Watermark detected in [{corner_name.upper()}] corner!")
            # Find the bounding box of the static bright pixels in local coordinates
            ys, xs = np.where(static_bright)
            loc_x_min, loc_x_max = np.min(xs), np.max(xs)
            loc_y_min, loc_y_max = np.min(ys), np.max(ys)
            
            # Convert to global coordinates
            global_x_min = x0 + loc_x_min
            global_x_max = x0 + loc_x_max
            global_y_min = y0 + loc_y_min
            global_y_max = y0 + loc_y_max
            
            # Let's expand slightly for safety
            global_x_min = max(0, global_x_min - 5)
            global_x_max = min(width, global_x_max + 5)
            global_y_min = max(0, global_y_min - 5)
            global_y_max = min(height, global_y_max + 5)
            
            w_w = global_x_max - global_x_min
            w_h = global_y_max - global_y_min
            
            print(f"   - Bounding Box global: X in [{global_x_min}, {global_x_max}], Y in [{global_y_min}, {global_y_max}]")
            print(f"   - Size: {w_w}x{w_h}")
            
            # Let's save a crop of the watermark from the mean image as template
            watermark_crop = mean_img[global_y_min:global_y_max, global_x_min:global_x_max]
            template_path = f"extracted_frames/{name_prefix}_{corner_name}_template.png"
            cv2.imwrite(template_path, watermark_crop)
            print(f"   - Saved watermark template: {template_path}")
            
            # Draw rectangle on mean image for inspection
            annotated = mean_img.copy()
            cv2.rectangle(annotated, (global_x_min, global_y_min), (global_x_max, global_y_max), (0, 0, 255), 2)
            cv2.imwrite(f"extracted_frames/{name_prefix}_detected_bbox.png", annotated)

analyze_video(video1, "video1")
analyze_video(video2, "video2")
