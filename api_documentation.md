# API Documentation: Video Dewatermark & Upscale Service

Tài liệu này đặc tả chi tiết về dịch vụ API cục bộ hỗ trợ tự động xóa watermark (sử dụng ProPainter) và siêu độ phân giải video (sử dụng Real-ESRGAN). Bạn có thể sao chép tài liệu này hoặc tệp cấu hình OpenAPI JSON bên dưới để nạp vào hệ thống Bot hỗ trợ của bạn.

---

## 🌐 1. Thông Tin Tổng Quan (Overview)
*   **Base URL**: `http://127.0.0.1:8080` (hoặc `http://localhost:8080`)
*   **Giao thức**: HTTP/1.1
*   **Định dạng dữ liệu gửi**: `multipart/form-data`
*   **Định dạng phản hồi**: Binary Stream (`video/mp4`)

---

## 🛠️ 2. Các Endpoint Chi Tiết (Endpoints)

### 2.1 Cổng thông tin Web (Web Portal)
Trả về giao diện đồ họa Glassmorphism Portal để người dùng thao tác trực tiếp trên trình duyệt.
*   **Method**: `GET`
*   **Path**: `/`
*   **Headers**: `Accept: text/html`
*   **Phản hồi (Response)**: `200 OK` (Mã HTML/CSS/JS)

---

### 2.2 Xử lý Video (Process Video)
Nhận tệp tin video thô đầu vào, tiến hành phân tích nhận dạng logo, chạy mô hình inpaint ProPainter để xóa và phóng to video nếu được yêu cầu. Sau đó trả về file video sạch.
*   **Method**: `POST`
*   **Path**: `/process`
*   **Content-Type**: `multipart/form-data`

#### 📥 Tham số đầu vào (Form Parameters)

| Tham số | Kiểu dữ liệu | Bắt buộc | Mặc định | Mô tả chi tiết |
| :--- | :--- | :--- | :--- | :--- |
| **`file`** | Binary | **Có** | | Tệp video raw định dạng `.mp4`, `.avi`, `.mkv`... cần xử lý. |
| **`profile`** | String | Không | `"auto"` | Mã template cần áp dụng. Nhận các giá trị:<br>- `"auto"`: Tự động quét khung hình để khớp template phù hợp.<br>- `"dola_ai_bottom_right"`: Logo Dola AI.<br>- `"veo_bottom_right_format"`: Logo Veo.<br>- `"gemini_omni_format"`: Logo Gemini Omni. |
| **`upscale`** | Boolean | Không | `false` | Bật/tắt chế độ siêu độ phân giải (Upscale lên 1080p). |
| **`upscale_model`** | String | Không | `"realesr-animevideov3"` | Mô hình upscale áp dụng:<br>- `"realesr-animevideov3"`: Tốc độ cực nhanh (khuyên dùng).<br>- `"RealESRGAN_x4plus_anime_6B"`: Chất lượng nét vẽ 3D điện ảnh. |
| **`target_resolution`** | String | Không | `"1080p"` | Độ phân giải đầu ra mong muốn (`"1080p"`). |

#### 📤 Định dạng phản hồi (Response)
*   **Mã phản hồi thành công**: `200 OK`
*   **Content-Type**: `video/mp4`
*   **Headers**:
    ```http
    Content-Disposition: attachment; filename="clean_<original_filename>.mp4"
    ```
*   **Body**: Luồng dữ liệu nhị phân (Binary video file stream) chứa video đã xử lý sạch.

#### ❌ Mã lỗi phản hồi (Error Status)
*   **`400 Bad Request`**: Xảy ra khi:
    - Chọn chế độ `"auto"` nhưng không tự động nhận diện được mẫu watermark nào.
    - Cung cấp mã `profile` thủ công không có trong cấu hình hệ thống.
    - Định dạng lỗi: `{"detail": "No matching watermark profile found..."}`
*   **`500 Internal Server Error`**: Quá trình chạy inference mô hình ProPainter hoặc Real-ESRGAN gặp lỗi phần cứng hoặc tệp video hỏng.
    - Định dạng lỗi: `{"detail": "Watermark removal processing failed."}`

---

## 💻 3. Ví Dụ Gọi API Cho Lập Trình Viên (Client SDK Examples)

