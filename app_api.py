from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
import shutil
import os
import uuid
import json
import logging
from process_with_template import detect_profile, process_dewatermark
from upscale_video import upscale_video

# Configure logging so detect_profile print()s and errors appear in Uvicorn console
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("video_api")

# Create directories
os.makedirs("temp_uploads", exist_ok=True)
os.makedirs("extracted_frames", exist_ok=True)

app = FastAPI(
    title="Video Process API",
    description="Local HTTP API for automated ProPainter dewatermarking and Real-ESRGAN upscaling.",
    version="1.0.0"
)

# Load configuration once on startup
with open("watermark_templates.json", "r") as f:
    config = json.load(f)

def cleanup_files(file_paths: list):
    for path in file_paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                print(f"[API Cleanup] Deleted: {path}")
            except Exception as e:
                print(f"[API Cleanup] Error deleting {path}: {e}")

@app.post("/process")
def process_video_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    profile: str = Form(None),
    upscale: bool = Form(False),
    upscale_model: str = Form("realesr-animevideov3"),
    target_resolution: str = Form("1080p")
):
    # 1. Generate unique file IDs
    req_id = str(uuid.uuid4())
    input_filename = f"input_{req_id}_{file.filename}"
    input_path = os.path.join("temp_uploads", input_filename)
    
    # Save uploaded file
    try:
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {str(e)}")
        
    files_to_clean = [input_path]
    
    # 2. Match watermark profile
    profile_key = profile
    logger.info(f"[Request {req_id[:8]}] File: {file.filename}, Profile param: {profile}, Upscale: {upscale}")
    
    if not profile_key or profile_key == "auto":
        logger.info(f"[Request {req_id[:8]}] Running auto-detection on {input_path}...")
        profile_key = detect_profile(input_path, config)
        logger.info(f"[Request {req_id[:8]}] Auto-detection result: {profile_key}")
        
    if not profile_key:
        cleanup_files(files_to_clean)
        raise HTTPException(
            status_code=400, 
            detail=f"Auto-detection failed for '{file.filename}'. No watermark template matched above threshold. Try specifying a profile manually (dola_ai_bottom_right / veo_bottom_right_format / gemini_omni_format)."
        )
        
    if profile_key not in config:
        cleanup_files(files_to_clean)
        raise HTTPException(
            status_code=400,
            detail=f"Profile '{profile_key}' does not exist in templates configuration. Valid options: {list(config.keys())}"
        )
        
    # 3. Process Watermark Removal
    dewatermarked_filename = f"clean_{req_id}.mp4"
    dewatermarked_path = os.path.join("temp_uploads", dewatermarked_filename)
    
    print(f"\n[API] Running dewatermarking for profile: {profile_key}...")
    clean_result = process_dewatermark(input_path, profile_key, config, output_path=dewatermarked_path)
    
    if not clean_result or not os.path.exists(dewatermarked_path):
        cleanup_files(files_to_clean)
        raise HTTPException(status_code=500, detail="Watermark removal processing failed.")
        
    files_to_clean.append(dewatermarked_path)
    final_output_path = dewatermarked_path
    
    # 4. Optional Upscale Stage
    if upscale:
        print(f"\n[API] Running upscaling with model: {upscale_model}...")
        try:
            upscale_result = upscale_video(dewatermarked_path, upscale_model, target_resolution)
            if upscale_result and os.path.exists(upscale_result):
                final_output_path = upscale_result
                files_to_clean.append(final_output_path)
            else:
                print("[API Warning] Upscale failed, falling back to clean dewatermarked video.")
        except Exception as e:
            print(f"[API Error] Upscale exception: {str(e)}")
            
    # 5. Serve response and register cleanup task
    background_tasks.add_task(cleanup_files, files_to_clean)
    
    # Return file with clean download name
    download_name = f"clean_{file.filename}"
    if upscale:
        download_name = f"upscaled_{target_resolution}_{file.filename}"
        
    return FileResponse(
        path=final_output_path, 
        media_type="video/mp4",
        filename=download_name
    )

