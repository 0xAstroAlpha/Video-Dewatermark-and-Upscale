from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from contextlib import asynccontextmanager
import asyncio
import shutil
import os
import re
import uuid
import json
import time
import logging
import subprocess
from urllib.parse import unquote
from process_with_template import detect_profile, process_dewatermark
from upscale_video import upscale_video

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB hard limit
PROCESS_TIMEOUT_SEC = 600             # 10-minute ProPainter timeout
TEMP_DIR = "temp_uploads"
FRAMES_DIR = "extracted_frames"

# GPU semaphore — only 1 job runs on GPU at a time; others queue
_gpu_semaphore = asyncio.Semaphore(1)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("video_api")

# ─────────────────────────────────────────────────────────────────────────────
# STARTUP / SHUTDOWN LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────
def _startup_cleanup():
    """Remove stale temp files and orphaned ProPainter result dirs on startup."""
    # Clean temp_uploads older than 1 hour
    if os.path.isdir(TEMP_DIR):
        cutoff = time.time() - 3600
        for f in os.listdir(TEMP_DIR):
            fp = os.path.join(TEMP_DIR, f)
            try:
                if os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
                    logger.info(f"[Startup Cleanup] Removed stale temp: {f}")
            except Exception:
                pass
    # Clean orphaned ProPainter results dirs
    propainter_dir = os.path.abspath("ProPainter")
    if os.path.isdir(propainter_dir):
        for d in os.listdir(propainter_dir):
            if d.startswith("results_run_"):
                dp = os.path.join(propainter_dir, d)
                try:
                    shutil.rmtree(dp)
                    logger.info(f"[Startup Cleanup] Removed orphaned ProPainter dir: {d}")
                except Exception:
                    pass

def _check_ffmpeg():
    """Warn if ffmpeg is not available in PATH."""
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        logger.info("[Startup] ffmpeg found in PATH ✓")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("[Startup] WARNING: ffmpeg NOT found in PATH! Audio merge will fail.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(FRAMES_DIR, exist_ok=True)
    _startup_cleanup()
    _check_ffmpeg()
    logger.info("[Startup] Video Dewatermark & Upscale API ready on port 8288")
    yield
    # On shutdown — nothing special needed

# ─────────────────────────────────────────────────────────────────────────────
# APP + CONFIG
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Video Process API",
    description="Local HTTP API for automated ProPainter dewatermarking and Real-ESRGAN upscaling.",
    version="2.0.0",
    lifespan=lifespan
)

# Load watermark templates once
with open("watermark_templates.json", "r") as f:
    config = json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _sanitize_filename(name: str) -> str:
    """Decode URL encoding and strip unsafe characters to prevent path traversal."""
    name = unquote(name)
    name = os.path.basename(name)  # strip any directory component
    name = re.sub(r'[^\w\-. ]', '_', name)  # keep safe chars only
    return name.strip() or "upload"

def _validate_video(path: str) -> tuple[bool, str]:
    """Return (ok, error_message). Checks file is a readable video with frames."""
    import cv2
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return False, "File is not a valid video or is corrupted."
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    if frame_count <= 0:
        return False, f"Video has 0 frames (frame_count={frame_count})."
    if fps <= 0:
        return False, f"Video has invalid FPS ({fps})."
    return True, ""

def cleanup_files(file_paths: list):
    """Delete a list of files safely — used as a BackgroundTask after response sent."""
    for path in file_paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                logger.info(f"[Cleanup] Deleted: {path}")
            except Exception as e:
                logger.warning(f"[Cleanup] Could not delete {path}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/process")
