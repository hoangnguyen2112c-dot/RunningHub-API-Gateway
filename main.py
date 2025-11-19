import os
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# ==========================================
#        CẤU HÌNH SERVER (LẤY TỪ RENDER)
# ==========================================

# 1. API Key RunningHub của Admin (Bạn)
RUNNINGHUB_API_KEY = os.environ.get("RUNNINGHUB_API_KEY")

# 2. Link Google Apps Script (Database quản lý User)
USER_DB_URL = os.environ.get("USER_DB_URL")

# 3. Cấu hình Workflow ID bí mật (CHỈ DÙNG ĐỂ THAM KHẢO, LOGIC TẠO TASK MỚI KHÔNG DÙNG PRESET_CONFIGS)
PRESET_CONFIGS = {
    "Normal (24G)": {
        "id": "1984294242724036609", 
        "prompt_id": "416", 
        "image_id": "284", 
        "strength_id": "134", 
        "show_strength": True
    },
    # "Vip (24G)" đã bị loại bỏ theo yêu cầu
    "Upscale (24G)": {
        "id": "1981382064639492097", 
        "prompt_id": "45", 
        "image_id": "59", 
        "strength_id": "", 
        "show_strength": False
    },
}

RUNNINGHUB_URLS = {
    "create": "https://www.runninghub.cn/task/openapi/create",
    "ai_app_run": "https://www.runninghub.ai/task/openapi/ai-app/run",
    "status": "https://www.runninghub.ai/task/openapi/status",
    "outputs": "https://www.runninghub.ai/task/openapi/outputs",
    "upload": "https://www.runninghub.ai/task/openapi/upload",
}

# ==========================================
#              KHỞI TẠO APP
# ==========================================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Client HTTP dùng chung
client = httpx.AsyncClient(timeout=60.0, verify=False, follow_redirects=True)

# --- CÁC MODEL DỮ LIỆU ---

class LoginRequest(BaseModel):
    username: str
    password: str

# ĐÃ SỬA: Thay preset_name bằng các ID để tự thêm ID workflow khác
class CreateTaskRequest(BaseModel):
    username: str
    password: str
    workflow_id: str  # ID chính của workflow
    prompt_id: str  # ID node cho Prompt
    image_id: str  # ID node cho Ảnh Input
    strength_id: Optional[str] = None  # ID node cho Strength (nếu có)
    
    gpu_mode: str
    prompt_text: str
    img_path: str
    strength: Optional[float] = None

# ==========================================
#             HÀM HỖ TRỢ (LOGIC)
# ==========================================

async def check_and_deduct(username, password, action="login"):
    """Gọi Google Apps Script để check user và trừ tiền"""
    if not USER_DB_URL:
        raise HTTPException(500, "Server chưa cấu hình USER_DB_URL (Google Script).")
    
    try:
        # Gọi sang Google Sheet
        res = await client.post(
            USER_DB_URL, 
            json={"action": action, "username": username, "password": password}
        )
        data = res.json()
        
        # Xử lý kết quả từ Google Sheet
        if not data.get("success"):
            # Trả về lỗi 401 nếu sai pass
            raise HTTPException(401, data.get("message", "Lỗi xác thực"))
            
        # Nếu là login mà hết tiền -> Báo lỗi
        if action == "login" and data.get("credits", 0) <= 0:
             raise HTTPException(402, "Tài khoản đã hết lượt chạy! Vui lòng liên hệ Admin.")
             
        return data
        
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(500, f"Lỗi kết nối Database: {str(e)}")

# ==========================================
#              API ENDPOINTS
# ==========================================

@app.post("/api/v1/login")
async def login_endpoint(req: LoginRequest):
    """API Đăng nhập & Kiểm tra số dư"""
    # Trả về data (có credits) nếu thành công
    return await check_and_deduct(req.username, req.password, "login")

