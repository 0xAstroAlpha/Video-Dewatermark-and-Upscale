# Video Dewatermark & Upscale Pipeline

Hệ thống xử lý video chuyên dụng giúp tự động **xóa watermark/logo tĩnh hoặc bán trong suốt** (bằng mô hình ProPainter & LaMa) và **siêu độ phân giải (Upscale)** video hoạt hình 3D từ 720p lên 1080p/2K (bằng Real-ESRGAN).

Hệ thống được tối ưu hóa đặc biệt cho GPU có VRAM trung bình (như RTX 3060 12GB), sử dụng giải pháp **Crop-Process-Overlay** giúp tăng tốc độ xử lý lên **hơn 10 lần** và tiết kiệm VRAM.

---

## 🚀 Các Tính Năng Nổi Bật

1.  **Template-Based Automation (Nhận diện mẫu tự động)**:
    *   Tự động so khớp mẫu logo tĩnh (Template Matching) trên nhiều khung hình để chọn cấu hình xử lý phù hợp.
    *   Hỗ trợ video có nhiều watermark ở các góc khác nhau (chạy nối tiếp dạng pipeline).
2.  **Crop-Process-Overlay (Tối ưu hóa GPU)**:
    *   Chỉ cắt và xử lý vùng chứa watermark trên GPU để giảm tải điểm ảnh xử lý.
    *   Tự động tính toán vùng đệm (margin) cho mạng RAFT bám dấu quang học khi camera di chuyển.
3.  **High-Quality Motion Profile (Cấu hình chuyển động nhanh)**:
    *   Hỗ trợ tham số chuyên sâu cho video lia máy nhanh (Raft Iterations=20, Stride=10) để tránh lỗi bóng ma (ghosting) hoặc méo hình.
4.  **Anime Upscaler**:
    *   Tích hợp model `RealESRGAN_x4plus_anime_6B` chuyên dụng để làm sắc nét viền vẽ và giữ nguyên chiều sâu khối 3D.

---

## 📂 Danh Sách Mã Nguồn Chính

*   `process_with_template.py`: Script API chính. Tự động nhận diện watermark từ cấu hình JSON, tạo mask, crop vùng ảnh, chạy ProPainter và overlay ngược lại.
*   `watermark_templates.json`: File cấu hình lưu trữ tọa độ watermark, tọa độ crop tối ưu, đường dẫn ảnh mẫu và tham số chạy của từng profile.
*   `inspect_new_videos.py`: Công cụ phân tích tự động phát hiện vị trí watermark bằng thuật toán tính toán phương sai thời gian (Temporal Variance).
*   `upscale_video.py`: Script chạy siêu độ phân giải 1080p/2K sử dụng Real-ESRGAN GPU.
*   `benchmark_super_opt.py`: Script chạy đo đạc hiệu năng các phương án tối ưu hóa vùng crop.

---

## 🛠️ Hướng Dẫn Cài Đặt

### 1. Cài đặt môi trường
Yêu cầu Python 3.9+ và CUDA 11.8+.

```bash
# Cài đặt các thư viện cơ bản
pip install -r requirements.txt

# Cài đặt ProPainter Dependencies
cd ProPainter
pip install -r requirements.txt
```

### 2. Tải Weights cho các Model
*   **ProPainter**: Tải các file weights `ProPainter.pth` và `recurrent_flow.pth` bỏ vào thư mục `ProPainter/weights/`.
*   **Real-ESRGAN**: Tải model `RealESRGAN_x4plus_anime_6B.pth` và `realesr-animevideov3.pth` bỏ vào thư mục `weights/`.

---

## 💻 Hướng Dẫn Sử Dụng

### 1. Xóa Watermark tự động bằng Profile mẫu
Để chạy xử lý xóa logo trên video mới, bạn chỉ cần chạy:

```bash
python process_with_template.py <duong_dan_video_cua_ban.mp4>
```

Hệ thống sẽ:
1.  Quét các frame 0, 50, 100, 150 để khớp mẫu watermark.
2.  Tự động chọn profile phù hợp (ví dụ: `veo_bottom_right_format` cho chữ "Veo" ở góc dưới bên phải).
3.  Thực hiện crop, xóa logo bằng ProPainter GPU và ghép đè ngược lại video gốc.
4.  Merge âm thanh gốc và xuất file sạch: `output_clean_<ten_video>.mp4`.

### 2. Tạo Profile Watermark mới cho hệ thống
Nếu gặp một dạng video có watermark mới:
1.  Chạy công cụ phân tích để dò tìm tọa độ tự động:
    ```bash
    python inspect_new_videos.py
    ```
2.  Lấy tọa độ watermark xuất ra ở console và ảnh mẫu template cắt ra trong thư mục `extracted_frames/`.
3.  Cập nhật thông tin tọa độ mở rộng (chia hết cho 8) vào tệp `watermark_templates.json`.

### 3. Siêu độ phân giải (Upscale) lên 1080p/2K
Chạy tăng nét video sau khi đã xóa logo sạch sẽ:

```bash
python upscale_video.py --video <ten_video_sach.mp4> --model RealESRGAN_x4plus_anime_6B
```
*   `RealESRGAN_x4plus_anime_6B`: Cho chất lượng nét nhất, giữ khối 3D tốt.
*   `realesr-animevideov3`: Tốc độ cực nhanh (nhanh hơn 18 lần), thích hợp xử lý hàng loạt.