async def process_video_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    profile: str = Form(None),
    upscale: bool = Form(False),
    upscale_model: str = Form("realesr-animevideov3"),
    target_resolution: str = Form("1080p")
):
    req_id = str(uuid.uuid4())
    short_id = req_id[:8]

    # ── 1. File size pre-check ────────────────────────────────────────────────
    content_length = file.size  # FastAPI sets this from Content-Length
    if content_length and content_length > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {content_length / 1e6:.1f} MB. Maximum is {MAX_UPLOAD_BYTES // 1024 // 1024} MB."
        )

    # ── 2. Save uploaded file ─────────────────────────────────────────────────
    safe_filename = _sanitize_filename(file.filename or "upload.mp4")
    input_filename = f"input_{req_id}_{safe_filename}"
    input_path = os.path.join(TEMP_DIR, input_filename)

    try:
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {e}")

    # Enforce size limit again after full write (in case Content-Length was missing)
    actual_size = os.path.getsize(input_path)
    if actual_size > MAX_UPLOAD_BYTES:
        os.remove(input_path)
        raise HTTPException(
            status_code=413,
            detail=f"File too large after upload: {actual_size / 1e6:.1f} MB. Maximum is {MAX_UPLOAD_BYTES // 1024 // 1024} MB."
        )

    files_to_clean = [input_path]
    logger.info(f"[{short_id}] Received: '{safe_filename}' ({actual_size / 1e6:.1f} MB) | profile={profile} upscale={upscale}")

    # ── 3. Validate video ─────────────────────────────────────────────────────
    valid, err_msg = _validate_video(input_path)
    if not valid:
        cleanup_files(files_to_clean)
        raise HTTPException(status_code=400, detail=f"Invalid video file: {err_msg}")

    # ── 4. Profile detection ──────────────────────────────────────────────────
    profile_key = profile
    if not profile_key or profile_key == "auto":
        logger.info(f"[{short_id}] Running auto-detection...")
        profile_key = detect_profile(input_path, config)
        logger.info(f"[{short_id}] Auto-detection result: {profile_key}")

    if not profile_key:
        cleanup_files(files_to_clean)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Auto-detection failed for '{safe_filename}'. "
                f"No watermark template matched. "
                f"Specify profile manually: {list(config.keys())}"
            )
        )

    if profile_key not in config:
        cleanup_files(files_to_clean)
        raise HTTPException(
            status_code=400,
            detail=f"Unknown profile '{profile_key}'. Valid options: {list(config.keys())}"
        )

    # ── 5. GPU-locked processing (semaphore) ──────────────────────────────────
    dewatermarked_filename = f"clean_{req_id}.mp4"
    dewatermarked_path = os.path.join(TEMP_DIR, dewatermarked_filename)

    logger.info(f"[{short_id}] Waiting for GPU slot...")
    async with _gpu_semaphore:
        logger.info(f"[{short_id}] GPU slot acquired. Processing profile: {profile_key}")
        try:
            # Run blocking CPU/GPU work in a thread so the event loop stays alive
            loop = asyncio.get_event_loop()
            clean_result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: process_dewatermark(
                    input_path, profile_key, config, output_path=dewatermarked_path
                )),
                timeout=PROCESS_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            cleanup_files(files_to_clean)
            raise HTTPException(
                status_code=504,
                detail=f"Processing timed out after {PROCESS_TIMEOUT_SEC // 60} minutes. Video may be too long."
            )
        except Exception as e:
            logger.error(f"[{short_id}] Dewatermark exception: {e}", exc_info=True)
            cleanup_files(files_to_clean)
            raise HTTPException(status_code=500, detail=f"Watermark removal failed: {e}")

    if not clean_result or not os.path.exists(dewatermarked_path):
        cleanup_files(files_to_clean)
        raise HTTPException(
            status_code=500,
            detail="ProPainter produced no output. Check server logs for details."
        )

    files_to_clean.append(dewatermarked_path)
    final_output_path = dewatermarked_path
    logger.info(f"[{short_id}] Dewatermarking complete.")

    # ── 6. Optional upscale ───────────────────────────────────────────────────
    if upscale:
        logger.info(f"[{short_id}] Running upscale with model: {upscale_model}")
        try:
            upscale_result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: upscale_video(dewatermarked_path, upscale_model, target_resolution)
            )
            if upscale_result and os.path.exists(upscale_result):
                final_output_path = upscale_result
                files_to_clean.append(upscale_result)
                logger.info(f"[{short_id}] Upscale complete: {upscale_result}")
            else:
                logger.warning(f"[{short_id}] Upscale produced no output; returning clean video without upscale.")
        except Exception as e:
            logger.error(f"[{short_id}] Upscale failed: {e}")
            # Non-fatal: fall back to the dewatermarked video

    # ── 7. Return file & schedule cleanup ─────────────────────────────────────
    download_name = f"upscaled_{target_resolution}_{safe_filename}" if upscale else f"clean_{safe_filename}"
    background_tasks.add_task(cleanup_files, files_to_clean)
    logger.info(f"[{short_id}] Sending response: {download_name}")

    return FileResponse(
        path=final_output_path,
        media_type="video/mp4",
        filename=download_name
    )


@app.get("/health")
def health_check():
    """Simple health check endpoint — returns queue depth and config info."""
    return {
        "status": "ok",
        "gpu_slots_available": _gpu_semaphore._value,
        "profiles": list(config.keys()),
        "max_upload_mb": MAX_UPLOAD_BYTES // 1024 // 1024,
        "process_timeout_sec": PROCESS_TIMEOUT_SEC,
    }

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