@app.get("/", response_class=HTMLResponse)
def get_web_ui():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Video Dewatermark & Upscale API Portal</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
        <style>
            :root {
                --primary: #6366f1;
                --primary-hover: #4f46e5;
                --bg: #0f172a;
                --card-bg: rgba(30, 41, 59, 0.7);
                --border: rgba(255, 255, 255, 0.08);
                --text: #f8fafc;
                --text-secondary: #94a3b8;
            }
            * {
                box-sizing: border-box;
                margin: 0;
                padding: 0;
                font-family: 'Outfit', sans-serif;
            }
            body {
                background: radial-gradient(circle at top right, #1e1b4b, #0f172a 60%);
                color: var(--text);
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                padding: 20px;
            }
            .container {
                max-width: 650px;
                width: 100%;
                background: var(--card-bg);
                backdrop-filter: blur(16px);
                border: 1px solid var(--border);
                border-radius: 24px;
                padding: 40px;
                box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
                transform: translateY(0);
                transition: all 0.3s ease;
            }
            h1 {
                font-size: 2rem;
                font-weight: 800;
                text-align: center;
                margin-bottom: 8px;
                background: linear-gradient(135deg, #a5b4fc, #6366f1);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }
            p.subtitle {
                text-align: center;
                color: var(--text-secondary);
                font-size: 0.95rem;
                margin-bottom: 30px;
            }
            .form-group {
                margin-bottom: 20px;
            }
            label {
                display: block;
                font-weight: 600;
                font-size: 0.85rem;
                text-transform: uppercase;
                letter-spacing: 0.05em;
                margin-bottom: 8px;
                color: var(--text-secondary);
            }
            select, input[type="text"] {
                width: 100%;
                background: rgba(15, 23, 42, 0.6);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 12px 16px;
                color: var(--text);
                outline: none;
                font-size: 0.95rem;
                transition: border-color 0.2s;
            }
            select:focus, input[type="text"]:focus {
                border-color: var(--primary);
            }
            .checkbox-group {
                display: flex;
                align-items: center;
                background: rgba(15, 23, 42, 0.4);
                border: 1px solid var(--border);
                border-radius: 12px;
                padding: 14px 16px;
                margin-bottom: 25px;
                cursor: pointer;
            }
            .checkbox-group input {
                margin-right: 12px;
                width: 18px;
                height: 18px;
                accent-color: var(--primary);
                cursor: pointer;
            }
            .checkbox-label {
                font-weight: 600;
                font-size: 0.95rem;
            }
            .dropzone {
                border: 2px dashed rgba(99, 102, 241, 0.4);
                border-radius: 16px;
                padding: 40px 20px;
                text-align: center;
                cursor: pointer;
                background: rgba(99, 102, 241, 0.03);
                transition: all 0.2s ease;
                margin-bottom: 25px;
            }
            .dropzone:hover, .dropzone.dragover {
                border-color: var(--primary);
                background: rgba(99, 102, 241, 0.08);
            }
            .dropzone svg {
                width: 48px;
                height: 48px;
                color: var(--primary);
                margin-bottom: 12px;
            }
            .dropzone p {
                font-size: 0.95rem;
                color: var(--text-secondary);
            }
            .dropzone p span {
                color: var(--primary);
                font-weight: 600;
            }
            .file-info {
                display: none;
                background: rgba(99, 102, 241, 0.1);
                border: 1px solid rgba(99, 102, 241, 0.2);
                border-radius: 12px;
                padding: 12px 16px;
                margin-bottom: 25px;
                align-items: center;
                justify-content: space-between;
            }
            .file-name {
                font-weight: 600;
                font-size: 0.9rem;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
                max-width: 80%;
            }
            .remove-file {
                color: #ef4444;
                font-weight: 600;
                cursor: pointer;
                font-size: 0.85rem;
            }
            button.submit-btn {
                width: 100%;
                background: var(--primary);
                color: white;
                border: none;
                border-radius: 12px;
                padding: 14px;
                font-size: 1rem;
                font-weight: 600;
                cursor: pointer;
                transition: background 0.2s, transform 0.1s;
                box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
            }
            button.submit-btn:hover {
                background: var(--primary-hover);
            }
            button.submit-btn:active {
                transform: scale(0.98);
            }
            button:disabled {
                background: #475569;
                box-shadow: none;
                cursor: not-allowed;
            }
            .progress-container {
                display: none;
                text-align: center;
                margin-top: 25px;
            }
            .spinner {
                width: 40px;
                height: 40px;
                border: 4px solid rgba(255, 255, 255, 0.1);
                border-top-color: var(--primary);
                border-radius: 50%;
                animation: spin 1s linear infinite;
                margin: 0 auto 15px auto;
            }
            .status-text {
                font-size: 0.95rem;
                color: var(--text-secondary);
            }
            @keyframes spin {
                to { transform: rotate(360deg); }
            }
            .upscale-options {
                display: none;
                animation: fadeIn 0.3s ease forwards;
            }
            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(-10px); }
                to { opacity: 1; transform: translateY(0); }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Video Restore Portal</h1>
            <p class="subtitle">Xóa watermark thông minh ProPainter & Siêu độ nét Real-ESRGAN</p>
            
            <form id="uploadForm">
                <div class="dropzone" id="dropzone">
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"></path>
                    </svg>
                    <p>Kéo thả video của bạn vào đây hoặc <span>chọn tệp tin</span></p>
                    <input type="file" id="fileInput" name="file" accept="video/*" style="display: none;">
                </div>
                
                <div class="file-info" id="fileInfo">
                    <span class="file-name" id="fileName">video.mp4</span>
                    <span class="remove-file" id="removeFile">Xóa</span>
                </div>
                
                <div class="form-group">
                    <label for="profile">Mẫu Watermark (Template)</label>
                    <select id="profile" name="profile">
                        <option value="auto">Tự động nhận diện (Auto-Detect)</option>
                        <option value="dola_ai_bottom_right">Dola AI (Bottom-Right, 720p)</option>
                        <option value="veo_bottom_right_format">Veo Watermark (Bottom-Right, Video 1)</option>
                        <option value="gemini_omni_format">Gemini Omni Logo (Bottom-Right, Video 2 HQ)</option>
                    </select>
                </div>
                
                <div class="checkbox-group" id="upscaleToggleGroup">
                    <input type="checkbox" id="upscale" name="upscale">
                    <span class="checkbox-label">Kích hoạt Siêu độ phân giải (Upscale 1080p)</span>
                </div>
                
                <div class="upscale-options" id="upscaleOptions">
                    <div class="form-group">
                        <label for="upscale_model">Mô hình Upscale (Model)</label>
                        <select id="upscale_model" name="upscale_model">
                            <option value="realesr-animevideov3">realesr-animevideov3 (Tốc độ cực nhanh)</option>
                            <option value="RealESRGAN_x4plus_anime_6B">RealESRGAN-Anime-6B (Siêu sắc nét, nét vẽ 3D)</option>
                        </select>
                    </div>
                </div>
                
                <button type="submit" class="submit-btn" id="submitBtn" disabled>Xử lý Video</button>
            </form>
            
            <div class="progress-container" id="progressContainer">
                <div class="spinner"></div>
                <p class="status-text" id="statusText">Đang tải tệp tin và chuẩn bị xử lý...</p>
            </div>
        </div>

        <script>
            const dropzone = document.getElementById('dropzone');
            const fileInput = document.getElementById('fileInput');
            const fileInfo = document.getElementById('fileInfo');
            const fileName = document.getElementById('fileName');
            const removeFile = document.getElementById('removeFile');
            const upscaleCheckbox = document.getElementById('upscale');
            const upscaleOptions = document.getElementById('upscaleOptions');
            const submitBtn = document.getElementById('submitBtn');
            const uploadForm = document.getElementById('uploadForm');
            const progressContainer = document.getElementById('progressContainer');
            const statusText = document.getElementById('statusText');
            
            let selectedFile = null;
            
            // Trigger file selection
            dropzone.addEventListener('click', () => fileInput.click());
            
            fileInput.addEventListener('change', (e) => {
                if (e.target.files.length > 0) {
                    handleFileSelection(e.target.files[0]);
                }
            });
            
            // Drag and drop event handlers
            dropzone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dropzone.classList.add('dragover');
            });
            
            dropzone.addEventListener('dragleave', () => {
                dropzone.classList.remove('dragover');
            });
            
            dropzone.addEventListener('drop', (e) => {
                e.preventDefault();
                dropzone.classList.remove('dragover');
                if (e.dataTransfer.files.length > 0) {
                    handleFileSelection(e.dataTransfer.files[0]);
                }
            });
            
            function handleFileSelection(file) {
                selectedFile = file;
                fileName.textContent = file.name;
                dropzone.style.display = 'none';
                fileInfo.style.display = 'flex';
                submitBtn.disabled = false;
            }
            
            removeFile.addEventListener('click', () => {
                selectedFile = null;
                fileInput.value = '';
                dropzone.style.display = 'block';
                fileInfo.style.display = 'none';
                submitBtn.disabled = true;
            });
            
            // Toggle upscale options display
            document.getElementById('upscaleToggleGroup').addEventListener('click', (e) => {
                if (e.target !== upscaleCheckbox) {
                    upscaleCheckbox.checked = !upscaleCheckbox.checked;
                }
                upscaleOptions.style.display = upscaleCheckbox.checked ? 'block' : 'none';
            });
            
            uploadForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                if (!selectedFile) return;
                
                // Hide button, show spinner
                submitBtn.style.display = 'none';
                progressContainer.style.display = 'block';
                
                const formData = new FormData();
                formData.append('file', selectedFile);
                formData.append('profile', document.getElementById('profile').value);
                formData.append('upscale', upscaleCheckbox.checked);
                formData.append('upscale_model', document.getElementById('upscale_model').value);
                formData.append('target_resolution', '1080p');
                
                let checkTimer = null;
                const statusMessages = [
                    "Đang phân tích watermark và tính toán dòng quang học...",
                    "Đang chạy inpainting ProPainter trên GPU...",
                    "Đang tái thiết lập nền video và khớp đè...",
                    "Đang xử lý đồng bộ kênh âm thanh...",
                ];
                
                if (upscaleCheckbox.checked) {
                    statusMessages.push("Đang chạy mô hình AI Super-Resolution (Real-ESRGAN)...");
                    statusMessages.push("Đang ghép đè và hoàn thiện âm thanh 1080p...");
                }
                
                let msgIdx = 0;
                statusText.textContent = statusMessages[0];
                checkTimer = setInterval(() => {
                    msgIdx = (msgIdx + 1) % statusMessages.length;
                    statusText.textContent = statusMessages[msgIdx];
                }, 10000);
                
                try {
                    const response = await fetch('/process', {
                        method: 'POST',
                        body: formData
                    });
                    
                    clearInterval(checkTimer);
                    
                    if (!response.ok) {
                        const errorData = await response.json();
                        alert(`Lỗi: ${errorData.detail || 'Xử lý video thất bại.'}`);
                        resetUI();
                        return;
                    }
                    
                    // Download processed video file
                    statusText.textContent = "Hoàn thành! Đang chuẩn bị tải file...";
                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.style.display = 'none';
                    a.href = url;
                    
                    let outName = "processed_" + selectedFile.name;
                    if (upscaleCheckbox.checked) outName = "upscaled_" + selectedFile.name;
                    
                    a.download = outName;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    
                    alert("Video xử lý thành công và đang được tải về máy!");
                    resetUI();
                    
                } catch (error) {
                    clearInterval(checkTimer);
                    alert(`Lỗi kết nối: ${error.message}`);
                    resetUI();
                }
            });
            
            function resetUI() {
                submitBtn.style.display = 'block';
                progressContainer.style.display = 'none';
            }
        </script>
    </body>
    </html>
    """
    return html_content