### 3.1 Sử dụng cURL (Terminal / Command Line)
```bash
curl -X POST "http://127.0.0.1:8080/process" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@input_video.mp4" \
  -F "profile=auto" \
  -F "upscale=true" \
  -F "upscale_model=realesr-animevideov3" \
  --output processed_clean.mp4
```

### 3.2 Sử dụng Python (Thư viện `requests`)
```python
import requests

url = "http://127.0.0.1:8080/process"
payload = {
    "profile": "gemini_omni_format",  # Tên profile cụ thể
    "upscale": "true",                # Truyền dạng chuỗi trong form-data
    "upscale_model": "realesr-animevideov3",
    "target_resolution": "1080p"
}

# Đọc file nhị phân
files = [
    ('file', ('video.mp4', open('input_video1.mp4', 'rb'), 'video/mp4'))
]

print("Đang gửi video đến API xử lý...")
response = requests.post(url, data=payload, files=files, stream=True)

if response.status_code == 200:
    with open("output_clean.mp4", "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    print("Xử lý thành công! Video đã được lưu tại output_clean.mp4")
else:
    print(f"Lỗi xử lý (Mã lỗi: {response.status_code}):")
    print(response.json())
```

### 3.3 Sử dụng JavaScript (Fetch API trên trình duyệt)
```javascript
const fileInput = document.querySelector('#fileInput'); // input file element
const formData = new FormData();
formData.append('file', fileInput.files[0]);
formData.append('profile', 'auto');
formData.append('upscale', true);
formData.append('upscale_model', 'realesr-animevideov3');

fetch('http://127.0.0.1:8080/process', {
    method: 'POST',
    body: formData
})
.then(response => {
    if (!response.ok) throw new Error('Xử lý video thất bại');
    return response.blob();
})
.then(blob => {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = "clean_video.mp4";
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
})
.catch(error => console.error('Lỗi kết nối:', error));
```

---

## 🤖 4. Cấu Hình OpenAPI 3.0 JSON Schema (Dành cho AI Bot)
Sao chép mã JSON dưới đây để nạp vào các hệ thống Agent/AI Bot để chúng tự động sinh Client và hiểu được cấu trúc dữ liệu:

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "Video Process API",
    "description": "Local HTTP API for automated ProPainter dewatermarking and Real-ESRGAN upscaling.",
    "version": "1.0.0"
  },
  "paths": {
    "/": {
      "get": {
        "summary": "Get Web Ui",
        "operationId": "get_web_ui__get",
        "responses": {
          "200": {
            "description": "Successful Response",
            "content": {
              "text/html": {
                "schema": {}
              }
            }
          }
        }
      }
    },
    "/process": {
      "post": {
        "summary": "Process Video Endpoint",
        "operationId": "process_video_endpoint_process_post",
        "requestBody": {
          "content": {
            "multipart/form-data": {
              "schema": {
                "properties": {
                  "file": {
                    "title": "File",
                    "type": "string",
                    "format": "binary",
                    "description": "The raw video file to process"
                  },
                  "profile": {
                    "title": "Profile",
                    "type": "string",
                    "enum": ["auto", "dola_ai_bottom_right", "veo_bottom_right_format", "gemini_omni_format"],
                    "default": "auto",
                    "description": "Watermark layout template code"
                  },
                  "upscale": {
                    "title": "Upscale",
                    "type": "boolean",
                    "default": false,
                    "description": "Enable super-resolution upscale to 1080p"
                  },
                  "upscale_model": {
                    "title": "Upscale Model",
                    "type": "string",
                    "enum": ["realesr-animevideov3", "RealESRGAN_x4plus_anime_6B"],
                    "default": "realesr-animevideov3",
                    "description": "Model weights to use for upscaling"
                  },
                  "target_resolution": {
                    "title": "Target Resolution",
                    "type": "string",
                    "default": "1080p",
                    "description": "Target resolution limit"
                  }
                },
                "required": ["file"]
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "Successful Response",
            "content": {
              "video/mp4": {
                "schema": {
                  "type": "string",
                  "format": "binary"
                }
              }
            }
          },
          "400": {
            "description": "Bad Request - Auto-detect failed or profile invalid"
          },
          "500": {
            "description": "Internal Server Error - Inference execution failed"
          }
        }
      }
    }
  }
}
```