@app.post("/api/v1/workflow/create")
async def create_task(req: CreateTaskRequest):
    """API Tạo Task (Có trừ tiền) - Đã sửa để dùng ID Workflow trực tiếp và loại bỏ máy Plus"""
    
    # BƯỚC 1: Kiểm tra đăng nhập & Số dư TRƯỚC khi chạy (Sẽ chặn nếu credits <= 0)
    await check_and_deduct(req.username, req.password, "login")

    # BƯỚC 2: Xây dựng Node Info (Mapping tham số vào đúng Node ID)
    node_info = []
    
    # Prompt (Text)
    if req.prompt_text and req.prompt_id:
        node_info.append({
            "nodeId": req.prompt_id, 
            "fieldName": "text", 
            "fieldValue": req.prompt_text
        })
        
    # Strength (Nếu có)
    if req.strength is not None and req.strength_id:
        node_info.append({
            "nodeId": req.strength_id, 
            "fieldName": "guidance", 
            "fieldValue": req.strength
        })
        
    # Ảnh Input (Đã upload)
    if req.img_path and req.image_id:
        node_info.append({
            "nodeId": req.image_id, 
            "fieldName": "image", 
            "fieldValue": req.img_path
        })

    payload = {
        "workflowId": req.workflow_id, # Dùng ID Workflow trực tiếp
        "nodeInfoList": node_info
    }

    # Xử lý chọn máy (GPU Mode)
    # ĐÃ BỎ LOGIC chọn máy "Plus (48G)"
    
    # BƯỚC 3: Gọi RunningHub (Dùng Key Admin của BẠN)
    if not RUNNINGHUB_API_KEY:
        raise HTTPException(500, "Server chưa có API Key RunningHub.")

    full_payload = {**payload, "apiKey": RUNNINGHUB_API_KEY}
    
    try:
        rh_res = await client.post(RUNNINGHUB_URLS["create"], json=full_payload)
        rh_data = rh_res.json()
        
        # Nếu RunningHub báo lỗi
        if rh_data.get("code") != 0:
            raise Exception(rh_data.get("msg"))
        
        # BƯỚC 4: Chạy thành công -> Gọi Google Sheet để TRỪ TIỀN
        await check_and_deduct(req.username, req.password, "deduct")
        
        # Trả về Task ID cho khách
        return {"taskId": rh_data.get("data", {}).get("taskId")}
        
    except Exception as e:
        raise HTTPException(500, f"Lỗi RunningHub: {str(e)}")

# --- API UPLOAD ---
from fastapi import UploadFile, File
@app.post("/api/v1/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    API Upload file chuẩn (Sử dụng Multipart/Form-data)
    """
    if not RUNNINGHUB_API_KEY:
        raise HTTPException(500, "Server chưa có API Key.")
        
    try:
        file_content = await file.read()
        filename = file.filename
        
        files = {'file': (filename, file_content, file.content_type or 'image/png')} 
        data = {'apiKey': RUNNINGHUB_API_KEY, 'fileType': 'image'}
        
        res = await client.post(RUNNINGHUB_URLS["upload"], files=files, data=data)
        res_data = res.json()
        
        if res_data.get("code") != 0:
             raise HTTPException(400, f"Upload lỗi từ RunningHub: {res_data.get('msg')}")
             
        return res_data.get("data")
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(500, f"Lỗi Upload Server: {str(e)}")

# --- CÁC API TRA CỨU (PROXY) ---

@app.get("/api/v1/task/status/{task_id}")
async def get_status(task_id: str):
    """Kiểm tra trạng thái Task"""
    res = await client.post(
        RUNNINGHUB_URLS["status"], 
        json={"taskId": task_id, "apiKey": RUNNINGHUB_API_KEY}
    )
    return res.json()

@app.get("/api/v1/task/outputs/{task_id}")
async def get_outputs(task_id: str):
    """Lấy kết quả Task"""
    res = await client.post(
        RUNNINGHUB_URLS["outputs"], 
        json={"taskId": task_id, "apiKey": RUNNINGHUB_API_KEY}
    )
    return res.json()

@app.on_event("shutdown")
async def app_shutdown():
    await client.aclose()
